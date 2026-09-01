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
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .compliance import check_post, extract_ng_words

logger = logging.getLogger("generator")
DEFAULT_MODEL = "claude-opus-4-8"


class GeneratorError(Exception):
    pass


def _normalize_candidates(candidates: list) -> list[dict]:
    """生成結果を {text, hook_type, content_type, exemplar_id} に正規化する。
    旧形式（文字列のリスト）も受ける（後方互換：既存テスト・手動注入を壊さない）。"""
    out = []
    for c in candidates:
        if isinstance(c, str):
            c = {"text": c}
        out.append({
            "text": str(c.get("text") or ""),
            "hook_type": str(c.get("hook_type") or ""),
            "content_type": str(c.get("content_type") or ""),
            "exemplar_id": str(c.get("exemplar_id") or ""),
        })
    return [c for c in out if c["text"].strip()]


def build_prompt(account: str, profile: dict, guideline: list[dict], analysis: dict, n: int,
                 knowledge: str = "", exemplars: list[dict] | None = None) -> str:
    guide = "\n".join(f"- [{g.get('重大度')}] {g.get('分類')}: {g.get('ルール')}" for g in guideline)
    wins = []
    for axis, key in [("時間帯", "by_time"), ("本文長", "by_length"), ("ツリー有無", "by_tree")]:
        cand = [t for t in analysis.get(key, []) if t[1]]
        best = max(cand, key=lambda t: (t[3] if isinstance(t[3], (int, float)) else -1), default=None)
        if best:
            wins.append(f"{axis}は「{best[0]}」が好調")
    if knowledge.strip():
        know_section = ("## 事業ナレッジ（最重要・このアカウントの全知識。声・戦略・合法ライン・"
                        "勝ち筋・フック型・KGI/CVRを含む。これを土台に作る）\n" + knowledge.strip())
    else:
        prof = "\n".join(f"- {k}: {v}" for k, v in profile.items())
        know_section = "## プロフィール\n" + prof

    # ── 実績の実物（勝ち/負けの対比・PDCA閉ループの中核）────────────────────
    def _fmt(entries, label):
        return [f"◆{label}（表示{e.get('views', 0)}回）\n{str(e.get('text') or '').strip()}"
                for e in entries]
    tops = [e for e in (analysis.get("trend_top") or []) if str(e.get("text") or "").strip()][:3]
    seen = {str(e.get("posted_id")) for e in tops}
    bottoms = [e for e in (analysis.get("trend_bottom") or [])
               if str(e.get("text") or "").strip() and str(e.get("posted_id")) not in seen][:3]
    # サンプルが薄いうちは「負け」を出さない（順位に意味が無いため）
    if analysis.get("trend_n_posts", 0) < 8:
        bottoms = []
    real = _fmt(tops, "勝ち") + _fmt(bottoms, "負け")
    real_section = ("## 実績の実物（直近28日・勝ちと負けの差から学ぶ。負けの真似は禁止）\n"
                    + "\n\n".join(real) + "\n\n") if real else ""

    # ── お手本DB（active のみ・最大7本・別ポジは構造だけ参考）─────────────────
    ex_lines = []
    for ex in (exemplars or []):
        if str(ex.get("status") or "active").strip() == "retired":
            continue
        caution = ("※別ポジの参考：フックと構造のみ真似る。声・主張・内容はコピーしない"
                   if str(ex.get("position") or "") == "別ポジ" else "")
        ex_lines.append(f"[{ex.get('exemplar_id')}]（出典:{ex.get('source')}／フック型:{ex.get('hook_type')}）{caution}\n"
                        f"{str(ex.get('text') or '').strip()}")
        if len(ex_lines) >= 7:
            break
    ex_section = ("## お手本DB（模倣した手本のIDを exemplar_id で必ず申告する）\n"
                  + "\n\n".join(ex_lines) + "\n\n") if ex_lines else ""

    type_section = (
        "## 型ラベルと探索枠（全投稿に必ず自己申告）\n"
        f"- フック型: {' / '.join(HOOK_TYPES)}（探索枠では新しい型を短い名前で自由命名可）\n"
        f"- 内容型: {' / '.join(CONTENT_TYPES)}\n"
        f"- {n}本のうち2〜3本は**探索枠**＝上の実績に無い新しいフック・切り口を意図的に試す\n"
        "- 各投稿の出力は text / hook_type / content_type / exemplar_id（模倣元。無ければ空文字）\n"
    )

    return (
        f"あなたはThreadsアカウント「{account}」の専属コンテンツ戦略担当です。\n"
        f"下記の事業ナレッジとガイドライン（規約・法令・NG）を**厳守**し、\n"
        f"実績の勝ちパターンを踏まえて、エンゲージ（最終的には送客/成立=CVR）が伸びる"
        f"翌週の投稿案を{n}本作成してください。\n\n"
        f"{know_section}\n\n"
        f"## ガイドライン（厳守・違反した投稿は機械的に破棄される）\n{guide}\n\n"
        f"## 実績の勝ちパターン（集計）\n" + ("／".join(wins) if wins else "（データ蓄積中・お手本の型を踏襲）") + "\n\n"
        f"{real_section}"
        f"{ex_section}"
        f"{type_section}\n"
        f"{THREADS_HOOK_RULES}\n\n"
        f"## 出力要件\n"
        f"- 各投稿は独立した完成本文（そのまま投稿できる形）\n"
        f"- NGワードは絶対に使わない／本文に外部URLを書かない（誘導はプロフィール動線）\n"
        f"- ナレッジの声と勝ち筋・フック型を踏襲\n"
    )


# 型ラベルの語彙（生成の自己申告と第2段の配分計算が共有する。新型は探索枠で自由命名可）
HOOK_TYPES = ["数字提示", "心の声", "逆張り", "失敗談", "問いかけ", "場面描写"]
CONTENT_TYPES = ["情報提供", "共感", "暴露", "求人紹介"]


# Threads の表示回数を左右する最重要原則（運営者の実運用知見・全事業共通）。
# generator と strategy（投稿例生成）の両方で使う。
THREADS_HOOK_RULES = (
    "## Threadsで表示回数を伸ばす鉄則（最重要・必ず守る）\n"
    "1. **1文目（フック）が全て**。Threadsはタイムラインで1〜2行目しか見えず、"
    "1文目が弱いと本文は誰にも読まれない。1文目で「自分のことだ」と指を止めさせる。\n"
    "   - 弱い例＝挨拶・自己紹介・呼びかけ・定型句で始める（例：『こんにちは』"
    "『〜なあなたへ』『今日は〜について話します』）。これらは**禁止**。\n"
    "   - 強い例＝読み手の具体的な状況・感情・心の声をいきなり描く（数字・固有の場面で刺す）。\n"
    "2. **短いほど伸びる**。長文になるほど表示回数は落ちる。1投稿は基本150字前後、"
    "長くても250字以内に収める（だらだら続けない）。\n"
    "3. **1投稿1メッセージ**。情報を詰め込まず、刺さる1点に絞る。\n"
    "4. 短い投稿と少し長い投稿を混ぜ、まず短文フック型を多めに作る。"
)


class Generator:
    def __init__(self, store, account: str, generate_fn=None, now_fn=None,
                 n_posts: int = 5, status: str = "draft", id_prefix: str | None = None,
                 suggest_hour: int = 21, tz_name: str = "Asia/Tokyo", model: str = DEFAULT_MODEL,
                 schedule_fn=None, rng=None):
        self.store = store
        self.account = account
        self.generate_fn = generate_fn or make_anthropic_generate_fn(model, n_posts)
        self.tz = ZoneInfo(tz_name)
        self.now_fn = now_fn or (lambda: datetime.now(self.tz))
        self.n_posts = n_posts
        self.status = status
        self.id_prefix = id_prefix or account.split("_")[0]
        self.suggest_hour = suggest_hour
        # 予約時刻の割り当て戦略（注入可能）。None なら従来どおり「翌日から1日1本・suggest_hour固定」。
        # 製造業は schedule.build_schedule を渡して「1日4本・昼1＋夜3・ランダム配置」にする。
        self.schedule_fn = schedule_fn
        self.rng = rng or random.Random()

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

        known_exemplar_ids = None
        if candidates is None:
            exemplars = self.store.get_exemplars(self.account)
            known_exemplar_ids = {str(ex.get("exemplar_id")) for ex in exemplars}
            prompt = build_prompt(self.account, profile, guideline, analysis, self.n_posts,
                                  knowledge=knowledge, exemplars=exemplars)
            candidates = self.generate_fn(prompt)

        candidates = _normalize_candidates(candidates)
        # exemplar_id の自己申告は無検証だとシートに嘘のIDが残る。自動生成時（exemplars を
        # 読んでいる場合）のみ、既知IDの集合と照合し未知IDは空文字に落とす。
        # candidates 手動注入時（テスト等）は exemplars を読んでいないので照合しない＝従来挙動。
        if known_exemplar_ids is not None:
            for c in candidates:
                if c["exemplar_id"] and c["exemplar_id"] not in known_exemplar_ids:
                    c["exemplar_id"] = ""
        # 想定本数より少なければ警告（max_tokens切れ等での静かな少数生成を可視化する）。
        # 正規化の後（空text除去後の実質本数）で警告する。
        if len(candidates) < self.n_posts:
            logger.warning("%s: 生成本数が想定を下回りました（要求%d / 取得%d）",
                           self.account, self.n_posts, len(candidates))

        now = self.now_fn()
        date = now.strftime("%Y%m%d")
        kept, rejected = [], []
        for c in candidates:
            ok, reasons = check_post(c["text"], ng)
            (kept.append(c) if ok else rejected.append({"text": c["text"], "reasons": reasons}))

        # 予約時刻の割り当て。schedule_fn があればそれ（製造業＝1日4本・ランダム）、
        # 無ければ従来どおり「翌日から1日1本・suggest_hour固定」。
        if self.schedule_fn is not None:
            schedule = self.schedule_fn(len(kept), start_date=now, tz=self.tz, rng=self.rng)
        else:
            schedule = [
                (now + timedelta(days=j)).replace(
                    hour=self.suggest_hour, minute=0, second=0, microsecond=0
                ).strftime("%Y-%m-%d %H:%M")
                for j in range(1, len(kept) + 1)
            ]

        written = []
        for j, (c, post_dt) in enumerate(zip(kept, schedule), 1):
            row_id = f"{self.id_prefix}-g{date}-{j:02d}"
            self.store.add_post(self.account, {
                "row_id": row_id,
                "post_datetime": post_dt,
                "text": c["text"],
                "media_type": "TEXT",
                "status": self.status,  # draft = 自動公開されない（人が queued に変える）
                "hook_type": c["hook_type"],
                "content_type": c["content_type"],
                "exemplar_ref": c["exemplar_id"],
            })
            written.append(row_id)
        logger.info("%s 生成完了: 候補%d / 合格%d / 破棄%d", self.account, len(candidates), len(kept), len(rejected))
        return {"kept": kept, "rejected": rejected, "written": written}


def make_anthropic_generate_fn(model: str = DEFAULT_MODEL, n: int = 5):
    """本番の生成関数（Anthropic 公式SDK）。ANTHROPIC_API_KEY を環境から読む。
    構造化出力で投稿配列を受け取り、そのまま返す。"""
    def fn(prompt: str) -> list[dict]:
        import anthropic
        client = anthropic.Anthropic()
        # 本数に比例して出力上限を確保。1投稿は基本150字前後・最大250字（THREADS_HOOK_RULES）に
        # 型ラベル（hook_type/content_type/exemplar_id）分を加味し、28本でも切れないよう
        # 保守的に 1本=900token 見込みで確保（28本→25,200）。
        max_tokens = min(60000, max(8000, 900 * n))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system="日本語で、指定のプロフィールとガイドラインに完全準拠した投稿本文と、"
                   "その型ラベル（hook_type/content_type/exemplar_id）を生成する。",
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {"posts": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "hook_type": {"type": "string"},
                        "content_type": {"type": "string"},
                        "exemplar_id": {"type": "string"},  # 模倣元お手本ID・無ければ ""
                    },
                    "required": ["text", "hook_type", "content_type", "exemplar_id"],
                    "additionalProperties": False,
                }}},
                "required": ["posts"],
                "additionalProperties": False,
            }}},
        )
        # max_tokens 切れ（途中で打ち切り）は JSON が壊れる/本数不足の元。明示的に検知して
        # 分かりやすいエラーにする（裸の JSONDecodeError を generic 失敗にしない）。
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise GeneratorError(
                f"生成が max_tokens({max_tokens}) で打ち切られました（n={n}）。本数を減らすか上限を上げてください。")
        text = next(b.text for b in resp.content if b.type == "text")
        try:
            return json.loads(text).get("posts", [])
        except json.JSONDecodeError as e:
            raise GeneratorError(f"生成結果のJSON解析に失敗（max_tokens切れの疑い・n={n}）: {e}") from e
    return fn
