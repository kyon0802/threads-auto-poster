#!/usr/bin/env python3
"""
Phase C ヘルパー: 長期トークンから user_id を取得し、accounts タブへ追記/更新する。
任意で posts タブにテスト投稿(T1)も追加する。
見出しが日本語/英語どちらでも動く（threads_poster.sheets のエイリアスを使用）。

前提（環境変数。local_run.sh と同様に .env を source して渡す）:
  GOOGLE_SERVICE_ACCOUNT_FILE か GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID

使い方:
  set -a; . ./.env; set +a
  python3 scripts/setup_account.py --token-file ~/.config/threads-poster/token.tmp --account takumi_kojo_navi --add-test-post
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import (  # noqa: E402
    header_maps, is_outsourced, ACCOUNTS_FIELD_ALIASES, POSTS_FIELD_ALIASES, POSTS_TAB_PREFIX,
)

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


def upsert(ws, aliases: dict, key_internal: str, key_val: str, fields: dict) -> str:
    """内部キー(英語)で受けた fields を、シートの実見出し(日/英)に合わせて追記 or 更新。"""
    import gspread

    header = ws.row_values(1)
    to_internal, to_header = header_maps(header, aliases)
    col = {n: i + 1 for i, n in enumerate(header)}
    key_header = to_header.get(key_internal)
    if not key_header:
        raise SystemExit(f"見出しに『{key_internal}』に対応する列がありません: {header}")
    key_cells = ws.col_values(col[key_header])
    target = None
    for idx, val in enumerate(key_cells[1:], start=2):
        if str(val) == str(key_val):
            target = idx
            break
    if target is None:
        # 追記: 実見出しの並び順に、内部キー経由で値を並べる
        row = []
        for h in header:
            ik = to_internal.get(h)
            row.append(str(fields.get(ik, "") or "") if ik else "")
        ws.append_row(row, value_input_option="RAW")
        return "追記"
    cells = [
        gspread.Cell(target, col[to_header[ik]], "" if v is None else str(v))
        for ik, v in fields.items()
        if ik in to_header
    ]
    if cells:
        ws.update_cells(cells, value_input_option="RAW")
    return "更新"


VALID_ROLES = ("", "自社", "own", "外注", "outsourced")


def validate_options(role: str, add_test_post: bool) -> tuple[bool, str]:
    """登録オプションの妥当性を返す。(可否, 理由)。

    - 運用種別の打ち間違いを入口で弾く。is_outsourced() は未知の値を自社扱いにする
      （投稿が全停止しない方へ倒す設計）ため、「外注のつもりで打ち間違え」がここを
      通ると自社扱いで登録されてしまう。
    - 外注アカにテスト投稿を作らせない。外注アカは投稿タブを作らない運用のため。
    """
    r = str(role or "").strip()
    if r.lower() not in [v.lower() for v in VALID_ROLES]:
        return False, (f"運用種別 '{role}' は不正です。"
                       f"次のいずれかを指定してください: 自社 / 外注（空欄は自社扱い）")
    if is_outsourced({"role": r}) and add_test_post:
        return False, "外注アカウントにテスト投稿は作れません（投稿タブを作らない運用のため）"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", required=True)
    # 既定値は置かない: 付け忘れが既定アカウント（本番）の user_id/トークンを別人の値で
    # 上書きする事故になるため、必ず明示させる。
    ap.add_argument("--account", required=True)
    ap.add_argument("--add-test-post", action="store_true")
    ap.add_argument("--role", default="",
                    help="運用種別。外注アカウントは「外注」を指定（空欄=自社）")
    ap.add_argument("--sheet-id", default=None,
                    help="対象シートID（未指定なら環境変数 SPREADSHEET_ID）。"
                         "外注アカは相乗り先の事業シートIDを明示すること")
    args = ap.parse_args()

    ok, reason = validate_options(args.role, args.add_test_post)
    if not ok:
        print(f"中止: {reason}")
        return 1

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
    sheet_id = args.sheet_id or os.environ.get("SPREADSHEET_ID")
    if not sheet_id:
        print("中止: --sheet-id か環境変数 SPREADSHEET_ID が必要です")
        return 1
    sh = gc.open_by_key(sheet_id)
    ws_a = sh.worksheet("accounts")

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    acct = args.account

    # 3) accounts 追記/更新
    how = upsert(ws_a, ACCOUNTS_FIELD_ALIASES, "account", acct, {
        "account": acct,
        "user_id": user_id,
        "access_token": token,
        "token_updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "daily_count": "",
        "daily_count_date": "",
        "role": args.role,
    })
    print(f"OK accounts {how}  account={acct}")

    # 4) 任意: テスト投稿。アカウント別タブ「投稿_<account>」に入れる
    #    （無ければ後方互換で単一 "posts" タブにフォールバック）。
    if args.add_test_post:
        post_tab = f"{POSTS_TAB_PREFIX}{acct}"
        try:
            ws_p = sh.worksheet(post_tab)
        except gspread.exceptions.WorksheetNotFound:
            try:
                ws_p = sh.worksheet("posts")
            except gspread.exceptions.WorksheetNotFound:
                raise SystemExit(
                    f"テスト投稿先タブが見つかりません。先に "
                    f"`python3 scripts/setup_post_tab.py --account {acct}` でタブ『{post_tab}』を作成してください。"
                )
        dt = (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
        # row_id は「全タブで一意」が必須要件（CLAUDE.md §5）。固定 "T1" だと複数アカで重複し
        # 書き戻しが別アカの行に着地しうるため、アカウント名を含めて一意にする。
        test_row_id = f"T1-{acct}"
        how_p = upsert(ws_p, POSTS_FIELD_ALIASES, "row_id", test_row_id, {
            "row_id": test_row_id,
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
        })
        print(f"OK posts {how_p}  row_id={test_row_id}  post_datetime={dt}"
              f"（JST・2分前=即時公開対象。稼働中の10分cronが実投稿する点に注意）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
