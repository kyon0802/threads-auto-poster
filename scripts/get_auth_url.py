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

# 投稿(threads_content_publish)だけでなく、インサイト収集(threads_manage_insights)も
# 最初から要求する。過去に投稿スコープだけで取得してしまい、収集を始める段階で
# 両アカウントのトークンを取り直す羽目になった（docs/CHANGELOG.md §19）。
DEFAULT_SCOPE = "threads_basic,threads_content_publish,threads_manage_insights"

# 外注アカウント（運用種別=外注）用。**投稿権限を付けない**。
# コード側でも投稿を止めているが、トークン自体に権限が無ければ万一分岐をすり抜けても
# API が投稿を拒否する＝二重の防御になる
# （2026-09-06 横断分析設計「やらないこと: 分析専用アカウントへの自動投稿」）。
COLLECT_ONLY_SCOPE = "threads_basic,threads_manage_insights"


def build_scope(collect_only: bool) -> str:
    """要求する権限を返す。collect_only=True（外注アカ）なら投稿権限を含めない。"""
    return COLLECT_ONLY_SCOPE if collect_only else DEFAULT_SCOPE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", default=os.environ.get("THREADS_APP_ID"))
    ap.add_argument("--redirect", default=os.environ.get("THREADS_REDIRECT_URI", "https://localhost/"))
    ap.add_argument("--collect-only", action="store_true",
                    help="外注アカウント用。投稿権限(threads_content_publish)を要求しない")
    ap.add_argument("--scope", default=None,
                    help="スコープを直接指定（通常は指定不要）")
    args = ap.parse_args()
    scope = args.scope or build_scope(args.collect_only)

    if not args.app_id:
        print("THREADS_APP_ID（Meta アプリの App ID）が必要です。--app-id か環境変数で指定してください。")
        return 1

    q = urllib.parse.urlencode({
        "client_id": args.app_id,
        "redirect_uri": args.redirect,
        "scope": scope,
        "response_type": "code",
    })
    url = "https://threads.net/oauth/authorize?" + q
    kind = "外注（収集のみ・投稿権限なし）" if args.collect_only else "自社（投稿＋収集）"
    print(f"種別: {kind}")
    print(f"要求する権限: {scope}\n")
    print("以下のURLをブラウザで開き、対象のThreadsアカウントでログインした状態で承認してください:\n")
    print(url)
    print("\n承認後、リダイレクト先URL（例 https://localhost/?code=XXXXXX#_ ）の")
    print("『code=』の後ろ〜『#』の手前までをコピーし、exchange_token.py に渡してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
