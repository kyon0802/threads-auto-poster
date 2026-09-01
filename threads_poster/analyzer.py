"""インサイト分析（純コード・AI不使用）。

`インサイト_<acc>` の最新スナップショットから、時間帯/曜日/本文長/ツリー有無 別の
平均エンゲージ率・平均表示を集計し、`インサイト分析_<acc>` タブへ出力する。
集計ロジックは純関数 `analyze_insights()` にして単体テスト可能にしている。

★期間窓（2026-07-28 追加）
以前は期間フィルタが無く常に**全期間累計**だったため、毎サイクルほぼ同じ数字が出て
（TOP1が6サイクル連続で同じ投稿・同じ「読み解き」が13サイクル連続）、3日PDCAの
Check が機能していなかった。`analyze_windowed()` で3系統に分ける:

  - KPI/ランキング … 直近 `window_days` 日（既定7）＋ 前7日との比較
  - 傾向分析       … 直近 `trend_window_days` 日（既定28）。7日だと曜日あたり1本で
                     サンプル不足になり、助言がノイズに振り回されるため長めに取る
  - 累計           … 参考値として `lifetime` に保持
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("analyzer")

WINDOW_DAYS = 7        # KPI・ランキングの集計窓（「今週」）
TREND_WINDOW_DAYS = 28  # 傾向分析（時間帯/曜日/本文長/形式）の集計窓

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
    # 負けランキング（views昇順ワースト3）。勝ち例だけでなく負け例を生成AIに対比で見せる
    # ためのもの（PDCA設計 2026-09-01）。views=0 の投稿こそ負け筆頭なので除外しない。
    bottom = sorted(posts, key=lambda r: _i(r.get("views")))[:3]
    out["bottom"] = [_entry(r) for r in bottom]
    return out


# ---------------------------------------------------------------- 期間窓つき集計

def _in_date_range(rows: list[dict], start, end) -> list[dict]:
    """投稿日時（post_datetime）が [start, end] の**日付**範囲にある行だけ残す。
    両端を含む（「直近7日」を人の読み方どおり 7日分にするため）。"""
    out = []
    for r in rows:
        dt = _parse_dt(r.get("post_datetime"))
        if dt and start <= dt.date() <= end:
            out.append(r)
    return out


def _ratio(cur, prev):
    """前期比（増減率）。前期が0/欠損なら None（0除算・誤誘導を避ける）。"""
    try:
        cur_f, prev_f = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if prev_f == 0:
        return None
    return round((cur_f - prev_f) / prev_f, 4)


_SUMMARY_KEYS = ("n_posts", "total_views", "total_reactions", "avg_er")


def analyze_windowed(rows: list[dict], *, now: datetime,
                     window_days: int = WINDOW_DAYS,
                     trend_window_days: int = TREND_WINDOW_DAYS) -> dict:
    """「直近N日 / 前N日 / 傾向用の長い窓 / 累計」を1つの分析結果にまとめる。

    返り値の**上位キーは直近N日**（`analyze_insights` と同じ形）なので、
    reporter / html_report は従来どおりのキーで読める。加えて:
      prev      … 前N日の要約（n_posts/total_views/total_reactions/avg_er）
      delta     … 前期比（増減率・前期0なら None）
      lifetime  … 全期間の要約（参考値）
      by_*      … 傾向分析（trend_window_days の窓で集計）
      period    … 各窓の開始/終了日（ISO文字列）
    """
    today = now.date()
    cur_start = today - timedelta(days=window_days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window_days - 1)
    trend_start = today - timedelta(days=trend_window_days - 1)

    cur = analyze_insights(_in_date_range(rows, cur_start, today))
    prev = analyze_insights(_in_date_range(rows, prev_start, prev_end))
    trend = analyze_insights(_in_date_range(rows, trend_start, today))
    life = analyze_insights(rows)

    out = dict(cur)  # 上位キー＝「今週」
    # 傾向軸だけは長い窓の結果で置き換える（KPIは7日・傾向は28日）
    for key in ("by_time", "by_weekday", "by_length", "by_tree"):
        out[key] = trend[key]
    out["trend_n_posts"] = trend["n_posts"]
    # 生成プロンプト注入用の勝ち/負け実物（28日窓。7日窓では12本しかなくサンプル不足のため）
    out["trend_top"] = trend["top"]
    out["trend_bottom"] = trend["bottom"]
    out["prev"] = {k: prev[k] for k in _SUMMARY_KEYS}
    out["lifetime"] = {k: life[k] for k in _SUMMARY_KEYS}
    out["delta"] = {k: _ratio(cur[k], prev[k]) for k in _SUMMARY_KEYS}
    out["window_days"] = window_days
    out["trend_window_days"] = trend_window_days
    out["period"] = {
        "cur_start": cur_start.isoformat(), "cur_end": today.isoformat(),
        "prev_start": prev_start.isoformat(), "prev_end": prev_end.isoformat(),
        "trend_start": trend_start.isoformat(),
    }
    out["period_label"] = f"{cur_start:%m-%d}〜{today:%m-%d}"
    out["prev_period_label"] = f"{prev_start:%m-%d}〜{prev_end:%m-%d}"
    out["trend_period_label"] = f"{trend_start:%m-%d}〜{today:%m-%d}"
    return out


def follower_trend(metric_rows: list[dict], *, now: datetime,
                   window_days: int = WINDOW_DAYS) -> dict:
    """`アカウント指標_<acc>` からフォロワー数の現在値と N日前との増減を返す。

    履歴が足りないときは prev/delta を None にする（0 と混同させない）。
    """
    today = now.date()
    cutoff = today - timedelta(days=window_days)
    snaps = []
    for r in metric_rows:
        d = _parse_date(r.get("snapshot_date"))
        n = r.get("followers_count")
        if d is None or str(n).strip() == "":
            continue
        snaps.append((d, _i(n)))
    if not snaps:
        return {"current": None, "prev": None, "delta": None}
    snaps.sort(key=lambda x: x[0])
    current = snaps[-1][1]
    past = [v for d, v in snaps if d <= cutoff]
    prev = past[-1] if past else None
    return {"current": current, "prev": prev,
            "delta": (current - prev) if prev is not None else None}


def _parse_date(s):
    s = str(s or "").strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def analysis_to_rows(a: dict) -> list[list]:
    rows = []
    # 期間窓つきの分析なら、タブ自身が「いつの数字か」を語れるように先頭へ要約行を置く
    # （全期間累計と取り違えられていたのが今回の混乱の一因）。
    if a.get("period_label"):
        rows.append(["期間", f"今週({a['period_label']})", a.get("n_posts", 0),
                     a.get("total_views", 0), a.get("avg_er", "")])
        prev = a.get("prev") or {}
        rows.append(["期間", f"前週({a.get('prev_period_label', '')})", prev.get("n_posts", 0),
                     prev.get("total_views", 0), prev.get("avg_er", "")])
        life = a.get("lifetime") or {}
        rows.append(["期間", "累計(全期間)", life.get("n_posts", 0),
                     life.get("total_views", 0), life.get("avg_er", "")])
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"),
                      ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        for (label, n, av, er) in a[key]:
            rows.append([axis, label, n, av, er])
    return rows


class Analyzer:
    """インサイト分析の実行役。

    now_fn は「現在時刻」の注入点（テストで固定できる）。以前は受け取るだけで未使用の
    死にパラメータだったが、期間窓の基準日として実際に使うようになった。
    """

    def __init__(self, store, now_fn=None, *, window_days: int = WINDOW_DAYS,
                 trend_window_days: int = TREND_WINDOW_DAYS):
        self.store = store
        self.now_fn = now_fn or datetime.now
        self.window_days = window_days
        self.trend_window_days = trend_window_days

    def run(self, account: str) -> dict:
        rows = self.store.get_insights(account)
        now = self.now_fn()
        # シートの投稿日時はJSTのnaive文字列なので、基準時刻もnaiveに揃える
        if getattr(now, "tzinfo", None) is not None:
            now = now.replace(tzinfo=None)
        a = analyze_windowed(rows, now=now, window_days=self.window_days,
                             trend_window_days=self.trend_window_days)
        self.store.write_analysis(account, ANALYSIS_HEADER, analysis_to_rows(a))
        logger.info("分析完了 %s: 今週%d投稿(%s) / 傾向%d投稿 / 累計%d投稿",
                    account, a["n_posts"], a["period_label"],
                    a.get("trend_n_posts", 0), a["lifetime"]["n_posts"])
        return a
