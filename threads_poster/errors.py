"""失敗の理由分類（純関数・依存なし）。

背景（2026-07-28）: AI生成が 2026-07-19 以降4サイクル連続で落ちていたが、原因は全期間
「Anthropic の残高不足」の1つだったのに、ログ上はどれも同じ `exit code 2` にしか見えず、
9日間気づかれなかった。理由をレポートとメール件名に載せるための分類器。

分類は例外メッセージの文字列マッチ（SDKの例外クラスに依存しない＝SDKのバージョン差で壊れない）。
"""
from __future__ import annotations

# (分類名, その分類と判定する小文字化済みキーワード群) を上から順に評価する。
# 「残高不足」は 400 系だが認証エラーと混同されやすいので最優先で判定する。
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("残高不足", ("credit balance", "insufficient_quota", "billing", "purchase credits")),
    ("認証エラー", ("authentication_error", "invalid x-api-key", "invalid_api_key",
                    "permission_error", "401", "403")),
    ("レート制限", ("rate_limit", "429", "too many requests")),
    ("一時障害", ("overloaded", "529", "500", "502", "503", "504",
                  "timeout", "timed out", "connection")),
    ("入力エラー", ("max_tokens", "context window", "prompt is too long", "invalid_request_error")),
]

FALLBACK = "その他のエラー"


def classify_generation_error(exc: BaseException | str) -> str:
    """例外（またはメッセージ文字列）を人が読める理由に分類する。

    レポート本文とメール件名に出すためのラベルなので、必ず何かを返す（未知は「その他のエラー」）。
    """
    msg = str(exc).lower()
    for label, keywords in _RULES:
        if any(k in msg for k in keywords):
            return label
    return FALLBACK


def is_actionable_by_user(reason: str) -> bool:
    """keita が自分で解消できる種類のエラーか（＝件名で強く知らせる価値があるか）。"""
    return reason in ("残高不足", "認証エラー")
