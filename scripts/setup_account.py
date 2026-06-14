#!/usr/bin/env python3
"""
Phase C ヘルパー: 長期トークンから user_id を取得し、accounts タブへ追記/更新する。
任意で posts タブにテスト投稿(T1)も追加する。

前提（環境変数。local_run.sh と同様に .env を source して渡す）:
  GOOGLE_SERVICE_ACCOUNT_FILE か GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID

使い方:
  set -a; . ./.env; set +a
  python3 scripts/setup_account.py --token-file ~/.config/threads-poster/token.tmp --account rk_riko2 --add-test-post
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

GRAPH = "https://graph.threads.net"
VER = "v1.0"


def load_service_account() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if path:
        with open(os.path.expanduser(path)) as f:
            return json.load(f)
    raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が見つかりません")


def upsert(ws, key_col: str, key_val: str, fields: dict) -> str:
    import gspread

    header = ws.row_values(1)
    col = {n: i + 1 for i, n in enumerate(header)}
    if key_col not in col:
        raise SystemExit(f"ヘッダに {key_col} がありません: {header}")
    key_cells = ws.col_values(col[key_col])
    target = None
    for idx, val in enumerate(key_cells[1:], start=2):
        if str(val) == str(key_val):
            target = idx
            break
    if target is None:
        row = [str(fields.get(n, "") or "") for n in header]
        ws.append_row(row, value_input_option="RAW")
        return "appended"
    cells = [
        gspread.Cell(target, col[n], "" if v is None else str(v))
        for n, v in fields.items()
        if n in col
    ]
    if cells:
        ws.update_cells(cells, value_input_option="RAW")
    return "updated"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--account", default="rk_riko2")
    ap.add_argument("--add-test-post", action="store_true")
    args = ap.parse_args()

    token = open(os.path.expanduser(args.token_file)).read().strip()

    # 1) /me で user_id / username を取得（＝トークンの有効性チェックも兼ねる）
    r = requests.get(
        f"{GRAPH}/{VER}/me",
        params={"fields": "id,username", "access_token": token},
        timeout=30,
    )
    try:
        me = r.json()
    except Exception:
        print(f"ME_NONJSON status={r.status_code} body={r.text[:300]}")
        return 1
    if "id" not in me:
        print(f"ME_ERROR status={r.status_code} body={json.dumps(me, ensure_ascii=False)}")
        return 1
    user_id = str(me["id"])
    username = me.get("username", "")
    print(f"OK /me  user_id={user_id}  username={username}")

    # 2) シート接続
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        load_service_account(), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ["SPREADSHEET_ID"])
    ws_a = sh.worksheet("accounts")
    ws_p = sh.worksheet("posts")

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    acct = args.account or username or "rk_riko2"

    # 3) accounts 追記/更新
    how = upsert(
        ws_a,
        "account",
        acct,
        {
            "account": acct,
            "user_id": user_id,
            "access_token": token,
            "token_updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "daily_count": "",
            "daily_count_date": "",
        },
    )
    print(f"OK accounts {how}  account={acct}")

    # 4) 任意: テスト投稿
    if args.add_test_post:
        dt = (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
        how_p = upsert(
            ws_p,
            "row_id",
            "T1",
            {
                "row_id": "T1",
                "account": acct,
                "post_datetime": dt,
                "text": "接続テスト：自動投稿システムの疎通確認です。確認後に削除します。",
                "media_type": "TEXT",
                "media_url": "",
                "reply_to": "",
                "reply_control": "",
                "status": "",
                "posted_id": "",
                "posted_at": "",
                "error": "",
            },
        )
        print(f"OK posts {how_p}  row_id=T1  post_datetime={dt}（JST・2分前=即時公開対象）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
