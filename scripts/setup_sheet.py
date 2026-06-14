#!/usr/bin/env python3
"""
スプレッドシート初期化＆接続チェック（Phase A の自動化）。

サービスアカウントでスプレッドシートに接続し、
  - accounts / posts タブが無ければ作成
  - 1行目のヘッダを正しい値に設定（手入力不要・冪等）
  - 接続が正しいかを緑/赤でレポート
する。

使い方:
  python3 scripts/setup_sheet.py --json /path/to/service_account.json --sheet <SPREADSHEET_ID>
  （または環境変数 GOOGLE_SERVICE_ACCOUNT_FILE / GOOGLE_SERVICE_ACCOUNT_JSON / SPREADSHEET_ID）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ACCOUNTS_HEADERS = [
    "account", "user_id", "access_token",
    "token_updated_at", "daily_count", "daily_count_date",
]
POSTS_HEADERS = [
    "row_id", "account", "post_datetime", "text", "media_type", "media_url",
    "reply_to", "reply_control", "status", "posted_id", "posted_at", "error",
]

OK = "\033[92m✓\033[0m"
NG = "\033[91m✗\033[0m"


def load_creds_info(json_path: str | None):
    """サービスアカウント情報を dict で返す。優先: --json > 環境変数JSON > 環境変数FILE。"""
    if json_path:
        with open(os.path.expanduser(json_path)) as f:
            return json.load(f)
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw)
    fpath = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if fpath:
        with open(os.path.expanduser(fpath)) as f:
            return json.load(f)
    return None


def ensure_tab(sh, name: str, headers: list[str]):
    """タブが無ければ作り、1行目ヘッダを正しい値にする（冪等）。"""
    existing = {ws.title: ws for ws in sh.worksheets()}
    ws = existing.get(name)
    if ws is None:
        ws = sh.add_worksheet(title=name, rows=500, cols=max(12, len(headers)))
        print(f"  {OK} タブ '{name}' を新規作成")
    current = ws.row_values(1)
    if current == headers:
        print(f"  {OK} タブ '{name}' のヘッダは正しい")
    else:
        last_col = chr(ord("A") + len(headers) - 1)  # 列数は最大12なのでA-L
        ws.batch_update([{"range": f"A1:{last_col}1", "values": [headers]}])
        action = "修正" if current else "設定"
        print(f"  {OK} タブ '{name}' のヘッダを{action}（{len(headers)}列）")
    return ws


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="サービスアカウントJSONのパス")
    ap.add_argument("--sheet", help="スプレッドシートID")
    args = ap.parse_args()

    sheet_id = args.sheet or os.environ.get("SPREADSHEET_ID")

    print("=== Phase A 接続チェック & シート初期化 ===\n")

    # 1) 認証情報
    try:
        info = load_creds_info(args.json)
    except FileNotFoundError as e:
        print(f"{NG} JSONファイルが見つかりません: {e.filename}")
        return 1
    except json.JSONDecodeError as e:
        print(f"{NG} JSONの形式が不正です: {e}")
        return 1
    if not info:
        print(f"{NG} サービスアカウントJSONが指定されていません。")
        print("    --json <path> か 環境変数 GOOGLE_SERVICE_ACCOUNT_FILE を設定してください。")
        return 1

    client_email = info.get("client_email", "(不明)")
    print(f"  {OK} サービスアカウント読込: {client_email}")

    # 2) スプレッドシートID
    if not sheet_id:
        print(f"{NG} スプレッドシートIDが未指定です（--sheet か SPREADSHEET_ID）。")
        return 1
    print(f"  {OK} スプレッドシートID: {sheet_id}")

    # 3) 接続
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print(f"{NG} 依存が未インストール: pip3 install -r requirements.txt")
        return 1

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"\n{NG} シートを開けません（未共有 or ID違い）。")
        print(f"    対処: スプレッドシートの『共有』に下記を【編集者】で追加してください:")
        print(f"      {client_email}")
        return 1
    except gspread.exceptions.APIError as e:
        msg = str(e)
        print(f"\n{NG} Google APIエラー: {msg[:300]}")
        if "Google Sheets API has not been used" in msg or "SERVICE_DISABLED" in msg:
            print("    対処: Google Cloud で『Google Sheets API』を有効化してください。")
        elif "PERMISSION_DENIED" in msg or "403" in msg:
            print(f"    対処: シートを {client_email} に【編集者】で共有してください。")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\n{NG} 接続失敗: {type(e).__name__}: {str(e)[:300]}")
        return 1

    print(f"  {OK} スプレッドシート '{sh.title}' に接続成功\n")

    # 4) タブ＆ヘッダ整備
    print("  --- タブ / ヘッダ ---")
    ensure_tab(sh, "accounts", ACCOUNTS_HEADERS)
    ensure_tab(sh, "posts", POSTS_HEADERS)

    print(f"\n{OK} Phase A 完了。シートは投稿システムから利用可能です。")
    print("  次: accounts タブにトークンを入れる（Phase B/C）→ DRY_RUN 疎通。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
