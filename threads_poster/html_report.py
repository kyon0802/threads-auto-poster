"""週次レポートの HTML ダッシュボード生成（自己完結・外部依存なし・AI不使用）。

analyzer の分析結果(dict)から、色・横棒グラフ・勝ちカード・上位投稿カード付きの
「1枚もの」HTML を文字列で返す。CSSはインライン、JS不要なのでどのブラウザでも開ける。
"""
from __future__ import annotations

import html as _html

THEMES = {
    "seizo": {"label": "製造業", "bg": "#0f1b2d", "panel": "#16263d", "line": "#24344f",
              "accent": "#e8833a", "bar": "#4a90d9", "text": "#eaf1f8", "sub": "#9fb3c8"},
    "uranai": {"label": "占い", "bg": "#1a1426", "panel": "#271d3a", "line": "#392b52",
               "accent": "#c9a24b", "bar": "#9b7bc8", "text": "#f1ecf7", "sub": "#bcabd4"},
}


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _bars(rows, theme):
    """rows: [(label, n, avg_views, avg_er), ...] → 平均エンゲージ率の横棒グラフHTML。"""
    shown = [(lbl, n, av, er) for (lbl, n, av, er) in rows if n]
    ers = [er for _, _, _, er in shown if isinstance(er, (int, float))]
    maxer = max(ers) if ers else 1.0
    out = []
    for lbl, n, av, er in shown:
        if isinstance(er, (int, float)) and er > 0:
            w = max(6, round(er / maxer * 100))
            inner = f'<div class="fill" style="width:{w}%"><span>{er}</span></div>'
        else:
            inner = '<div class="fill empty"><span>—</span></div>'
        out.append(
            f'<div class="row"><span class="lab">{_esc(lbl)}</span>'
            f'<div class="track">{inner}</div>'
            f'<span class="meta">{n}本 / 表示{av}</span></div>')
    return "\n".join(out) or '<p class="none">データ不足</p>'


def build_html(account: str, analysis: dict, gen_date: str, theme: str = "seizo", title: str | None = None) -> str:
    t = THEMES.get(theme, THEMES["seizo"])
    n = analysis.get("n_posts", 0)
    title = title or account

    def best(key):
        cand = [r for r in analysis.get(key, []) if r[1]]
        withr = [r for r in cand if isinstance(r[3], (int, float))]
        pool = withr or cand
        return max(pool, key=lambda r: (r[3] if isinstance(r[3], (int, float)) else -1), default=None)

    cards = []
    for axis, key in [("時間帯", "by_time"), ("本文長", "by_length"), ("形式", "by_tree")]:
        b = best(key)
        if b:
            er = b[3] if isinstance(b[3], (int, float)) else "—"
            cards.append(f'<div class="card"><div class="c-axis">{axis}の勝ち</div>'
                         f'<div class="c-val">{_esc(b[0])}</div><div class="c-er">平均ER {er}</div></div>')
    cards_html = "".join(cards) or '<div class="card"><div class="c-val">データ蓄積中</div></div>'

    posts = []
    for i, r in enumerate(analysis.get("top", []), 1):
        posts.append(f'<div class="post"><div class="rank">{i}</div>'
                     f'<div class="pv">{_esc(r.get("views"))}<small>表示</small></div>'
                     f'<div class="pm">ER {_esc(r.get("engagement_rate"))} ・ {_esc(r.get("text_len"))}字'
                     f' ・ {_esc(r.get("post_datetime"))}</div></div>')
    posts_html = "".join(posts) or '<p class="none">上位データなし</p>'

    sections = ""
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        sections += f'<div class="axis"><h3>{axis}別 平均エンゲージ率</h3>{_bars(analysis.get(key, []), t)}</div>'

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>週次レポート｜{_esc(title)}</title>
<style>
:root {{ --bg:{t['bg']}; --panel:{t['panel']}; --line:{t['line']}; --accent:{t['accent']};
         --bar:{t['bar']}; --text:{t['text']}; --sub:{t['sub']}; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
        font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; line-height:1.6; }}
.wrap {{ max-width:880px; margin:0 auto; padding:28px 20px 60px; }}
.head {{ border-left:6px solid var(--accent); padding:6px 0 6px 16px; margin-bottom:6px; }}
.head h1 {{ margin:0; font-size:24px; }}
.head .sub {{ color:var(--sub); font-size:13px; }}
.cards {{ display:flex; gap:14px; margin:22px 0; flex-wrap:wrap; }}
.card {{ flex:1; min-width:170px; background:var(--panel); border:1px solid var(--line);
         border-radius:14px; padding:16px 18px; }}
.c-axis {{ color:var(--sub); font-size:12px; }}
.c-val {{ font-size:20px; font-weight:700; margin:4px 0; color:var(--accent); }}
.c-er {{ color:var(--sub); font-size:12px; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
          padding:18px 20px; margin:16px 0; }}
.panel h2 {{ margin:0 0 12px; font-size:16px; }}
.axis {{ margin:14px 0; }}
.axis h3 {{ font-size:13px; color:var(--sub); margin:0 0 8px; font-weight:600; }}
.row {{ display:flex; align-items:center; gap:10px; margin:6px 0; }}
.lab {{ width:96px; font-size:13px; color:var(--text); flex:none; }}
.track {{ flex:1; background:rgba(255,255,255,.06); border-radius:8px; height:24px; overflow:hidden; }}
.fill {{ height:100%; background:linear-gradient(90deg,var(--bar),var(--accent));
         border-radius:8px; display:flex; align-items:center; justify-content:flex-end;
         padding-right:8px; min-width:34px; }}
.fill span {{ font-size:11px; color:#fff; font-weight:700; }}
.fill.empty {{ background:rgba(255,255,255,.08); justify-content:center; padding:0; }}
.fill.empty span {{ color:var(--sub); }}
.meta {{ width:120px; text-align:right; font-size:11px; color:var(--sub); flex:none; }}
.post {{ display:flex; align-items:center; gap:14px; padding:10px 0; border-bottom:1px solid var(--line); }}
.post:last-child {{ border-bottom:none; }}
.rank {{ width:26px; height:26px; border-radius:50%; background:var(--accent); color:#10131a;
         font-weight:800; display:flex; align-items:center; justify-content:center; font-size:13px; flex:none; }}
.pv {{ width:84px; font-size:20px; font-weight:800; flex:none; }}
.pv small {{ font-size:10px; color:var(--sub); font-weight:500; margin-left:3px; }}
.pm {{ color:var(--sub); font-size:12px; }}
.none {{ color:var(--sub); font-size:13px; }}
.foot {{ color:var(--sub); font-size:11px; margin-top:24px; text-align:center; }}
</style></head>
<body><div class="wrap">
  <div class="head">
    <h1>📊 週次レポート｜{_esc(title)}</h1>
    <div class="sub">{t['label']} ・ 生成日 {_esc(gen_date)} ・ 対象 {n} 投稿</div>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="panel"><h2>勝ちパターン（軸別の平均エンゲージ率）</h2>{sections}</div>
  <div class="panel"><h2>🏆 上位投稿（表示数）</h2>{posts_html}</div>
  <div class="foot">Threads自動分析 ・ エンゲージ率＝(いいね+返信+リポスト+引用)÷表示回数</div>
</div></body></html>"""
