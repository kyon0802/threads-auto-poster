"""全投稿データのエクスポート用に行を整える（純関数・AI不使用・読み取り専用）。

目的: 全アカウント・全期間の投稿を「1投稿1行」に揃え、人がスプレッドシートで
アカウント別・月別に絞り込める形にする。集計そのものは analyzer/vendor が持つので、
ここは**並べ替えと補完だけ**を担当する。

2つの落とし穴をここで吸収する:
  1. インサイトは posted_id × 取得日 で毎日1行積まれる。素直に出すと1投稿が日数ぶんに
     膨らみ、投稿本数を大きく誤らせる → 最新スナップショットだけを採る。
  2. 本文列は 2026-09-11 に追加したため、それ以前に収集を終えた古い投稿は本文が空。
     自社アカはこちらが書いた原稿が投稿タブに残っているので、そこから補完する。
"""
from __future__ import annotations

from datetime import date, datetime

WEEKDAYS = "月火水木金土日"

_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

# エクスポートの列順（スプレッドシートの見出しと1対1）。
COLUMNS = [
    ("月", "month"), ("投稿日時", "post_datetime"), ("曜日", "weekday"), ("時", "hour"),
    ("本文", "text"), ("文字数", "text_len"),
    ("表示", "views"), ("いいね", "likes"), ("返信", "replies"),
    ("リポスト", "reposts"), ("引用", "quotes"), ("シェア", "shares"),
    ("本文の取得元", "text_source"), ("投稿ID", "row_id"),
    ("フック型", "hook_type"), ("内容型", "content_type"), ("リンク", "permalink"),
]


def _parse_dt(s) -> datetime | None:
    s = str(s or "").strip()
    if not s:
        return None
    for f in _FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _to_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def latest_per_post(rows: list[dict]) -> list[dict]:
    """日次スナップショットを投稿単位に畳む（最新の取得日を採用）。"""
    best: dict[str, dict] = {}
    for r in rows:
        pid = str(r.get("posted_id") or "").strip()
        if not pid:
            continue
        cur = best.get(pid)
        if cur is None or str(r.get("snapshot_date") or "") >= str(cur.get("snapshot_date") or ""):
            best[pid] = r
    return list(best.values())


def build_rows(account: str, insight_rows: list[dict], posts_by_id: dict[str, dict]) -> list[dict]:
    """1アカウント分のエクスポート行を返す（投稿日時の昇順）。

    引数:
      insight_rows … インサイトタブの行（日次スナップショットのまま渡してよい）
      posts_by_id  … 投稿後ID → {text, row_id, hook_type, content_type}（投稿タブ由来・本文補完用）
    """
    out = []
    for r in latest_per_post(insight_rows):
        dt = _parse_dt(r.get("post_datetime"))
        if dt is None:
            continue  # 日時が無い行は月別集計を壊すので出さない
        src = posts_by_id.get(str(r.get("posted_id") or ""), {})
        text = str(r.get("text") or "").strip()
        # API で取れている本文が正（実際に投稿された文面）。無いときだけ投稿タブから補う。
        text_source = "API収集" if text else ("投稿タブ" if str(src.get("text") or "").strip() else "")
        if not text:
            text = str(src.get("text") or "")
        out.append({
            "account": account,
            "posted_id": r.get("posted_id", ""),
            "month": dt.strftime("%Y-%m"),
            "post_datetime": dt.strftime("%Y-%m-%d %H:%M"),
            "weekday": WEEKDAYS[dt.weekday()],
            "hour": dt.hour,
            "text": text,
            "text_len": len(text),
            "text_source": text_source,
            "views": _to_int(r.get("views")),
            "likes": _to_int(r.get("likes")),
            "replies": _to_int(r.get("replies")),
            "reposts": _to_int(r.get("reposts")),
            "quotes": _to_int(r.get("quotes")),
            "shares": _to_int(r.get("shares")),
            "row_id": src.get("row_id", ""),
            "hook_type": src.get("hook_type", ""),
            "content_type": src.get("content_type", ""),
            "permalink": r.get("permalink", ""),
        })
    out.sort(key=lambda r: r["post_datetime"])
    return out


def monthly_summary(rows: list[dict]) -> list[dict]:
    """アカウント×月の集計を返す。

    **中央値を主指標にする**。1本の当たり投稿で平均は簡単に跳ねるため、平均だけを見ると
    評価が逆転する（2026-09-06 横断分析設計の必須要件）。平均は乖離を見せるために併記する。
    """
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((r["account"], r["month"]), []).append(r)
    out = []
    for (acc, month), g in sorted(by.items()):
        v = sorted(x["views"] for x in g)
        out.append({
            "account": acc,
            "month": month,
            "posts": len(g),
            "views_total": sum(v),
            "views_median": v[len(v) // 2],
            "views_mean": round(sum(v) / len(v)),
            "zero_views": sum(1 for x in v if x == 0),
            "low_views": sum(1 for x in v if x <= 5),
            "likes": sum(x["likes"] for x in g),
            "replies": sum(x["replies"] for x in g),
            "with_text": sum(1 for x in g if str(x["text"]).strip()),
        })
    return out
