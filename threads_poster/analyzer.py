"""インサイト分析（純コード・AI不使用）。

`インサイト_<acc>` の最新スナップショットから、時間帯/曜日/本文長/ツリー有無 別の
平均エンゲージ率・平均表示を集計し、`インサイト分析_<acc>` タブへ出力する。
集計ロジックは純関数 `analyze_insights()` にして単体テスト可能にしている。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("analyzer")

TIME_BANDS = [("深夜(0-5)", 0, 5), ("朝(6-11)", 6, 11), ("昼(12-17)", 12, 17), ("夜(18-23)", 18, 23)]
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
LEN_BUCKETS = [("〜99字", 0, 99), ("100-199字", 100, 199), ("200-399字", 200, 399), ("400字〜", 400, 10 ** 9)]

ANALYSIS_HEADER = ["分析軸", "区分", "投稿数", "平均表示", "平均エンゲージ率"]


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _has_er(r) -> bool:
    """エンゲージ率が「入っている」か。0.0 は有効値なので落とさない（None/空文字だけ欠落扱い）。
    ※ `x or ""` 方式だと数値0.0が falsy で欠落扱いになり avg/ランキングが歪むため使わない。"""
    v = r.get("engagement_rate")
    return v is not None and str(v).strip() != ""


def _parse_dt(s):
    s = str(s or "").strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _latest_per_post(rows):
    """posted_id ごとに snapshot_date が最大の行を残す（日次スナップショットの最新を採用）。"""
    best = {}
    for r in rows:
        pid = str(r.get("posted_id") or "")
        if not pid:
            continue
        d = str(r.get("snapshot_date") or "")
        if pid not in best or d >= best[pid][0]:
            best[pid] = (d, r)
    return [v[1] for v in best.values()]


def _agg(items):
    """(投稿数, 平均表示, 平均エンゲージ率)。エンゲージ率は数値が入っている投稿だけで平均。"""
    n = len(items)
    if n == 0:
        return (0, "", "")
    avg_views = round(sum(_i(r.get("views")) for r in items) / n, 1)
    ers = [_f(r.get("engagement_rate")) for r in items if _has_er(r)]
    avg_er = round(sum(ers) / len(ers), 4) if ers else ""
    return (n, avg_views, avg_er)


def _reactions(r) -> int:
    """1投稿の合計反応数＝いいね＋返信＋リポスト＋引用。"""
    return sum(_i(r.get(k)) for k in ("likes", "replies", "reposts", "quotes"))


def analyze_insights(rows: list[dict]) -> dict:
    posts = _latest_per_post(rows)
    for r in posts:
        r["_dt"] = _parse_dt(r.get("post_datetime"))
    out = {"n_posts": len(posts), "by_time": [], "by_weekday": [], "by_length": [], "by_tree": [],
           "top": [], "top_er": [], "total_views": 0, "total_reactions": 0, "avg_er": ""}

    # KPI 合計（レポートのサマリカード用）
    out["total_views"] = sum(_i(r.get("views")) for r in posts)
    out["total_reactions"] = sum(_reactions(r) for r in posts)
    ers_all = [_f(r.get("engagement_rate")) for r in posts if _has_er(r)]
    out["avg_er"] = round(sum(ers_all) / len(ers_all), 4) if ers_all else ""

    for label, lo, hi in TIME_BANDS:
        items = [r for r in posts if r["_dt"] and lo <= r["_dt"].hour <= hi]
        out["by_time"].append((label, *_agg(items)))
    for wd in range(7):
        items = [r for r in posts if r["_dt"] and r["_dt"].weekday() == wd]
        out["by_weekday"].append((WEEKDAYS[wd], *_agg(items)))
    for label, lo, hi in LEN_BUCKETS:
        items = [r for r in posts if lo <= _i(r.get("text_len")) <= hi]
        out["by_length"].append((label, *_agg(items)))
    for label, is_tree in [("ツリー", True), ("単発", False)]:
        items = [r for r in posts if (str(r.get("is_tree") or "").strip() != "") == is_tree]
        out["by_tree"].append((label, *_agg(items)))

    def _entry(r):
        return {
            "posted_id": r.get("posted_id"), "post_datetime": r.get("post_datetime"),
            "views": _i(r.get("views")), "engagement_rate": r.get("engagement_rate"),
            "likes": _i(r.get("likes")), "replies": _i(r.get("replies")),
            "reposts": _i(r.get("reposts")), "quotes": _i(r.get("quotes")),
            "reactions": _reactions(r), "text_len": _i(r.get("text_len")),
            "permalink": r.get("permalink"), "is_tree": r.get("is_tree"),
        }

    # 表示数ランキング（リーチ）
    top = sorted(posts, key=lambda r: _i(r.get("views")), reverse=True)[:5]
    out["top"] = [_entry(r) for r in top]
    # エンゲージ率ランキング（質）。ERが入っている投稿のみを対象に降順。
    er_posts = [r for r in posts if _has_er(r)]
    top_er = sorted(er_posts, key=lambda r: _f(r.get("engagement_rate")), reverse=True)[:5]
    out["top_er"] = [_entry(r) for r in top_er]
    return out


def analysis_to_rows(a: dict) -> list[list]:
    rows = []
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"),
                      ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        for (label, n, av, er) in a[key]:
            rows.append([axis, label, n, av, er])
    return rows


class Analyzer:
    def __init__(self, store, now_fn=None):
        self.store = store

    def run(self, account: str) -> dict:
        rows = self.store.get_insights(account)
        a = analyze_insights(rows)
        self.store.write_analysis(account, ANALYSIS_HEADER, analysis_to_rows(a))
        logger.info("分析完了 %s: %d投稿", account, a["n_posts"])
        return a
