"""投稿在庫（ランウェイ）の算出（純関数・AI不使用・読み取り専用）。

背景（2026-07-28）: 生成が止まって在庫がゼロになっても、投稿ワークフローは
「公開対象0件」で成功のまま緑になり続けるため、4アカウント全ての停止が9〜19日間
気づかれなかった。監視の対象を「ジョブが落ちたか」から「出せる在庫があるか」へ
移すための計算をここに置く（週次レポートのバッジと、日次の在庫監視で共用する）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

# publisher が実際に公開する status（sheets の運用ルールと一致させること）。
# 空 or queued のみ公開対象。draft/retired/posted/error/publishing は対象外。
PUBLISHABLE_STATUS = {"", "queued"}

_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_dt(s) -> datetime | None:
    """シートの日時文字列（JST・naive）を datetime に。読めなければ None。"""
    s = str(s or "").strip().replace("/", "-")
    if not s:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def compute_runway(posts: list[dict], account: str, *, now: datetime,
                   posts_per_day: int = 4) -> dict:
    """1アカウントの在庫状況を返す。

    posts は store.get_posts() の結果（全アカウント混在可）。now は naive JST。

    返り値:
      pending        … これから公開される本数（未来の 空/queued）
      overdue        … 公開時刻を過ぎているのに未公開の本数（cron停止・トークン失効の痕跡）
      next_at        … 次に公開される日時（無ければ None）
      last_at        … 在庫の最終日時（無ければ None）＝いつまで投稿が続くか
      days_left      … 在庫が尽きるまでの残り日数（last_at の日付 − 今日）
      last_posted_at … 直近で実際に公開された日時（無ければ None）
      silent_days    … 最後の公開からの経過日数（無投稿日数）。公開実績が無ければ None
      posts_per_day  … 想定本数（呼び出し側の設定をそのまま返す・表示用）
    """
    mine = [p for p in posts if str(p.get("account", account)) == str(account)]

    pending, overdue = [], []
    posted_dts = []
    for p in mine:
        status = str(p.get("status") or "").strip().lower()
        dt = parse_dt(p.get("post_datetime"))
        if status == "posted":
            # 実施日時があればそれを優先（予定と実績がずれることがある）
            posted_dts.append(parse_dt(p.get("posted_at")) or dt)
            continue
        if status not in PUBLISHABLE_STATUS:
            continue  # draft / retired / error / publishing は公開対象外
        if dt is None:
            continue  # 日時が空/不正な行は publisher も公開しない
        (pending if dt > now else overdue).append(dt)

    posted_dts = [d for d in posted_dts if d]
    last_posted_at = max(posted_dts) if posted_dts else None
    last_at = max(pending) if pending else None
    days_left = max(0, (last_at.date() - now.date()).days) if last_at else 0
    silent_days = (now.date() - last_posted_at.date()).days if last_posted_at else None

    return {
        "account": account,
        "pending": len(pending),
        "overdue": len(overdue),
        "next_at": min(pending) if pending else None,
        "last_at": last_at,
        "days_left": days_left,
        "last_posted_at": last_posted_at,
        "silent_days": silent_days,
        "posts_per_day": posts_per_day,
    }


def runway_severity(r: dict, *, warn_days: int = 2) -> str:
    """在庫状況の深刻度。'critical'（在庫ゼロ）/ 'warning'（残り僅か・滞留あり）/ 'ok'。"""
    if r["pending"] == 0:
        return "critical"
    if r["days_left"] <= warn_days or r["overdue"] > 0:
        return "warning"
    return "ok"


def runway_message(r: dict) -> str:
    """人が読む1行サマリ（メール件名・ログ・アラート本文で共用）。"""
    if r["pending"] == 0:
        silent = f"・最終投稿から{r['silent_days']}日" if r["silent_days"] is not None else ""
        return f"{r['account']}: 在庫ゼロ（このままでは投稿が出ません{silent}）"
    tail = f"・{r['last_at']:%m-%d %H:%M}まで" if r["last_at"] else ""
    over = f"・時刻超過の未公開{r['overdue']}本" if r["overdue"] else ""
    return f"{r['account']}: 在庫{r['pending']}本（残り{r['days_left']}日{tail}）{over}"


def summarize(posts: list[dict], accounts: list[str], *, now: datetime,
              posts_per_day: int = 4) -> list[dict]:
    """複数アカウントぶんの在庫状況をまとめて返す（深刻な順）。"""
    out = [compute_runway(posts, a, now=now, posts_per_day=posts_per_day) for a in accounts]
    order = {"critical": 0, "warning": 1, "ok": 2}
    for r in out:
        r["severity"] = runway_severity(r)
        r["message"] = runway_message(r)
    return sorted(out, key=lambda r: (order[r["severity"]], r["days_left"]))
