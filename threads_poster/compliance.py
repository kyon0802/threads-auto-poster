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


# ---------------------------------------------------------------- 重複ゲート（2026-09-07）
# ★なぜ必要か
# 2026-09-01に「勝ち投稿の本文をAIに見せる」PDCA機能を入れた直後から、AIが実例を丸写しし、
# 完全一致の投稿が数日おきに再投稿されるようになった（実測: 住田 0組→11組・ぱし 0組→15組・
# takumi の重複率は13倍）。Threadsで同一本文を繰り返すのはスパム判定の引き金であり、
# 製造業アカウントは過去2回BANされている。プロンプトの指示だけでは確実に防げないので、
# 公開前の機械ゲートで落とす（決定的・AIの気分に左右されない）。
DUPLICATE_THRESHOLD = 0.85  # 0.9台=丸写し、0.85前後=言い回しを変えただけの再投稿まで捕まえる


def find_near_duplicate(text: str, existing: list[str],
                        threshold: float = DUPLICATE_THRESHOLD) -> str | None:
    """`text` と類似しすぎる既存本文があれば、その本文を返す（無ければ None）。

    difflib の quick_ratio() で安くふるってから ratio() で確定する（比較件数が多いため）。
    同じテーマの別投稿（0.7前後）は通し、実質同一（0.85以上）だけを止める。
    """
    import difflib

    t = (text or "").strip()
    if not t:
        return None
    for e in existing or []:
        e = (e or "").strip()
        if not e:
            continue
        m = difflib.SequenceMatcher(None, t, e)
        if m.quick_ratio() < threshold:   # 上界なので、これ未満なら ratio も届かない
            continue
        if m.ratio() >= threshold:
            return e
    return None
