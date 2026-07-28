"""週次レポートの HTML 生成（自己完結・外部依存なし・AI不使用）。

明るい白ベースのシンプルなデザイン。**メール(Gmail)でもそのまま読める**よう、
レイアウトは table ＋ 全要素インラインCSS（flex/grid/外部CSSは使わない）。

含む項目（ユーザー要望）:
  0) 運用状態バンド（在庫ランウェイ / フォロワー増減 / 生成の成否）★2026-07-28追加
  1) KPIサマリ（直近7日・前7日比つき。各指標に短い説明）
  2) 今週の投稿ランキング TOP5（**実際の投稿本文**を全文表示）— 表示数順＋エンゲージ率順
  3) 傾向分析（時間帯/曜日/本文長/形式の棒グラフ＋一言の読み解き・28日窓）
  4) 来週の方針（AI生成）＋ 方針に沿った投稿例 3本

★2026-07-28の改修理由: 以前は全期間累計を「今週の…」と表示していたため毎回ほぼ同じ数字が出て、
在庫が尽きて9日間投稿が止まっていたことも、生成が4サイクル連続で失敗していたことも、
レポートからは一切読み取れなかった。運用状態を必ず目に入る位置に出す。
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

# 状態色（在庫・生成の成否など。数値の増減とは別系統で使う）
OK = "#0ca30c"
WARN = "#b8860b"
CRIT = "#d03b3b"
DOWN = "#c2410c"     # 前期比マイナス


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


def _delta_chip(v) -> str:
    """前期比（増減率）のチップ。None＝比較不能（前期0や履歴なし）は「—」で誤誘導を防ぐ。"""
    if v is None:
        return (f'<span style="font-size:10.5px;color:{SUB};">前期比 —</span>')
    pct = float(v) * 100
    if abs(pct) < 0.5:
        return f'<span style="font-size:10.5px;color:{SUB};">→ 横ばい</span>'
    up = pct > 0
    color = OK if up else DOWN
    return (f'<span style="font-size:10.5px;font-weight:700;color:{color};">'
            f'{"▲" if up else "▼"} {abs(pct):.0f}%</span>')


def _kpi_cards(a: dict, t: dict) -> str:
    """KPIカード。期間窓つきの分析なら前期比チップを添える。"""
    win = a.get("window_days")
    period = a.get("period_label")
    scope = f"直近{win}日({period})" if win and period else "集計対象"
    d = a.get("delta") or {}
    items = [
        (_num(a.get("total_views", 0)), "総表示回数", d.get("total_views"),
         f"{scope}に投稿した分が見られた合計回数（リーチの大きさ）"),
        (_er_pct(a.get("avg_er")), "平均エンゲージ率", d.get("avg_er"),
         "表示に対する反応の割合。投稿の“刺さり”の質"),
        (_num(a.get("n_posts", 0)), "投稿数", d.get("n_posts"),
         f"{scope}に公開できた本数（0なら在庫切れ）"),
        (_num(a.get("total_reactions", 0)), "合計リアクション", d.get("total_reactions"),
         "いいね＋返信＋リポスト＋引用の総数"),
    ]
    cells = ""
    for val, label, delta, desc in items:
        chip = _delta_chip(delta) if a.get("window_days") else ""
        cells += (
            f'<td width="25%" valign="top" style="padding:6px;">'
            f'<div style="background:{PANEL};border:1px solid {LINE};border-radius:12px;padding:14px 12px;">'
            f'<div style="font-size:24px;font-weight:800;color:{t["accent_d"]};line-height:1.2;">{val}</div>'
            f'<div style="font-size:12px;font-weight:700;color:{INK};margin-top:4px;">{label}</div>'
            + (f'<div style="margin-top:2px;">{chip}</div>' if chip else "")
            + f'<div style="font-size:10.5px;color:{SUB};margin-top:3px;line-height:1.5;">{desc}</div>'
            f'</div></td>')
    table = (f'<table width="100%" style="border-collapse:collapse;table-layout:fixed;margin:4px 0 6px;">'
             f'<tr>{cells}</tr></table>')
    if a.get("window_days"):
        life = a.get("lifetime") or {}
        table += (f'<div style="font-size:10.5px;color:{SUB};margin:0 6px 6px;line-height:1.6;">'
                  f'※ 数値は<b>直近{win}日（{_esc(period)}）に公開した投稿</b>の実績。'
                  f'「前期比」の比較対象は<b>前{win}日（{_esc(a.get("prev_period_label", ""))}）</b>。'
                  f'（参考：全期間累計 {_num(life.get("total_views", 0))}表示 / '
                  f'{_num(life.get("n_posts", 0))}投稿）</div>')
    return table


def _status_band(runway: dict | None, followers: dict | None, gen_info: dict | None) -> str:
    """★運用状態バンド。「投稿が出ているか／出せる在庫があるか」を最上部に置く。

    今回の障害（在庫ゼロで9〜19日停止・生成4回連続失敗）が、レポートを見ても
    分からなかったことへの対策。数字ではなく運用の生死をここで見せる。
    """
    if runway is None and followers is None and gen_info is None:
        return ""
    cells = []

    if runway is not None:
        pending = runway.get("pending", 0)
        days = runway.get("days_left", 0)
        overdue = runway.get("overdue", 0)
        if pending == 0:
            silent = runway.get("silent_days")
            note = f"最終投稿から{silent}日" if silent is not None else "公開実績なし"
            cells.append((CRIT, "🔴 在庫ゼロ", f"残り0日・{note}",
                          "このままでは投稿が1本も出ません"))
        elif days <= 2 or overdue:
            extra = f"・時刻超過{overdue}本" if overdue else ""
            cells.append((WARN, f"🟡 在庫{pending}本", f"残り{days}日{extra}", "早めの補充が必要です"))
        else:
            cells.append((OK, f"🟢 在庫{pending}本", f"残り{days}日", "投稿は継続して出ます"))

    if followers is not None and followers.get("current") is not None:
        cur = followers["current"]
        dl = followers.get("delta")
        if dl is None:
            sub, color = "増減不明（履歴不足）", SUB
        elif dl > 0:
            sub, color = f"+{dl}人", OK
        elif dl < 0:
            sub, color = f"{dl}人", DOWN
        else:
            sub, color = "±0人", SUB
        cells.append((color, f"👥 {cur:,}", sub, "フォロワー数（7日前との比較）"))

    if gen_info is not None:
        ok = gen_info.get("ok")
        if ok is True:
            n = gen_info.get("written", 0)
            cells.append((OK, f"🟢 生成 {n}本", gen_info.get("status", ""), "次サイクル分を投入済み"))
        elif ok is False:
            cells.append((CRIT, f"🔴 生成失敗", _esc(gen_info.get("reason", "原因不明")),
                          _esc(gen_info.get("detail", ""))[:110]))
        else:
            cells.append((SUB, "⚪ 生成オフ", _esc(gen_info.get("reason", "")), "設定により生成していません"))

    if not cells:
        return ""
    width = int(100 / len(cells))
    tds = ""
    for color, head, sub, desc in cells:
        tds += (
            f'<td width="{width}%" valign="top" style="padding:6px;">'
            f'<div style="background:{PANEL};border:1px solid {LINE};border-left:4px solid {color};'
            f'border-radius:10px;padding:12px;">'
            f'<div style="font-size:15px;font-weight:800;color:{color};line-height:1.3;">{head}</div>'
            f'<div style="font-size:12px;font-weight:700;color:{INK};margin-top:3px;">{sub}</div>'
            f'<div style="font-size:10.5px;color:{SUB};margin-top:3px;line-height:1.5;">{desc}</div>'
            f'</div></td>')
    return (f'<div style="margin:14px 0 2px;font-size:13px;font-weight:800;color:{INK};">運用状態</div>'
            f'<table width="100%" style="border-collapse:collapse;table-layout:fixed;margin:2px 0 8px;">'
            f'<tr>{tds}</tr></table>')


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
    tw, tl = a.get("trend_window_days"), a.get("trend_period_label")
    if tw and tl:
        # KPIは7日窓だが、曜日別などは7日だと1本ずつでノイズになるので長めの窓で見る。
        # どの期間を見ているかを明示しないと、KPIと数字が合わず混乱するため必ず出す。
        body += (f'<div style="font-size:11px;color:{SUB};margin:2px 0 8px;line-height:1.6;">'
                 f'傾向は<b>直近{tw}日（{_esc(tl)}・{_num(a.get("trend_n_posts", 0))}投稿）</b>で集計。'
                 f'上のKPI（直近{a.get("window_days")}日）とは対象期間が異なります'
                 f'（曜日別などは7日だと1本ずつになり判断できないため）。</div>')
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        body += (f'<div style="margin:10px 0;"><div style="font-size:12px;font-weight:700;color:{SUB};'
                 f'margin-bottom:5px;">{axis}別 平均エンゲージ率</div>{_bars(a.get(key, []), t)}</div>')
    body += _interpret(a)
    return _section("📈 傾向分析（どこが伸びているか）", body, t)


def _strategy(strategy: dict | None, t: dict, error: str | None = None) -> str:
    if not strategy:
        # 方針が無いまま黙って消えると「正常なスキップ」と見分けがつかないので理由を出す。
        if error:
            return _section("🧭 来週の方針", (
                f'<div style="background:#fdf3f3;border:1px solid #f3d4d4;border-radius:10px;'
                f'padding:11px 14px;font-size:12.5px;color:{INK};line-height:1.7;">'
                f'今回は方針を生成できませんでした（理由: <b style="color:{CRIT};">{_esc(error)}</b>）。'
                f'解消すると次サイクルから自動で復帰します。</div>'), t)
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
                   week_label: str | None = None, *,
                   followers: dict | None = None, runway: dict | None = None,
                   gen_info: dict | None = None, strategy_error: str | None = None) -> str:
    """1アカウント分のレポート本体（<html>なし）。複数アカをまとめて1通のメールに入れられる。

    followers … analyzer.follower_trend() の結果（None なら非表示）
    runway    … inventory.compute_runway() の結果（None なら非表示）
    gen_info  … {"ok": True/False/None, "reason","detail","written","status"}（None なら非表示）
    """
    t = THEMES.get(theme, THEMES["seizo"])
    title = title or account
    n = analysis.get("n_posts", 0)
    period = analysis.get("period_label")
    scope = week_label or (f'直近{analysis.get("window_days")}日 {period}' if period else None)
    sub = f'{t["label"]} ・ 生成日 {_esc(gen_date)} ・ 対象 {n} 投稿'
    if scope:
        sub = f'{t["label"]} ・ {_esc(scope)} ・ 生成日 {_esc(gen_date)} ・ 対象 {n} 投稿'

    header = (
        f'<div style="background:{t["accent"]};border-radius:14px;padding:18px 22px;margin:18px 0 6px;">'
        f'<div style="font-size:22px;font-weight:800;color:#fff;">📊 週次レポート｜{_esc(title)}</div>'
        f'<div style="font-size:12.5px;color:#fff;opacity:.92;margin-top:4px;">{sub}</div></div>')

    return (
        header
        + _status_band(runway, followers, gen_info)
        + _kpi_cards(analysis, t)
        + _ranking("🏆 投稿ランキング TOP5（表示数）", analysis.get("top", []), t,
                   "この期間の投稿がありません（在庫切れの可能性）")
        + _ranking("⭐ 投稿ランキング TOP5（エンゲージ率）", analysis.get("top_er", []), t,
                   "エンゲージ率の取得データがまだ少なめです")
        + _trend(analysis, t)
        + _strategy(strategy, t, strategy_error)
        + (f'<div style="font-size:10.5px;color:{SUB};margin-top:22px;padding-top:12px;'
           f'border-top:1px solid {LINE};line-height:1.6;text-align:center;">'
           f'Threads自動分析 ・ エンゲージ率＝(いいね＋返信＋リポスト＋引用)÷表示回数<br>'
           f'止めるには投稿キューの該当行を削除、または GitHub Variable PAUSED=1（キルスイッチ）。</div>')
    )


def build_inventory_alert(rows: list[dict], gen_date: str) -> str:
    """在庫ランウェイ監視のアラートHTML（日次・メール本文用）。

    rows は inventory.summarize() の結果（深刻な順）。「ジョブが落ちたか」ではなく
    「投稿が出せる在庫があるか」を直接見せるのが目的なので、状態を最初に大きく出す。
    """
    crit = [r for r in rows if r["severity"] == "critical"]
    warn = [r for r in rows if r["severity"] == "warning"]
    if crit:
        color, head = CRIT, f"🔴 {len(crit)}アカウントが在庫ゼロ"
        lead = "このままでは投稿が1本も出ません。生成を回すか、在庫を補充してください。"
    elif warn:
        color, head = WARN, f"🟡 {len(warn)}アカウントの在庫が残りわずか"
        lead = "数日中に在庫が尽きます。次サイクルの生成が走るか確認してください。"
    else:
        color, head = OK, "🟢 全アカウント正常"
        lead = "すべてのアカウントに十分な在庫があります。"

    body = (f'<div style="background:{PANEL};border:1px solid {LINE};border-left:5px solid {color};'
            f'border-radius:12px;padding:16px 18px;margin:14px 0;">'
            f'<div style="font-size:19px;font-weight:800;color:{color};">{head}</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:6px;line-height:1.7;">{lead}</div></div>')

    cells = ""
    for r in rows:
        c = {"critical": CRIT, "warning": WARN, "ok": OK}[r["severity"]]
        last = f'{r["last_at"]:%m-%d %H:%M}' if r["last_at"] else "—"
        silent = f'{r["silent_days"]}日' if r["silent_days"] is not None else "—"
        cells += (
            f'<tr>'
            f'<td style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:12.5px;color:{INK};">'
            f'<b style="color:{c};">●</b> {_esc(r["account"])}</td>'
            f'<td align="right" style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:12.5px;'
            f'color:{INK};font-weight:700;">{_num(r["pending"])}本</td>'
            f'<td align="right" style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:12.5px;'
            f'color:{c};font-weight:700;">残り{r["days_left"]}日</td>'
            f'<td align="right" style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:12px;'
            f'color:{SUB};">{last}</td>'
            f'<td align="right" style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:12px;'
            f'color:{SUB};">{silent}</td>'
            f'</tr>')
    header = "".join(
        f'<th align="{a}" style="padding:8px 10px;border-bottom:2px solid {LINE};font-size:11px;'
        f'color:{SUB};font-weight:700;white-space:nowrap;">{t}</th>'
        for t, a in [("アカウント", "left"), ("在庫", "right"), ("ランウェイ", "right"),
                     ("在庫の最終日時", "right"), ("無投稿", "right")])
    body += (f'<table width="100%" style="border-collapse:collapse;background:{PANEL};'
             f'border:1px solid {LINE};border-radius:10px;"><tr>{header}</tr>{cells}</table>')
    body += (f'<div style="font-size:10.5px;color:{SUB};margin-top:14px;line-height:1.7;">'
             f'「在庫」＝これから公開される投稿の本数（status が空 または queued で未来の日時）。'
             f'draft / retired は公開対象外なので含みません。<br>'
             f'投稿ワークフローは在庫ゼロでも成功で終わるため、この通知が停止を検知する唯一の手段です。'
             f'（{_esc(gen_date)} 時点）</div>')
    return wrap_document("投稿在庫アラート", body)


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
               week_label: str | None = None, *,
               followers: dict | None = None, runway: dict | None = None,
               gen_info: dict | None = None, strategy_error: str | None = None) -> str:
    """1アカウント分の完全なHTML文書（reports/ 保存・添付用）。"""
    return wrap_document(
        title or account,
        build_fragment(account, analysis, gen_date, theme, title, strategy, week_label,
                       followers=followers, runway=runway, gen_info=gen_info,
                       strategy_error=strategy_error))
