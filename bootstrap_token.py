"""
初回だけ手動で実行するトークン取得ヘルパー。
Meta開発者ダッシュボードで取得した「短期トークン(1時間)」を
「長期トークン(60日)」に交換し、スプレッドシートのaccountsタブに貼る値を出力する。

使い方:
  export THREADS_CLIENT_SECRET="あなたのApp Secret"
  python bootstrap_token.py <short_lived_token>
"""
import sys
import os
from threads_poster.threads_api import ThreadsClient


def main():
    if len(sys.argv) < 2:
        print("使い方: python bootstrap_token.py <short_lived_token>")
        sys.exit(1)
    short = sys.argv[1]
    secret = os.environ.get("THREADS_CLIENT_SECRET")
    if not secret:
        print("環境変数 THREADS_CLIENT_SECRET を設定してください")
        sys.exit(1)

    data = ThreadsClient.exchange_for_long_lived(short, secret)
    print("=== 長期トークン取得成功 ===")
    print("access_token:", data["access_token"])
    print("expires_in(秒):", data.get("expires_in"))
    print("\nスプレッドシートの accounts タブ access_token 列にこの値を貼り付け、")
    print("token_updated_at に今日の日時 (例: 2026-06-08 12:00:00) を入れてください。")


if __name__ == "__main__":
    main()
