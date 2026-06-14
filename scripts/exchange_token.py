#!/usr/bin/env python3
"""
Phase B/C: 認可コード -> 短期トークン -> 長期トークン(60日) を取得し、
accounts タブに貼り付ける値を出力する。

前提（環境変数 or 引数）:
  THREADS_APP_ID         Meta アプリの App ID
  THREADS_CLIENT_SECRET  Meta アプリの App secret
  THREADS_REDIRECT_URI   認可時と同じ redirect_uri（既定 https://localhost/）

使い方:
  python3 scripts/exchange_token.py <認可code>
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

# プロジェクトルートを import パスに追加（scripts/ から実行するため）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.threads_api import ThreadsClient, ThreadsAPIError  # noqa: E402

GRAPH = "https://graph.threads.net"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", help="OAuth認可コード")
    ap.add_argument("--app-id", default=os.environ.get("THREADS_APP_ID"))
    ap.add_argument("--secret", default=os.environ.get("THREADS_CLIENT_SECRET"))
    ap.add_argument("--redirect", default=os.environ.get("THREADS_REDIRECT_URI", "https://localhost/"))
    args = ap.parse_args()

    if not (args.app_id and args.secret):
        print("THREADS_APP_ID と THREADS_CLIENT_SECRET が必要です。")
        return 1

    # 1) 認可code -> 短期トークン（user_id も返る）
    try:
        resp = requests.post(
            f"{GRAPH}/oauth/access_token",
            data={
                "client_id": args.app_id,
                "client_secret": args.secret,
                "grant_type": "authorization_code",
                "redirect_uri": args.redirect,
                "code": args.code,
            },
            timeout=30,
        )
        short = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"短期トークン取得で通信エラー: {e}")
        return 1

    if "access_token" not in short:
        print(f"短期トークン取得失敗: {short}")
        print("  redirect_uri が認可時と一致しているか、code が使い回し/期限切れでないか確認。")
        return 1

    short_token = short["access_token"]
    user_id = str(short.get("user_id", ""))
    print("✓ 短期トークン取得")

    # 2) 短期 -> 長期(60日)
    try:
        data = ThreadsClient.exchange_for_long_lived(short_token, args.secret)
    except ThreadsAPIError as e:
        print(f"長期トークン交換失敗: {e}")
        return 1

    long_token = data["access_token"]
    print(f"✓ 長期トークン取得 (expires_in={data.get('expires_in')}秒 ≒ {int(data.get('expires_in', 0)) // 86400}日)\n")

    print("=== accounts タブに貼る値 ===")
    print(f"  user_id          : {user_id or '(取得できず。Threads APIで /me を確認)'}")
    print(f"  access_token     : {long_token}")
    print("  token_updated_at : 今日の日時を入れる（例 2026-06-09 12:00:00）")
    print("  account          : 任意の識別名（例 seizo_kyujin）")
    print("  daily_count / daily_count_date : 空でOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
