"""機械コンプラゲート（純コード・決定的）。生成投稿が公開キューに入る前の最終防壁（§17e）。
ガイドラインタブの「NGワード」行から禁止語を抽出し、本文を機械的に遮断する。
LLMの判断に頼らず、ここで必ず止める二重の安全網。
"""
from __future__ import annotations

import re

URL_RE = re.compile(r"https?://|line\.me|lin\.ee|t\.co/|bit\.ly", re.IGNORECASE)


def extract_ng_words(guideline_rows: list[dict]) -> list[str]:
    """ガイドラインの「NGワード」分類の行から、/・、区切りの禁止語を取り出す。"""
    words: list[str] = []
    for r in guideline_rows:
        if "NGワード" in str(r.get("分類", "")):
            for w in re.split(r"[/、,／・\s]+", str(r.get("ルール", ""))):
                w = w.strip()
                if w:
                    words.append(w)
    return words


def check_post(text: str, ng_words: list[str], max_len: int = 500, forbid_url: bool = True) -> tuple[bool, list[str]]:
    """(合格か, 理由リスト) を返す。理由が空なら合格。Threads本文上限=500字。"""
    reasons: list[str] = []
    t = str(text or "")
    if not t.strip():
        reasons.append("本文が空")
    for w in ng_words:
        if w and w in t:
            reasons.append(f"NGワード「{w}」を含む")
    if len(t) > max_len:
        reasons.append(f"文字数超過（{len(t)}/{max_len}）")
    if forbid_url and URL_RE.search(t):
        reasons.append("本文に外部URL（誘導はプロフィール動線にする）")
    return (len(reasons) == 0, reasons)
