"""来週の方針＋具体的な投稿例（3本前後）を生成する（週次レポート用・Claude）。

generator が「公開する投稿」を作るのに対し、こちらは**人が読む方針サマリ**を作る。
分析結果（勝ちパターン）＋事業ナレッジ／ガイドラインを踏まえて、
  - direction: 来週の方針（数文）
  - focus:    具体的にやること（箇条書き 3〜5）
  - examples: 方針に沿った投稿例（3本前後・本文つき）
を返す。例文は機械コンプラゲート（NGワード/URL/文字数）を通し、違反は落とす。

LLM 呼び出しは `generate_fn` で注入可能（テスト＝フェイク／本番＝Anthropic SDK）。
キーが無い等で生成できない場合は呼び出し側で None を扱う（レポートは方針なしで出る）。
"""
from __future__ import annotations

import json
import logging

from .compliance import check_post, extract_ng_words

logger = logging.getLogger("strategy")
DEFAULT_MODEL = "claude-opus-4-8"


def _wins(analysis: dict) -> list[str]:
    wins = []
    for axis, key in [("時間帯", "by_time"), ("曜日", "by_weekday"),
                      ("本文長", "by_length"), ("形式", "by_tree")]:
        cand = [t for t in analysis.get(key, []) if t[1]]
        best = max(cand, key=lambda t: (t[3] if isinstance(t[3], (int, float)) else -1), default=None)
        if best:
            wins.append(f"{axis}は「{best[0]}」が好調")
    return wins


def build_strategy_prompt(account: str, analysis: dict, knowledge: str, guideline: list[dict],
                          profile: dict | None = None) -> str:
    wins = "／".join(_wins(analysis)) or "（データ蓄積中）"
    guide = "\n".join(f"- [{g.get('重大度')}] {g.get('分類')}: {g.get('ルール')}" for g in guideline)
    know = knowledge.strip()[:8000]
    # 知識源：ナレッジ全文（濃い）を最優先。無ければプロフィール（声/テーマ/お手本/NG）を使う。
    if know:
        know_section = "## 事業ナレッジ（声・戦略・合法ライン・勝ち筋）\n" + know
    elif profile:
        prof = "\n".join(f"- {k}: {v}" for k, v in profile.items())
        know_section = "## プロフィール（声・テーマ・お手本・NG）\n" + prof
    else:
        know_section = "## 事業ナレッジ\n（なし）"
    return (
        f"あなたはThreadsアカウント「{account}」の専属コンテンツ戦略担当です。\n"
        f"直近の実績分析と事業ナレッジ/プロフィールをもとに、**来週1週間の運用方針**を立ててください。\n\n"
        f"## 直近の勝ちパターン（実データ）\n{wins}\n"
        f"・総表示 {analysis.get('total_views')}／平均エンゲージ率 {analysis.get('avg_er')}／"
        f"分析対象 {analysis.get('n_posts')}投稿\n\n"
        f"{know_section}\n\n"
        f"## ガイドライン（厳守・違反例文は破棄される）\n{guide}\n\n"
        f"## 出力（JSON）\n"
        f"- direction: 来週の方針（3〜5文。なぜそうするかの根拠を実データに紐づけて具体的に）\n"
        f"- focus: 具体的にやること（箇条書き3〜5個。時間帯/本文長/テーマ/フックなど実行可能な指示）\n"
        f"- examples: 方針に沿った投稿例を3本。各 {{狙い, 本文}}。本文はそのまま投稿できる完成形・"
        f"NGワードと外部URLは使わない・1投稿500字以内・ナレッジの声と勝ち筋を踏襲。\n"
    )


def make_anthropic_strategy_fn(model: str = DEFAULT_MODEL):
    def fn(prompt: str) -> dict:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            # 例文3本前後＋方針なので8000で十分。例文本数を増やすならここも連動させる。
            max_tokens=8000,
            system="日本語で、指定のナレッジとガイドラインに完全準拠した運用方針と投稿例のみを生成する。",
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string"},
                    "focus": {"type": "array", "items": {"type": "string"}},
                    "examples": {"type": "array", "items": {"type": "object", "properties": {
                        "狙い": {"type": "string"}, "本文": {"type": "string"}},
                        "required": ["狙い", "本文"], "additionalProperties": False}},
                },
                "required": ["direction", "focus", "examples"],
                "additionalProperties": False,
            }}},
        )
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise RuntimeError("strategy 生成が max_tokens で打ち切られました")
        text = next(b.text for b in resp.content if b.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"strategy のJSON解析に失敗（max_tokens切れ疑い）: {e}") from e
    return fn


def generate_strategy(store, account: str, analysis: dict, generate_fn=None,
                      model: str = DEFAULT_MODEL) -> dict | None:
    """来週方針＋例文を生成して dict で返す。例文はコンプラゲート通過分のみ残す。
    生成に失敗（キー無し等）したら None（レポートは方針セクションなしで出力）。"""
    guideline = store.get_guideline()
    knowledge = store.get_knowledge(account)
    profile = store.get_profile(account)
    if not knowledge.strip() and not profile:
        logger.info("%s: ナレッジ/プロフィール未整備のため方針生成をスキップ", account)
        return None
    ng = extract_ng_words(guideline)
    fn = generate_fn or make_anthropic_strategy_fn(model)
    try:
        prompt = build_strategy_prompt(account, analysis, knowledge, guideline, profile)
        raw = fn(prompt)
    except Exception as e:  # noqa: BLE001 キー無し/通信失敗等。方針なしでレポートは出す。
        logger.warning("%s: 方針生成に失敗（方針なしで継続）: %s", account, e)
        return None

    examples = []
    for ex in raw.get("examples", []):
        body = (ex.get("本文") or "").strip()
        if not body:
            continue
        ok, reasons = check_post(body, ng)
        if ok:
            examples.append({"aim": (ex.get("狙い") or "").strip(), "text": body})
        else:
            logger.info("%s: 方針例文をコンプラで除外: %s", account, reasons)
    return {
        "direction": (raw.get("direction") or "").strip(),
        "focus": [f.strip() for f in raw.get("focus", []) if f and f.strip()],
        "examples": examples,
    }
