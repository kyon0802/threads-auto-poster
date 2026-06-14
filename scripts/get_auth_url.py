#!/usr/bin/env python3
"""
Phase B: Threads の OAuth 認可URLを生成する。
表示されたURLをブラウザで開き、投稿用アカウントで承認すると、
redirect_uri に ?code=XXXX が付いて返ってくる。その code を exchange_token.py に渡す。

使い方:
  python3 scripts/get_auth_url.py --app-id <App ID> --redirect https://localhost/
  （または環境変数 THREADS_APP_ID / THREADS_REDIRECT_URI）
"""
import argparse
import os
import sys
import urllib.parse

DEFAULT_SCOPE = "threads_basic,threads_content_publish"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", default=os.environ.get("THREADS_APP_ID"))
    ap.add_argument("--redirect", default=os.environ.get("THREADS_REDIRECT_URI", "https://localhost/"))
    ap.add_argument("--scope", default=DEFAULT_SCOPE)
    args = ap.parse_args()

    if not args.app_id:
        print("THREADS_APP_ID（Meta アプリの App ID）が必要です。--app-id か環境変数で指定してください。")
        return 1

    q = urllib.parse.urlencode({
        "client_id": args.app_id,
        "redirect_uri": args.redirect,
        "scope": args.scope,
        "response_type": "code",
    })
    url = "https://threads.net/oauth/authorize?" + q
    print("以下のURLをブラウザで開き、投稿用Threadsアカウントで承認してください:\n")
    print(url)
    print("\n承認後、リダイレクト先URL（例 https://localhost/?code=XXXXXX#_ ）の")
    print("『code=』の後ろ〜『#』の手前までをコピーし、exchange_token.py に渡してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
