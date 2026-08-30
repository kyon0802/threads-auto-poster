"""生成AIの事前疎通チェック（preflight.yml から手動実行）。

★なぜ必要か（2026-07-28の障害・docs/CHANGELOG.md §27 の続き）
全アカウント停止の原因は「Anthropic のクレジット残高切れ」だったが、それを確かめる手段が
「3日に1度の週次サイクルを待って赤くなるのを見る」しか無かった。復旧作業のたびに
本番の週次（生成→queued→即公開）を撃つのは、BAN歴のあるアカウントを抱える運用では危険。

そこでこのエントリは **生成AIが今この瞬間叩けるかどうかだけ** を最小コストで確かめる。
- シートを読まない／書かない・メールを送らない・投稿を1本も作らない（＝副作用ゼロ）
- max_tokens=1 の最小リクエストを1回だけ投げる（課金はほぼゼロ）
- 失敗理由を errors.py で分類し「残高不足／認証エラー／…」まで切り分ける
- 本番と同じ GEN_MODEL を使うので、モデルIDが古い/無効になった場合もここで判明する

環境変数:
  ANTHROPIC_API_KEY … 必須（Secret）
  GEN_MODEL         … 本番の週次と同じ既定値。実際に生成で使うモデルを検査する

終了コード: 0=正常（生成を再開できる） / 1=設定不足 / 2=APIが使えない（原因はログに出る）
"""
import logging
import os
import re
import sys

from threads_poster.errors import classify_generation_error, is_actionable_by_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main_preflight")

DEFAULT_MODEL = "claude-opus-4-8"

# 公開repoのActionsログに鍵が出ないための保険（§17b）。
# Anthropic の鍵はヘッダ送信なので通常は例外文に出ないが、ログは公開されるため必ず伏せる。
_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def mask(text: str) -> str:
    """例外メッセージに万一APIキーが混ざっていても伏せる。"""
    return _KEY_RE.sub("sk-ant-***", text)


def check_anthropic(model: str, create_fn) -> dict:
    """最小リクエストを1回投げて、生成AIが使える状態かを判定する。

    create_fn(model) を呼ぶだけに切り出してあるので、テストからSDKを注入できる
    （このリポジトリの publisher/collector と同じ依存注入の作法）。
    """
    try:
        create_fn(model)
    except Exception as e:  # noqa: BLE001 分類してユーザーに見せるのが目的なので全部受ける
        reason = classify_generation_error(e)
        return {
            "ok": False,
            "reason": reason,
            "detail": mask(str(e)),
            "actionable": is_actionable_by_user(reason),
        }
    return {"ok": True, "reason": "正常", "detail": "", "actionable": False}


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("GEN_MODEL") or DEFAULT_MODEL
    if not api_key:
        log.error("ANTHROPIC_API_KEY が未設定です（Secretを確認してください）")
        return 1

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def create_fn(m: str):
        # 最小リクエスト。応答内容には用が無く「叩けるか」だけを見るので max_tokens=1。
        return client.messages.create(
            model=m, max_tokens=1, messages=[{"role": "user", "content": "ping"}]
        )

    log.info("生成AIの事前チェックを開始します（model=%s・最小リクエスト1回）", model)
    result = check_anthropic(model, create_fn)

    if result["ok"]:
        log.info("✅ 正常：生成AIは使えます（model=%s）。週次の生成を再開できます。", model)
        return 0

    log.error("❌ 生成AIが使えません（原因=%s・model=%s）", result["reason"], model)
    log.error("詳細: %s", result["detail"])
    if result["reason"] == "残高不足":
        log.error("→ 対応：Anthropic コンソールの Plans & Billing でクレジットを購入してください"
                  "（前払い残高方式のため、請求の未納が無くても残高を使い切ると止まります）。"
                  "Auto-reload を有効にすると再発を防げます。")
    elif result["actionable"]:
        log.error("→ 対応：ANTHROPIC_API_KEY（Secret）が失効/誤りの可能性があります。再発行して更新してください。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
