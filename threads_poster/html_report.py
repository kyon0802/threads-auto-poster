"""週次レポートの HTML 生成（自己完結・外部依存なし・AI不使用）。

明るい白ベースのシンプルなデザイン。**メール(Gmail)でもそのまま読める**よう、
レイアウトは table ＋ 全要素インラインCSS（flex/grid/外部CSSは使わない）。

含む項目（ユーザー要望）:
  1) KPIサマリ（各指標に短い説明つき＝曖昧さをなくす）
  2) 今週の投稿ランキング TOP5（**実際の投稿本文**を全文表示）— 表示数順＋エンゲージ率順
  3) 傾向分析（時間帯/曜日/本文長/形式の棒グラフ＋一言の読み解き）
  4) 来週の方針（AI生成）＋ 方針に沿った投稿例 3本
"""
from __future__ import annotations

import html as _html

# 明るい白ベースのテーマ（事業ごとにアクセント色だけ変える）。
THEMES = {
    "seizo": {"label": "製造業", "accent": "#e8833a", "accent_d": "#c25e15", "bar": "#e8833a"},
    "uranai": {"label": "占い", "accent": "#a9803f", "accent_d": "#876327", "bar": "#9b7bc8"},
}
INK = "#1d2733"      # 本文の濃い色
SUB = "#6b7785"      # 補助テキスト
LINE = "#e3e8ee"     # 枠線
PANEL = "#ffffff"    # カード背景
PAGE = "#f4f6f8"     # ページ背景
TRACK = "#eef1f5"    # 棒グラフの溝


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _br(s) -> str:
    """投稿本文の改行を保持（エスケープ後に \\n → <br>）。"""
    return _esc(s).replace("\n", "<br>")


def _num(v) -> str:
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"):  # NaN/inf は壊れ表示を避ける
            return "—"
        return f"{int(f):,}"
    except (TypeError, ValueError):
        return _esc(v)


def _er_pct(er) -> str:
    """エンゲージ率(0.0244)→『2.44%』。空/NaN/inf なら『—』。"""
    try:
        f = float(er)
        if f != f or abs(f) == float("inf"):
            return "—"
        return f"{f * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _best(rows):
    cand = [r for r in rows if r[1]]
    withr = [r for r in cand if isinstance(r[3], (int, float))]
    pool = withr or cand
    return max(pool, key=lambda r: (r[3] if isinstance(r[3], (int, float)) else -1), default=None)


def _kpi_cards(a: dict, t: dict) -> str:
    items = [
        (_num(a.get("total_views", 0)), "総表示回数", "今週の全投稿が見られた合計回数（リーチの大きさ）"),
        (_er_pct(a.get("avg_er")), "平均エンゲージ率", "表示に対する反応の割合。投稿の“刺さり”の質"),
        (_num(a.get("n_posts", 0)), "分析投稿数", "今回の集計対象になった投稿の本数"),
        (_num(a.get("total_reactions", 0)), "合計リアクション", "いいね＋返信＋リポスト＋引用の総数"),
    ]
    cells = ""
    for val, label, desc in items:
        cells += (
            f'<td width="25%" valign="top" style="padding:6px;">'
            f'<div style="background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:14px 12px;">'
            f'<div style="font-size:24px;font-weight:800;color:{t["accent_d"]};line-height:1.2;">{val}</div>'
            f'<div style="font-size:12px;font-weight:700;color:{INK};margin-top:4px;">{label}</div>'
            f'<div style="font-size:10.5px;color:{SUB};margin-top:3px;line-height:1.5;">{desc}</div>'
            f'</div></td>')
    return (f'<table width="100%" style="border-collapse:collapse;table-layout:fixed;margin:4px 0 6px;">'
            f'<tr>{cells}</tr></table>')


def _rank_card(i: int, r: dict, t: dict) -> str:
    text = r.get("text") or "（本文が見つかりませんでした）"
    metrics = (
        f'👁 <b style="color:{INK};">{_num(r.get("views"))}</b> 表示'
        f' ・ ❤ {_num(r.get("likes"))}'
        f' ・ 💬 {_num(r.get("replies"))}'
        f' ・ 🔁 {_num(r.get("reposts"))}'
        f' ・ エンゲージ率 <b style="color:{t["accent_d"]};">{_er_pct(r.get("engagement_rate"))}</b>'
        f' ・ {_esc(r.get("post_datetime"))}')
    return (
        f'<table width="100%" style="border-collapse:collapse;margin:9px 0;background:{PANEL};'
        f'border:1px solid {LINE};border-radius:12px;"><tr>'
        f'<td width="46" valign="top" style="padding:14px 4px 14px 12px;">'
        f'<div style="width:30px;height:30px;border-radius:50%;background:{t["accent"]};color:#fff;'
        f'font-weight:800;text-align:center;line-height:30px;font-size:14px;">{i}</div></td>'
        f'<td valign="top" style="padding:12px 14px 12px 6px;">'
        f'<div style="font-size:13px;color:{INK};line-height:1.75;white-space:pre-wrap;">{_br(text)}</div>'
        f'<div style="margin-top:9px;padding-top:8px;border-top:1px dashed {LINE};'
        f'font-size:11.5px;color:{SUB};line-height:1.7;">{metrics}</div>'
        f'</td></tr></table>')


def _ranking(title: str, entries: list, t: dict, empty: str) -> str:
    if not entries:
        body = f'<div style="font-size:13px;color:{SUB};padding:6px 2px;">{empty}</div>'
    else:
        body = "".join(_rank_card(i, r, t) for i, r in enumerate(entries, 1))
    return _section(title, body, t)


def _bars(rows, t: dict) -> str:
    shown = [(lbl, n, av, er) for (lbl, n, av, er) in rows if n]
    if not shown:
        return f'<div style="font-size:12px;color:{SUB};">データ不足</div>'
    ers = [er for _, _, _, er in shown if isinstance(er, (int, float))]
    maxer = max(ers) if ers and max(ers) > 0 else 1.0
    out = '<table width="100%" style="border-collapse:collapse;">'
    for lbl, n, av, er in shown:
        if isinstance(er, (int, float)) and er > 0:
            w = max(4, round(er / maxer * 100))
            bar = (f'<div style="background:{t["bar"]};height:18px;border-radius:5px;width:{w}%;"></div>')
            val = f'ER {_er_pct(er)} ・ {n}本'
        else:
            bar = f'<div style="height:18px;"></div>'
            val = f'— ・ {n}本'
        out += (
            f'<tr>'
            f'<td width="92" style="font-size:12px;color:{INK};padding:4px 8px 4px 0;white-space:nowrap;">{_esc(lbl)}</td>'
            f'<td style="padding:4px 0;"><div style="background:{TRACK};border-radius:5px;height:18px;width:100%;">{bar}</div></td>'
            f'<td width="118" align="right" style="font-size:11px;color:{SUB};padding:4px 0 4px 8px;white-space:nowrap;">{val}</td>'
            f'</tr>')
    return out + "</table>"


def _interpret(a: dict) -> str:
    bt, bl, btr = _best(a.get("by_time", [])), _best(a.get("by_length", [])), _best(a.get("by_tree", []))
    tips = []
    if bt:
        tips.append(f'時間帯は<b>「{_esc(bt[0])}」</b>がエンゲージ最良 → 主要投稿をこの時間帯に寄せる')
    if bl:
        tips.append(f'本文長は<b>「{_esc(bl[0])}」</b>が好相性 → この長さを基準にする')
    if btr:
        tips.append(f'<b>「{_esc(btr[0])}」</b>形式が相対的に好調')
    if not tips:
        return ""
    lis = "".join(f'<li style="margin:3px 0;">{x}</li>' for x in tips)
    return (f'<div style="background:#fbf4ec;border:1px solid #f0dcc4;border-radius:10px;'
            f'padding:10px 14px;margin-top:10px;font-size:12.5px;color:{INK};line-height:1.7;">'
            f'<b>読み解き：</b><ul style="margin:6px 0 0;padding-left:18px;">{lis}</ul></div>')


def _trend(a: dict, t: dict) -> str:
    body = ""
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        body += (f'<div style="margin:10px 0;"><div style="font-size:12px;font-weight:700;color:{SUB};'
                 f'margin-bottom:5px;">{axis}別 平均エンゲージ率</div>{_bars(a.get(key, []), t)}</div>')
    body += _interpret(a)
    return _section("📈 傾向分析（どこが伸びているか）", body, t)


def _strategy(strategy: dict | None, t: dict) -> str:
    if not strategy:
        return ""
    direction = strategy.get("direction") or ""
    focus = strategy.get("focus") or []
    examples = strategy.get("examples") or []
    body = ""
    if direction:
        body += (f'<div style="font-size:13px;color:{INK};line-height:1.8;'
                 f'white-space:pre-wrap;">{_br(direction)}</div>')
    if focus:
        lis = "".join(f'<li style="margin:4px 0;">{_esc(x)}</li>' for x in focus)
        body += (f'<div style="margin-top:10px;font-size:12px;font-weight:700;color:{SUB};">具体的にやること</div>'
                 f'<ul style="margin:6px 0 0;padding-left:20px;font-size:13px;color:{INK};line-height:1.7;">{lis}</ul>')
    out = _section("🧭 来週の方針", body, t)
    if examples:
        ex_html = ""
        for i, ex in enumerate(examples, 1):
            ex_html += (
                f'<table width="100%" style="border-collapse:collapse;margin:9px 0;background:{PANEL};'
                f'border:1px solid {LINE};border-radius:12px;"><tr><td style="padding:12px 14px;">'
                f'<div style="font-size:11px;font-weight:700;color:{t["accent_d"]};">例{i}｜{_esc(ex.get("aim"))}</div>'
                f'<div style="margin-top:7px;font-size:13px;color:{INK};line-height:1.75;'
                f'white-space:pre-wrap;">{_br(ex.get("text"))}</div>'
                f'</td></tr></table>')
        out += _section("✍️ 方針に沿った投稿例（3本）", ex_html, t)
    return out


def _section(title: str, inner: str, t: dict) -> str:
    return (f'<div style="margin:18px 0 6px;font-size:15px;font-weight:800;color:{INK};'
            f'border-left:5px solid {t["accent"]};padding-left:10px;">{title}</div>'
            f'<div>{inner}</div>')


def build_fragment(account: str, analysis: dict, gen_date: str, theme: str = "seizo",
                   title: str | None = None, strategy: dict | None = None,
                   week_label: str | None = None) -> str:
    """1アカウント分のレポート本体（<html>なし）。複数アカをまとめて1通のメールに入れられる。"""
    t = THEMES.get(theme, THEMES["seizo"])
    title = title or account
    n = analysis.get("n_posts", 0)
    sub = f'{t["label"]} ・ 生成日 {_esc(gen_date)} ・ 対象 {n} 投稿'
    if week_label:
        sub = f'{t["label"]} ・ {_esc(week_label)} ・ 生成日 {_esc(gen_date)} ・ 対象 {n} 投稿'

    header = (
        f'<div style="background:{t["accent"]};border-radius:14px;padding:18px 22px;margin:18px 0 6px;">'
        f'<div style="font-size:22px;font-weight:800;color:#fff;">📊 週次レポート｜{_esc(title)}</div>'
        f'<div style="font-size:12.5px;color:#fff;opacity:.92;margin-top:4px;">{sub}</div></div>')

    return (
        header
        + _kpi_cards(analysis, t)
        + _ranking("🏆 投稿ランキング TOP5（表示数）", analysis.get("top", []), t, "上位データがまだありません")
        + _ranking("⭐ 投稿ランキング TOP5（エンゲージ率）", analysis.get("top_er", []), t,
                   "エンゲージ率の取得データがまだ少なめです")
        + _trend(analysis, t)
        + _strategy(strategy, t)
        + (f'<div style="font-size:10.5px;color:{SUB};margin-top:22px;padding-top:12px;'
           f'border-top:1px solid {LINE};line-height:1.6;text-align:center;">'
           f'Threads自動分析 ・ エンゲージ率＝(いいね＋返信＋リポスト＋引用)÷表示回数<br>'
           f'止めるには投稿キューの該当行を削除、または GitHub Variable PAUSED=1（キルスイッチ）。</div>')
    )


def wrap_document(title: str, inner: str) -> str:
    """レポート本体（fragment 1個 or 複数連結）を1枚のHTML文書にする。"""
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>週次レポート｜{_esc(title)}</title></head>'
        f'<body style="margin:0;background:{PAGE};">'
        f'<div style="max-width:720px;margin:0 auto;padding:14px 14px 50px;'
        f'font-family:-apple-system,\'Hiragino Sans\',\'Noto Sans JP\',Meiryo,sans-serif;color:{INK};">'
        f'{inner}</div></body></html>')


def build_html(account: str, analysis: dict, gen_date: str, theme: str = "seizo",
               title: str | None = None, strategy: dict | None = None,
               week_label: str | None = None) -> str:
    """1アカウント分の完全なHTML文書（reports/ 保存・添付用）。"""
    return wrap_document(
        title or account,
        build_fragment(account, analysis, gen_date, theme, title, strategy, week_label))
