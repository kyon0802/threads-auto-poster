"""Threads API のトークン漏洩防止（公開repoのActionsログ対策）。

背景（2026-07-28の監査）: アクセストークンは URL のクエリで送っているが、通信呼び出し
8箇所すべてに例外処理が無かった。ネットワーク瞬断やタイムアウトが起きると requests の
例外メッセージに `Max retries exceeded with url: ...access_token=<本物>` が入り、それが
ログへ出る。このリポジトリは public で Actions ログは第三者も閲覧でき、しかもトークンは
GitHub Secret ではなくシート保管なので Actions の自動マスクも効かない。

実行: python3 -m pytest tests/test_threads_api_masking.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from threads_poster.threads_api import (  # noqa: E402
    ThreadsAPIError, ThreadsClient, http_request, mask_secrets,
)

TOKEN = "THAAABsecret-token-value-should-never-appear"


def test_mask_secrets_redacts_access_token_in_url():
    url = f"https://graph.threads.net/v1.0/123/threads?media_type=TEXT&access_token={TOKEN}&text=hi"
    masked = mask_secrets(url)
    assert TOKEN not in masked, masked
    assert "access_token=<REDACTED>" in masked
    assert "media_type=TEXT" in masked and "text=hi" in masked  # 秘密以外は消さない
    print("  ✓ URL中の access_token をマスク OK")


def test_mask_secrets_redacts_client_secret():
    # 長期トークン交換のURLには client_secret も載る
    url = f"https://graph.threads.net/access_token?client_secret=abc123XYZ&access_token={TOKEN}"
    masked = mask_secrets(url)
    assert "abc123XYZ" not in masked and TOKEN not in masked, masked
    assert masked.count("<REDACTED>") == 2
    print("  ✓ client_secret もマスク OK")


def test_mask_secrets_handles_non_string():
    assert mask_secrets(None) == "None"
    assert mask_secrets(ValueError("boom")) == "boom"
    print("  ✓ 例外オブジェクト等の非文字列でも落ちない OK")


def test_http_request_masks_connection_error():
    # requests が投げる典型的な例外メッセージ（URL入り）を再現する
    real_message = (
        "HTTPSConnectionPool(host='graph.threads.net', port=443): Max retries exceeded with url: "
        f"/v1.0/123/threads?media_type=TEXT&access_token={TOKEN} (Caused by NewConnectionError(...))"
    )
    orig = requests.request

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError(real_message)

    requests.request = boom
    try:
        try:
            http_request("post", "https://graph.threads.net/v1.0/123/threads",
                         {"access_token": TOKEN})
        except ThreadsAPIError as e:
            assert TOKEN not in str(e), str(e)
            assert "<REDACTED>" in str(e)
            assert "ConnectionError" in str(e)      # 何が起きたかは分かる
            assert e.__cause__ is None              # 元例外を鎖に残さない（URLが再露出するため）
        else:
            raise AssertionError("ThreadsAPIError が送出されなかった")
    finally:
        requests.request = orig
    print("  ✓ 通信失敗の例外からトークンが消え、例外チェーンも切れている OK")


def test_http_request_masks_timeout():
    orig = requests.request

    def boom(*a, **k):
        raise requests.exceptions.ReadTimeout(f"timeout for url ...access_token={TOKEN}")

    requests.request = boom
    try:
        try:
            http_request("get", "https://graph.threads.net/v1.0/x", {"access_token": TOKEN})
        except ThreadsAPIError as e:
            assert TOKEN not in str(e), str(e)
        else:
            raise AssertionError("ThreadsAPIError が送出されなかった")
    finally:
        requests.request = orig
    print("  ✓ タイムアウトでもマスクされる OK")


def test_error_message_is_masked_at_construction():
    # API のエラーボディがトークンを含んで返ってきた場合も、例外生成時点で伏せる
    e = ThreadsAPIError(f"APIエラー status=400 body={{'url': 'x?access_token={TOKEN}'}}")
    assert TOKEN not in str(e) and "<REDACTED>" in str(e)
    print("  ✓ ThreadsAPIError は生成時にマスクされる OK")


def test_all_http_calls_go_through_the_wrapper():
    """新しい呼び出しが素の requests を使って穴を開けないことを機械的に保証する。"""
    import inspect

    import threads_poster.threads_api as mod

    src = inspect.getsource(mod)
    body = src.split("def http_request", 1)[1]
    # http_request 定義の中の1回だけが requests.request を直接呼んでよい
    assert body.count("requests.request(") == 1, "requests を直接呼ぶ箇所が増えています"
    assert "requests.get(" not in src and "requests.post(" not in src, \
        "requests.get/post の直接呼び出しが復活しています（http_request 経由にすること）"
    print("  ✓ 素の requests 呼び出しが1箇所（ラッパ内）だけであることを機械保証 OK")


def test_client_delegates_to_wrapper():
    c = ThreadsClient("123", TOKEN, timeout=7)
    seen = {}

    orig = requests.request

    def spy(method, url, params=None, timeout=None):
        seen.update(method=method, timeout=timeout)
        raise requests.exceptions.ConnectionError("x")

    requests.request = spy
    try:
        try:
            c.get_status("cid")
        except ThreadsAPIError:
            pass
    finally:
        requests.request = orig
    assert seen == {"method": "get", "timeout": 7}, seen
    print("  ✓ ThreadsClient がラッパへ委譲し timeout も渡している OK")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n全 {len(fns)} 件 PASS")
