"""インサイト収集オーケストレータ（読み取り専用・Publisher 対称）。

日次で各アカウントの「投稿別インサイト」と「アカウント全体指標」を取得し、
スプレッドシートへ**日次スナップショット**として冪等 upsert する。投稿系（publisher）には一切触れない。

Publisher と同じ注入（store / client_factory / now_fn / dry_run）でテスト容易。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .threads_api import ThreadsClient, ThreadsAPIError
from .sheets import Store

logger = logging.getLogger("collector")

# エンゲージ率の分子（表示回数で割る）。views は分母なので含めない。
ENGAGE_METRICS = ("likes", "replies", "reposts", "quotes")


def _parse_api_ts(ts, tz: ZoneInfo):
    """Threads API の timestamp（ISO8601・例 2024-06-01T12:00:00+0000）を tz 付き datetime に。失敗時 None。"""
    if not ts:
        return None
    s = str(ts).strip()
    # "+0000" を "+00:00" に正規化（fromisoformat 用）。"Z" 終端や既に ":" 付きはそのまま。
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz)


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


class Collector:
    def __init__(self, store: Store, tz_name: str = "Asia/Tokyo",
                 client_factory=ThreadsClient, now_fn=None, dry_run: bool = False,
                 lookback_days: int = 60, media_metrics=None, user_metrics=None):
        self.store = store
        self.tz = ZoneInfo(tz_name)
        self.client_factory = client_factory
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.dry_run = dry_run
        self.lookback_days = lookback_days
        self.media_metrics = list(media_metrics) if media_metrics else \
            ["views", "likes", "replies", "reposts", "quotes", "shares"]
        # アカウント全体の views は since/until 必須の可能性があり初回で全体失敗を招きうるので
        # 既定から外す（累積 total_value 型の安定指標のみ）。実機確認後に env/引数で追加可。
        self.user_metrics = list(user_metrics) if user_metrics else \
            ["likes", "replies", "reposts", "quotes", "followers_count"]

    def run(self) -> dict:
        now = self.now_fn()
        snap = now.strftime("%Y-%m-%d")
        collected_at = now.strftime("%Y-%m-%d %H:%M:%S")
        cutoff = now - timedelta(days=self.lookback_days)
        results = {"accounts": 0, "media": 0, "errors": 0, "skipped_old": 0}

        # 投稿タブから posted_id -> 投稿行 の対応を作る（row_id / ツリー有無 の best-effort 結合）。
        posts_by_pid: dict[str, dict] = {}
        try:
            for p in self.store.get_posts():
                pid = str(p.get("posted_id") or "").strip()
                if pid:
                    posts_by_pid[pid] = p
        except Exception as e:  # noqa: BLE001  結合は任意。失敗しても収集は続行。
            logger.warning("投稿タブ読込に失敗（結合スキップ）: %s", e)

        for acc in self.store.get_accounts():
            account = acc.get("account")
            token = acc.get("access_token")
            user_id = acc.get("user_id")
            if not account or not token or not user_id:
                logger.warning("アカウント情報不足のためスキップ: %r", account)
                continue
            client = self.client_factory(user_id=user_id, access_token=token)

            # 1) アカウント全体指標（日次1行）
            try:
                ui = client.get_user_insights(metrics=self.user_metrics)
                fields = {m: ui.get(m) for m in self.user_metrics}
                fields["collected_at"] = collected_at
                if self.dry_run:
                    logger.info("[dry-run] アカウント指標 %s: %s", account, fields)
                else:
                    self.store.upsert_account_metric(account, snap, fields)
                results["accounts"] += 1
            except ThreadsAPIError as e:
                results["errors"] += 1
                logger.error("%s アカウント指標の取得失敗: %s", account, e)

            # 2) 投稿別インサイト（lookback 内の各投稿を日次スナップショット）
            try:
                media = client.list_media()
            except ThreadsAPIError as e:
                results["errors"] += 1
                logger.error("%s 投稿一覧の取得失敗: %s", account, e)
                continue
            for m in media:
                mid = str(m.get("id") or "").strip()
                if not mid:
                    continue
                ts = _parse_api_ts(m.get("timestamp"), self.tz)
                if ts is not None and ts < cutoff:
                    results["skipped_old"] += 1
                    continue
                try:
                    ins = client.get_media_insights(mid, metrics=self.media_metrics)
                except ThreadsAPIError as e:  # 1件の失敗で全体は止めない（PRD要件）
                    results["errors"] += 1
                    logger.warning("media=%s のインサイト取得失敗・スキップ: %s", mid, e)
                    continue
                views = _to_int(ins.get("views"))
                engage = sum(_to_int(ins.get(k)) for k in ENGAGE_METRICS)
                er = round(engage / views, 4) if views else ""  # 表示0/欠落時は空（誤誘導を避ける）
                p = posts_by_pid.get(mid, {})
                fields = {
                    "row_id": p.get("row_id", ""),
                    "permalink": m.get("permalink", ""),
                    "post_datetime": ts.strftime("%Y-%m-%d %H:%M") if ts else (m.get("timestamp") or ""),
                    "media_type": m.get("media_type", ""),
                    "text_len": len(m.get("text") or ""),
                    "is_tree": "ツリー" if str(p.get("reply_to") or "").strip() else "",
                    "views": ins.get("views", ""),
                    "likes": ins.get("likes", ""),
                    "replies": ins.get("replies", ""),
                    "reposts": ins.get("reposts", ""),
                    "quotes": ins.get("quotes", ""),
                    "shares": ins.get("shares", ""),
                    "engagement_rate": er,
                    "collected_at": collected_at,
                }
                if self.dry_run:
                    logger.info("[dry-run] インサイト %s media=%s views=%s engage=%s", account, mid, views, engage)
                else:
                    self.store.upsert_insight(account, mid, snap, fields)
                results["media"] += 1

        logger.info("収集結果: %s", results)
        return results
