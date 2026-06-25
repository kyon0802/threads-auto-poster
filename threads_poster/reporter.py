"""週次レポート生成。分析結果を読み物に整形し、`週次レポート` タブへ追記＋
（任意で）ローカル事業フォルダへ Markdown をミラーする。AI不使用（純コード）。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("reporter")

REPORT_HEADER = ["生成日", "アカウント", "投稿数", "サマリ", "勝ちパターン仮説", "上位投稿"]


def _best(axis_rows):
    """各軸で最も良い区分(タプル label,n,平均表示,平均ER)を返す。ER優先・無ければ表示。"""
    cand = [t for t in axis_rows if t[1]]  # n>0
    if not cand:
        return None
    withr = [t for t in cand if isinstance(t[3], (int, float))]
    pool = withr or cand
    return max(pool, key=lambda t: (t[3] if isinstance(t[3], (int, float)) else -1, t[2] if isinstance(t[2], (int, float)) else -1))


def build_report(account: str, a: dict, gen_date: str) -> dict:
    bt, bw, bl, btr = (_best(a["by_time"]), _best(a["by_weekday"]),
                       _best(a["by_length"]), _best(a["by_tree"]))

    def fmt(b, axis):
        if not b:
            return None
        er = f"ER {b[3]}" if isinstance(b[3], (int, float)) else "ER -"
        return f"{axis}=「{b[0]}」({er}・平均表示{b[2]}・{b[1]}本)"

    parts = [p for p in [fmt(bt, "時間帯"), fmt(bw, "曜日"), fmt(bl, "本文長"), fmt(btr, "形式")] if p]
    summary = f"{a['n_posts']}投稿を分析。好調な傾向: " + " / ".join(parts) if parts else f"{a['n_posts']}投稿（集計対象が少なめ）。"

    hyp = []
    if bt:
        hyp.append(f"投稿は{bt[0]}帯が伸びやすい→この時間に主要投稿を寄せる")
    if bl:
        hyp.append(f"本文長は{bl[0]}が好相性→この長さを基準に")
    if btr:
        hyp.append(f"{btr[0]}形式が相対的に好調")
    hypothesis = "／".join(hyp) if hyp else "データ蓄積待ち（数日分で精度が上がる）"

    top_lines = []
    for i, t in enumerate(a["top"], 1):
        body = ""
        top_lines.append(f"{i}. 表示{t['views']}・ER{t['engagement_rate']}・{t['text_len']}字 ({t['post_datetime']})")
    top_str = " | ".join(top_lines)

    md = [f"# 週次レポート — {account}", f"_生成日: {gen_date}・対象 {a['n_posts']}投稿_", "",
          "## サマリ", summary, "", "## 勝ちパターン仮説", hypothesis, "", "## 各軸の集計"]
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        md.append(f"### {axis}")
        for (label, n, av, er) in a[key]:
            if n:
                md.append(f"- {label}: {n}本 / 平均表示 {av} / 平均ER {er}")
    md.append("")
    md.append("## 上位投稿")
    md += [f"- {ln}" for ln in top_lines]

    return {"summary": summary, "hypothesis": hypothesis, "top_str": top_str, "markdown": "\n".join(md)}


class Reporter:
    def __init__(self, store, mirror_dir: str | None = None):
        self.store = store
        self.mirror_dir = mirror_dir

    def run(self, account: str, analysis: dict, gen_date: str) -> dict:
        rep = build_report(account, analysis, gen_date)
        self.store.append_report(REPORT_HEADER, [
            gen_date, account, analysis["n_posts"], rep["summary"], rep["hypothesis"], rep["top_str"]])
        if self.mirror_dir:
            try:
                os.makedirs(self.mirror_dir, exist_ok=True)
                path = os.path.join(self.mirror_dir, f"週次レポート_{account}_{gen_date}.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(rep["markdown"])
                logger.info("レポートをローカルにミラー: %s", path)
            except OSError as e:
                logger.warning("ローカルミラー失敗: %s", e)
        return rep
