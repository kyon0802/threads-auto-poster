"""生成AIの事前疎通チェック（main_preflight）の検証。

背景（2026-07-28〜08-31）: 全アカ停止の原因は Anthropic の残高切れだったが、それを確かめる
手段が「3日に1度の週次サイクルが赤くなるのを待つ」しか無く、1ヶ月間放置された。
このチェックは副作用ゼロで原因を切り分けるためのもので、分類とマスクが要。

実行: python3 -m pytest tests/test_preflight.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main_preflight import check_anthropic, mask  # noqa: E402


def boom(message):
    """create_fn が指定メッセージで落ちるスタブを返す。"""
    def _fn(model):
        raise RuntimeError(message)
    return _fn


def test_ok_when_api_responds():
    called = []
    result = check_anthropic("claude-opus-4-8", lambda m: called.append(m))
    assert result["ok"] is True
    assert result["reason"] == "正常"
    assert called == ["claude-opus-4-8"], "本番と同じモデルIDで検査すること"


def test_credit_shortage_is_identified_and_actionable():
    """今回の障害そのもの。残高不足と分かり、かつ人が対処できると印が付くこと。"""
    msg = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
           "'message': 'Your credit balance is too low to access the Anthropic API.'}}")
    result = check_anthropic("claude-opus-4-8", boom(msg))
    assert result["ok"] is False
    assert result["reason"] == "残高不足"
    assert result["actionable"] is True


def test_auth_error_is_distinguished_from_credit():
    result = check_anthropic("claude-opus-4-8", boom("authentication_error: invalid x-api-key"))
    assert result["reason"] == "認証エラー"
    assert result["actionable"] is True


def test_api_key_is_masked_in_detail():
    """公開repoのActionsログに鍵を出さない（§17b）。"""
    # 架空の短いダミー値（実在の鍵ではない。本物らしい長さにするとsecret検知フックに
    # 引っかかるため意図的に短くしている。マスクの正規表現は1文字以上にマッチする）。
    result = check_anthropic("m", boom("bad key sk-ant-DUMMY01 rejected"))
    assert "DUMMY01" not in result["detail"]
    assert "sk-ant-***" in result["detail"]


def test_mask_leaves_normal_text_untouched():
    assert mask("credit balance is too low") == "credit balance is too low"
