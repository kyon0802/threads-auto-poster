#!/usr/bin/env python3
"""現行の単一シートから、事業別シートへ accounts行＋投稿タブを移管する（Phase 0・事業分離）。

OLD（全事業が同居する現行シート）→ DST（事業別シート）へ、指定アカウントの
  - accounts 行（トークン・日次カウント込みの完全コピー）
  - 投稿_<account> タブの全行（posted / 投稿後ID / 状態 を含め verbatim にミラー）
を移す。**冪等**＝再実行すると毎回 OLD の最新状態で DST を上書きする（重複しない）。

値はすべて RAW（文字列）で書く。理由＝17桁の user_id / 投稿後ID やトークンを
USER_ENTERED で書くと数値化されて桁落ち・破損する恐れがあるため。
DST の既定タブ「シート1 / Sheet1」は移管後に削除する。

注意: OLD は read-only（移管中も投稿は止まらない）。切替（ルーティング）は別ステップ。

使い方:
  GOOGLE_SERVICE_ACCOUNT_FILE=~/.config/threads-poster/service-account.json \
  python3 scripts/migrate_to_business_sheet.py --src <OLD_ID> --dst <DST_ID> --account <acc>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from string import ascii_uppercase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import (  # noqa: E402
    ACCOUNTS_FIELD_ALIASES, POSTS_FIELD_ALIASES,
    canonical_headers, per_account_post_headers, header_maps, POSTS_TAB_PREFIX,
)


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not p:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が必要です")
    with open(os.path.expanduser(p)) as f:
        return json.load(f)


def read_internal(ws, aliases) -> list[dict]:
    """ワークシートを「内部キー(英語)」の dict のリストで読む（見出しは日/英どちらでも可）。"""
    records = ws.get_all_records()
    if not records:
        return []
    to_internal, _ = header_maps(list(records[0].keys()), aliases)
    return [{to_internal.get(k, k): v for k, v in r.items()} for r in records]


def s(v) -> str:
    return "" if v is None else str(v)


def ensure_tab(sh, title: str, ncols: int, nrows: int):
    existing = {ws.title: ws for ws in sh.worksheets()}
    if title in existing:
        return existing[title]
    return sh.add_worksheet(title=title, rows=max(nrows, 20), cols=max(ncols, 12))


def write_block(sh, ws, headers: list[str], internal_order: list[str], records: list[dict]) -> int:
    """ヘッダ＋データ行を RAW(文字列) で書く。clear してから書くので冪等（全置換）。"""
    rows = [headers] + [[s(r.get(k, "")) for k in internal_order] for r in records]
    ws.clear()
    sh.values_update(f"'{ws.title}'!A1",
                     params={"valueInputOption": "RAW"},
                     body={"values": rows})
    return len(rows) - 1  # データ行数


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="現行（全事業同居）シートID")
    ap.add_argument("--dst", required=True, help="移管先（事業別）シートID")
    ap.add_argument("--account", required=True, help="移管するアカウント名")
    args = ap.parse_args()

    import gspread
    from google.oauth2.service_account import Credentials
    gc = gspread.authorize(Credentials.from_service_account_info(
        load_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"]))
    src = gc.open_by_key(args.src)
    dst = gc.open_by_key(args.dst)
    print(f"=== 移管: '{src.title}' → '{dst.title}'  (account={args.account}) ===")

    # --- accounts（指定アカウントの1行だけ） ---
    acc_headers = canonical_headers(ACCOUNTS_FIELD_ALIASES)
    acc_order = list(ACCOUNTS_FIELD_ALIASES.keys())
    src_accounts = read_internal(src.worksheet("accounts"), ACCOUNTS_FIELD_ALIASES)
    match = [a for a in src_accounts if str(a.get("account")) == args.account]
    if not match:
        raise SystemExit(f"OLD の accounts に '{args.account}' が見つかりません")
    acc_ws = ensure_tab(dst, "accounts", len(acc_headers), 10)
    n_acc = write_block(dst, acc_ws, acc_headers, acc_order, match)

    # --- 投稿_<account>（全行を verbatim ミラー） ---
    post_headers = per_account_post_headers()
    post_order = [k for k in POSTS_FIELD_ALIASES if k != "account"]
    post_title = f"{POSTS_TAB_PREFIX}{args.account}"
    src_posts = read_internal(src.worksheet(post_title), POSTS_FIELD_ALIASES)
    post_ws = ensure_tab(dst, post_title, len(post_headers), len(src_posts) + 20)
    n_post = write_block(dst, post_ws, post_headers, post_order, src_posts)
    if "投稿日時" in post_headers:  # 文字列書式に固定（手編集時の自動日付変換を防ぐ）
        L = ascii_uppercase[post_headers.index("投稿日時")]
        post_ws.format(f"{L}2:{L}1000", {"numberFormat": {"type": "TEXT"}})

    # --- 既定タブ「シート1 / Sheet1」を削除 ---
    for ws in dst.worksheets():
        if ws.title in ("シート1", "Sheet1") and len(dst.worksheets()) > 1:
            dst.del_worksheet(ws)
            print(f"  既定タブ '{ws.title}' を削除")
            break

    posted = sum(1 for r in src_posts if str(r.get("status") or "").lower() == "posted")
    queued = sum(1 for r in src_posts if str(r.get("status") or "").lower() in ("", "queued"))
    print(f"  ✓ accounts={n_acc}行 / {post_title}={n_post}行 "
          f"(posted={posted}, queued/空={queued}) を移管完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
