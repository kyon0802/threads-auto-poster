#!/usr/bin/env python3
"""
既存スプレッドシートの見出しを日本語(正規)へ移行する（データ行は保持）。
- accounts / posts の1行目を、現在の列順を保ったまま日本語へ置換
- posts の「投稿日時」列を文字列書式に固定（Google の自動日付変換で崩れるのを防ぐ）
- 「記入例」タブ（通常投稿＋ツリー例）を作成/更新（コードは読まない見本タブ）

前提環境変数: GOOGLE_SERVICE_ACCOUNT_FILE/_JSON, SPREADSHEET_ID
使い方:
  set -a; . ./.env; set +a
  python3 scripts/migrate_headers_ja.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import (  # noqa: E402
    header_maps, canonical_headers, ACCOUNTS_FIELD_ALIASES, POSTS_FIELD_ALIASES,
)


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if p:
        with open(os.path.expanduser(p)) as f:
            return json.load(f)
    raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が見つかりません")


def col_letter(idx1: int) -> str:  # 1-based 列番号 -> A1 形式の列文字（<=26列）
    return chr(ord("A") + idx1 - 1)


def migrate_headers(ws, aliases: dict):
    """1行目を、現在の列順を保ったまま日本語の正規見出しへ置換。データ行は触らない。"""
    header = ws.row_values(1)
    to_internal, _ = header_maps(header, aliases)
    canon = {internal: names[0] for internal, names in aliases.items()}  # 内部キー -> 日本語
    new_header = [(canon.get(to_internal[h], h) if h in to_internal else h) for h in header]
    last = col_letter(len(new_header))
    ws.batch_update([{"range": f"A1:{last}1", "values": [new_header]}])
    return header, new_header


def main() -> int:
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        load_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    sh = gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"])
    ws_a = sh.worksheet("accounts")
    ws_p = sh.worksheet("posts")

    old_a, new_a = migrate_headers(ws_a, ACCOUNTS_FIELD_ALIASES)
    print(f"accounts 見出し:\n  旧: {old_a}\n  新: {new_a}")
    old_p, new_p = migrate_headers(ws_p, POSTS_FIELD_ALIASES)
    print(f"posts 見出し:\n  旧: {old_p}\n  新: {new_p}")

    # 「投稿日時」列を文字列書式に固定（"2026-06-16 21:00" が日付に化けるのを防ぐ）
    pheader = ws_p.row_values(1)
    if "投稿日時" in pheader:
        L = col_letter(pheader.index("投稿日時") + 1)
        ws_p.format(f"{L}2:{L}1000", {"numberFormat": {"type": "TEXT"}})
        print(f"posts『投稿日時』({L}列)を文字列書式に固定")

    # 「記入例」タブ（通常投稿1件＋ツリー3件）。コードは読まない見本。
    ph = canonical_headers(POSTS_FIELD_ALIASES)
    examples = [
        ph,
        ["例1",  "rk_riko2", "2026-06-16 21:00", "今日のひとこと。◯◯について話します。",     "TEXT", "", "",     "", "", "", "", ""],
        ["例T1", "rk_riko2", "2026-06-16 21:30", "①導入：◯◯で悩んでいませんか？",            "TEXT", "", "",     "", "", "", "", ""],
        ["例T2", "rk_riko2", "2026-06-16 21:31", "②本題：実は△△が効きます。",                "TEXT", "", "例T1", "", "", "", "", ""],
        ["例T3", "rk_riko2", "2026-06-16 21:32", "③締め：詳しくはプロフィールから。",          "TEXT", "", "例T2", "", "", "", "", ""],
    ]
    try:
        ex = sh.worksheet("記入例")
        ex.clear()
    except gspread.exceptions.WorksheetNotFound:
        ex = sh.add_worksheet(title="記入例", rows=50, cols=len(ph))
    ex.batch_update([{"range": f"A1:{col_letter(len(ph))}{len(examples)}", "values": examples}])
    print("『記入例』タブを作成/更新（通常投稿＋ツリー例）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
