"""翌週コンテンツ自動生成（Claude API）＋機械コンプラゲート（Phase 2）。

プロフィール/ガイドライン/分析結果を読み、翌週分の投稿案を生成 → コンプラ通過分のみ
`投稿_<acc>` へ **status=draft** で投入する（人が確認して queued に変えるまで自動公開されない）。

★必須タブ存在ゲート（§17e）：`プロフィール_<acc>` か `ガイドライン` が空/欠落なら
生成を**中止**（盲目生成の禁止）。空のまま低品質/規約違反の投稿を作らせないための機械ゲート。

生成の LLM 呼び出しは `generate_fn` で注入可能（テストはフェイク注入・本番は Anthropic SDK）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .compliance import check_post, extract_ng_words

logger = logging.getLogger("generator")
DEFAULT_MODEL = "claude-opus-4-8"


class GeneratorError(Exception):
    pass


def build_prompt(account: str, profile: dict, guideline: list[dict], analysis: dict, n: int,
                 knowledge: str = "") -> str:
    guide = "\n".join(f"- [{g.get('重大度')}] {g.get('分類')}: {g.get('ルール')}" for g in guideline)
    wins = []
    for axis, key in [("時間帯", "by_time"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        cand = [t for t in analysis.get(key, []) if t[1]]
        best = max(cand, key=lambda t: (t[3] if isinstance(t[3], (int, float)) else -1), default=None)
        if best:
            wins.append(f"{axis}は「{best[0]}」が好調")
    # 知識源：ナレッジ全文（最優先・濃い）があればそれを、無ければプロフィールを使う
    if knowledge.strip():
        know_section = ("## 事業ナレッジ（最重要・このアカウントの全知識。声・戦略・合法ライン・"
                        "勝ち筋・フック型・KGI/CVRを含む。これを土台に作る）\n" + knowledge.strip())
    else:
        prof = "\n".join(f"- {k}: {v}" for k, v in profile.items())
        know_section = "## プロフィール\n" + prof
    return (
        f"あなたはThreadsアカウント「{account}」の専属コンテンツ戦略担当です。\n"
        f"下記の事業ナレッジとガイドライン（規約・法令・NG）を**厳守**し、\n"
        f"実績の勝ちパターンを踏まえて、エンゲージ（最終的には送客/成立=CVR）が伸びる"
        f"翌週の投稿案を{n}本作成してください。\n\n"
        f"{know_section}\n\n"
        f"## ガイドライン（厳守・違反した投稿は機械的に破棄される）\n{guide}\n\n"
        f"## 実績の勝ちパターン\n" + ("／".join(wins) if wins else "（データ蓄積中・お手本の型を踏襲）") + "\n\n"
        f"## 出力要件\n"
        f"- 各投稿は独立した完成本文（そのまま投稿できる形）\n"
        f"- NGワードは絶対に使わない／本文に外部URLを書かない（誘導はプロフィール動線）\n"
        f"- 1投稿500字以内・ナレッジの声と勝ち筋・フック型を踏襲\n"
    )


class Generator:
    def __init__(self, store, account: str, generate_fn=None, now_fn=None,
                 n_posts: int = 5, status: str = "draft", id_prefix: str | None = None,
                 suggest_hour: int = 21, tz_name: str = "Asia/Tokyo", model: str = DEFAULT_MODEL):
        self.store = store
        self.account = account
        self.generate_fn = generate_fn or make_anthropic_generate_fn(model, n_posts)
        self.tz = ZoneInfo(tz_name)
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.n_posts = n_posts
        self.status = status
        self.id_prefix = id_prefix or account.split("_")[0]
        self.suggest_hour = suggest_hour

    def run(self, analysis: dict, candidates: list[str] | None = None) -> dict:
        profile = self.store.get_profile(self.account)
        guideline = self.store.get_guideline()
        knowledge = self.store.get_knowledge(self.account)
        # ★必須タブ存在ゲート（§17e）：ガイドライン(NG語)必須 ＋ ナレッジ or プロフィール のどちらか。
        # 空/欠落なら loud fail（盲目生成しない）。
        if not guideline or not (knowledge.strip() or profile):
            raise GeneratorError(
                f"{self.account}: ナレッジ/プロフィール or ガイドライン未整備のため生成中止"
                f"（knowledge={len(knowledge)}字 / profile={len(profile)}項目 / guideline={len(guideline)}行）")
        ng = extract_ng_words(guideline)

        if candidates is None:
            prompt = build_prompt(self.account, profile, guideline, analysis, self.n_posts, knowledge=knowledge)
            candidates = self.generate_fn(prompt)

        now = self.now_fn()
        date = now.strftime("%Y%m%d")
        kept, rejected = [], []
        for text in candidates:
            ok, reasons = check_post(text, ng)
            (kept if ok else rejected).append(text if ok else {"text": text, "reasons": reasons})

        written = []
        for j, text in enumerate(kept, 1):
            row_id = f"{self.id_prefix}-g{date}-{j:02d}"
            dt = (now + timedelta(days=j)).replace(hour=self.suggest_hour, minute=0, second=0, microsecond=0)
            self.store.add_post(self.account, {
                "row_id": row_id,
                "post_datetime": dt.strftime("%Y-%m-%d %H:%M"),
                "text": text,
                "media_type": "TEXT",
                "status": self.status,  # draft = 自動公開されない（人が queued に変える）
            })
            written.append(row_id)
        logger.info("%s 生成完了: 候補%d / 合格%d / 破棄%d", self.account, len(candidates), len(kept), len(rejected))
        return {"kept": kept, "rejected": rejected, "written": written}


def make_anthropic_generate_fn(model: str = DEFAULT_MODEL, n: int = 5):
    """本番の生成関数（Anthropic 公式SDK）。ANTHROPIC_API_KEY を環境から読む。
    構造化出力で投稿配列を受け取り、そのまま返す。"""
    def fn(prompt: str) -> list[str]:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system="日本語で、指定のプロフィールとガイドラインに完全準拠した投稿本文のみを生成する。",
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {"posts": {"type": "array", "items": {"type": "string"}}},
                "required": ["posts"],
                "additionalProperties": False,
            }}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text).get("posts", [])
    return fn
