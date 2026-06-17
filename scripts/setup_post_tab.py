#!/usr/bin/env python3
"""アカウント別の投稿タブ「投稿_<account>」を用意する（複数アカウント運用）。

- 既存 'posts' タブがあり --from-posts 指定時: それをリネームして流用
- 無ければ新規作成
- 見出しをアカウント別スキーマ（「アカウント」列なし＝タブ名で判別）に設定
- 「投稿日時」列を文字列書式に固定
- --update-examples 指定時: 「記入例」タブをアカウント別スキーマで作り直す

安全装置: リネーム元タブにデータ行があると列ずれの恐れがあるため自動上書きしない（中止）。
前提環境変数: GOOGLE_SERVICE_ACCOUNT_FILE/_JSON, SPREADSHEET_ID
使い方:
  set -a; . ./.env; set +a
  python3 scripts/setup_post_tab.py --account takumi_kojo_navi --from-posts --update-examples
  python3 scripts/setup_post_tab.py --account second_acc   # 2アカ目以降は新規作成
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from string import ascii_uppercase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import per_account_post_headers, POSTS_TAB_PREFIX  # noqa: E402


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if p:
        with open(os.path.expanduser(p)) as f:
            return json.load(f)
    raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が見つかりません")


def col_letter(i1: int) -> str:
    return ascii_uppercase[i1 - 1]


def has_data(ws) -> bool:
    vals = ws.get_all_values()
    return len(vals) > 1 and any(any(c.strip() for c in row) for row in vals[1:])


def set_post_tab(ws, headers):
    """ヘッダをアカウント別スキーマに整える（データ行が無い前提でクリア→ヘッダ設定）。"""
    if has_data(ws):
        raise SystemExit(f"タブ『{ws.title}』にデータ行があります。列ずれ防止のため自動整形を中止しました。")
    ws.clear()
    last = col_letter(len(headers))
    ws.batch_update([{"range": f"A1:{last}1", "values": [headers]}])
    if "投稿日時" in headers:
        L = col_letter(headers.index("投稿日時") + 1)
        ws.format(f"{L}2:{L}1000", {"numberFormat": {"type": "TEXT"}})


def update_examples(sh, headers):
    """「記入例」タブをアカウント別スキーマ（通常投稿＋ツリー例）で作り直す。"""
    import gspread

    rows = [
        headers,
        ["例1",  "2026-06-16 21:00", "今日のひとこと。◯◯について話します。", "TEXT", "", "",     "", "", "", "", ""],
        ["例T1", "2026-06-16 21:30", "①導入：◯◯で悩んでいませんか？",        "TEXT", "", "",     "", "", "", "", ""],
        ["例T2", "2026-06-16 21:31", "②本題：実は△△が効きます。",            "TEXT", "", "例T1", "", "", "", "", ""],
        ["例T3", "2026-06-16 21:32", "③締め：詳しくはプロフィールから。",      "TEXT", "", "例T2", "", "", "", "", ""],
    ]
    try:
        ex = sh.worksheet("記入例")
        ex.clear()
    except gspread.exceptions.WorksheetNotFound:
        ex = sh.add_worksheet(title="記入例", rows=50, cols=len(headers))
    ex.batch_update([{"range": f"A1:{col_letter(len(headers))}{len(rows)}", "values": rows}])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--from-posts", action="store_true", help="既存 'posts' タブをリネームして流用")
    ap.add_argument("--update-examples", action="store_true", help="記入例タブも作り直す")
    args = ap.parse_args()

    import gspread
    from google.oauth2.service_account import Credentials

    sh = gspread.authorize(
        Credentials.from_service_account_info(load_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    ).open_by_key(os.environ["SPREADSHEET_ID"])

    title = f"{POSTS_TAB_PREFIX}{args.account}"
    headers = per_account_post_headers()
    titles = {ws.title: ws for ws in sh.worksheets()}

    if title in titles:
        ws = titles[title]
        print(f"既存タブ『{title}』を使用")
    elif args.from_posts and "posts" in titles:
        ws = titles["posts"]
        ws.update_title(title)
        print(f"'posts' を『{title}』にリネーム")
    else:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        print(f"タブ『{title}』を新規作成")

    set_post_tab(ws, headers)
    print(f"見出し設定＋投稿日時を文字列書式化: {headers}")

    if args.update_examples:
        update_examples(sh, headers)
        print("「記入例」タブをアカウント別スキーマで作り直し")
    return 0


if __name__ == "__main__":
    sys.exit(main())
