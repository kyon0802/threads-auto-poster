"""
オーケストレーション本体。
GitHub Actions などから定期実行され、毎回:
  1. アカウントごとにトークンを必要に応じてリフレッシュ
  2. 公開時刻が到来した未投稿の行を抽出
  3. ツリー(親→子)の依存を解決しつつ順次公開
  4. 250/24h のレート制限を遵守
  5. 結果(status / posted_id)をスプレッドシートに書き戻す
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .threads_api import ThreadsClient, ThreadsAPIError
from .sheets import Store

logger = logging.getLogger("publisher")

# トークンは「24h以上経過」してから更新可能。安全側で日数経過後に更新。
REFRESH_AFTER_DAYS = 7
# 安全上限。Threads公式は250/24h/ユーザーだが、スパム判定回避のため低めの既定を推奨。
DEFAULT_MAX_POSTS_PER_DAY = 50


def _parse_dt(value, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    s = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    logger.warning("日時パース失敗: %r", value)
    return None


class Publisher:
    def __init__(
        self,
        store: Store,
        tz_name: str = "Asia/Tokyo",
        max_posts_per_day: int = DEFAULT_MAX_POSTS_PER_DAY,
        client_factory=ThreadsClient,
        now_fn=None,
        dry_run: bool = False,
        tree_reply_delay_sec: int = 0,
    ):
        self.store = store
        self.tz = ZoneInfo(tz_name)
        self.max_posts_per_day = max_posts_per_day
        self.client_factory = client_factory
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.dry_run = dry_run
        # 同じ回で親を公開した直後の子（ツリー返信）を待つ秒数。人間らしいテンポにする。
        self.tree_reply_delay_sec = tree_reply_delay_sec

    # ---------- トークン ----------
    def refresh_tokens(self) -> None:
        now = self.now_fn()
        for acc in self.store.get_accounts():
            updated = _parse_dt(acc.get("token_updated_at"), self.tz)
            if updated is None:
                # 手動投入直後の長期トークン。失効していない前提でリフレッシュせず、
                # 次回以降に経過日数を測れるよう token_updated_at を now で初期化する。
                # （リフレッシュは「24h以上経過」が条件なので、投入直後に叩くと必ず失敗する。）
                if self.dry_run:
                    logger.info("[dry-run] %s: token_updated_at を now で初期化", acc["account"])
                    continue
                self.store.update_account(
                    acc["account"], {"token_updated_at": now.strftime("%Y-%m-%d %H:%M:%S")}
                )
                continue
            if (now - updated) <= timedelta(days=REFRESH_AFTER_DAYS):
                continue  # まだ更新不要（7日以内）
            if self.dry_run:
                logger.info("[dry-run] %s のトークンをリフレッシュ", acc["account"])
                continue
            try:
                data = ThreadsClient.refresh_long_lived_token(acc["access_token"])
                self.store.update_account(
                    acc["account"],
                    {"access_token": data["access_token"],
                     "token_updated_at": now.strftime("%Y-%m-%d %H:%M:%S")},
                )
                logger.info("%s のトークンを更新 (expires_in=%s)", acc["account"], data.get("expires_in"))
            except ThreadsAPIError as e:
                logger.error("%s トークン更新失敗: %s", acc["account"], e)

    # ---------- レート制限 ----------
    def _daily_count(self, acc: dict, today: str) -> int:
        if str(acc.get("daily_count_date")) == today:
            try:
                return int(acc.get("daily_count") or 0)
            except ValueError:
                return 0
        return 0  # 日付が変わっていればリセット

    @staticmethod
    def _order_parents_first(due_list: list[dict]) -> list[dict]:
        """時刻順を保ちつつ、ツリーの親を必ず子より前に並べ替える（行順・同時刻に依存しない）。"""
        by_id = {str(p["row_id"]): p for p in due_list}
        ordered: list[dict] = []
        visited: set[str] = set()

        def emit(p: dict, chain: set) -> None:
            rid = str(p["row_id"])
            if rid in visited or rid in chain:
                return
            parent = str(p.get("reply_to") or "").strip()
            if parent and parent in by_id:
                emit(by_id[parent], chain | {rid})  # 親を先に
            if rid not in visited:
                visited.add(rid)
                ordered.append(p)

        for p in due_list:
            emit(p, set())
        return ordered

    # ---------- メイン ----------
    def run(self) -> dict:
        now = self.now_fn()
        today = now.strftime("%Y-%m-%d")
        self.refresh_tokens()

        accounts = {a["account"]: a for a in self.store.get_accounts()}
        posts = self.store.get_posts()

        # row_id -> posted_id の既知マップ（ツリー親解決用）。
        # 投稿IDが数値だけ（例 1, 2）でもツリーが壊れないよう、キーは常に str に正規化する。
        posted_ids = {
            str(p["row_id"]): p.get("posted_id")
            for p in posts
            if str(p.get("status")) == "posted" and p.get("posted_id")
        }
        # アカウント別の本日カウント
        counts = {name: self._daily_count(acc, today) for name, acc in accounts.items()}

        # 公開対象: status空/queued かつ 公開時刻到来。時刻昇順で処理（親が先に来る前提＋保険でリトライ）。
        def is_due(p) -> bool:
            status = str(p.get("status") or "").lower()
            if status not in ("", "queued"):
                return False
            dt = _parse_dt(p.get("post_datetime"), self.tz)
            if dt is None or dt > now:
                return False
            # ここまで来た＝公開時刻が到来した未投稿の行。row_id が無ければ公開しない。
            # row_id は書き戻し（status/posted_id）と冪等性のキーで、空のまま公開すると
            # 書き戻しが正しい行に着地せず二重投稿の原因になる。
            # （未記入の空行は上の時刻判定で先に除外されるので、ここで警告が乱発しない。）
            if not str(p.get("row_id") or "").strip():
                logger.warning("公開時刻が到来した行に投稿IDが無いため公開しません（冪等性保護）: text=%r",
                               (p.get("text") or "")[:30])
                return False
            return True

        due = self._order_parents_first(
            sorted(
                [p for p in posts if is_due(p)],
                key=lambda p: _parse_dt(p.get("post_datetime"), self.tz),
            )
        )

        results = {"posted": 0, "skipped": 0, "error": 0, "deferred": 0}
        posted_this_run: set[str] = set()  # この回で公開した投稿ID（ツリー間隔判定用）

        for p in due:
            row_id = str(p["row_id"])  # 数値IDでも str に統一（ツリー連結・書き戻しの一致用）
            account = p["account"]
            acc = accounts.get(account)
            if not acc:
                self._mark_error(row_id, f"未登録アカウント: {account}", account=account)
                results["error"] += 1
                continue

            # レート制限
            if counts.get(account, 0) >= self.max_posts_per_day:
                logger.info("%s は本日上限 (%d) に達したためスキップ", account, self.max_posts_per_day)
                results["skipped"] += 1
                continue

            # ツリー: 親が未公開ならこの回は保留（次回リトライ）
            reply_to_id = None
            parent_row = str(p.get("reply_to") or "").strip()
            if parent_row:
                parent_posted = posted_ids.get(parent_row)
                if not parent_posted:
                    logger.info("row %s: 親 %s が未公開のため保留", row_id, parent_row)
                    results["deferred"] += 1
                    continue
                reply_to_id = parent_posted

            # ツリー返信を人間らしいテンポで：同じ回で親を公開した直後の子は少し間を空ける。
            if reply_to_id and parent_row in posted_this_run and self.tree_reply_delay_sec > 0:
                if self.dry_run:
                    logger.info("[dry-run] ツリー間隔 %d秒を空ける（row=%s）", self.tree_reply_delay_sec, row_id)
                else:
                    time.sleep(self.tree_reply_delay_sec)

            media_type = (p.get("media_type") or "TEXT").upper()
            media_urls = self._split_media_urls(p.get("media_url")) if media_type == "CAROUSEL" else None
            try:
                if self.dry_run:
                    new_id = f"DRYRUN-{row_id}"
                    logger.info("[dry-run] 公開 row=%s account=%s reply_to=%s text=%r",
                                row_id, account, reply_to_id, (p.get("text") or "")[:40])
                else:
                    # 二重投稿防止(write-ahead): API実行前にシート上で行を publishing で確保する。
                    # 公開後に必ず posted へ更新する。途中でプロセスが落ちた場合は publishing が残り、
                    # is_due の対象外になるため再投稿されない（復旧は手動で status を空に戻す）。
                    self.store.update_post(row_id, {"status": "publishing"}, account=account)
                    client = self.client_factory(user_id=acc["user_id"], access_token=acc["access_token"])
                    new_id = client.post(
                        text=p.get("text") or None,
                        media_type=media_type,
                        image_url=p.get("media_url") or None,
                        video_url=p.get("media_url") or None,
                        media_urls=media_urls,
                        reply_to_id=reply_to_id,
                        reply_control=p.get("reply_control") or None,
                    )
            except ThreadsAPIError as e:
                self._mark_error(row_id, str(e), account=account)
                results["error"] += 1
                logger.error("公開失敗 row=%s: %s", row_id, e)
                continue

            new_count = counts.get(account, 0) + 1
            # DRY_RUN はシートを一切書き換えない（対象検出と疎通確認のみ）。
            if not self.dry_run:
                self.store.update_post(row_id, {
                    "status": "posted",
                    "posted_id": new_id,
                    "posted_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "error": "",
                }, account=account)
                self.store.update_account(account, {
                    "daily_count": new_count,
                    "daily_count_date": today,
                })
            # ツリー親解決とレート計算のためのメモリ更新（dry/real 共通）。
            counts[account] = new_count
            posted_ids[row_id] = new_id
            posted_this_run.add(row_id)
            results["posted"] += 1
            logger.info("公開成功 row=%s -> %s%s", row_id, new_id, " (dry-run)" if self.dry_run else "")

        logger.info("実行結果: %s", results)
        return results

    def _mark_error(self, row_id: str, msg: str, account: str | None = None) -> None:
        self.store.update_post(row_id, {"status": "error", "error": msg[:300]}, account=account)

    @staticmethod
    def _split_media_urls(raw) -> list[str]:
        """CAROUSEL用: media_url のカンマ/改行区切りを URL のリストに分解する。"""
        return [u.strip() for u in re.split(r"[,\n]+", str(raw or "")) if u.strip()]
