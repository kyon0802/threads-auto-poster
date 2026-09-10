#!/usr/bin/env python3
"""登録済みアカウントとトークンの状態を確認する（読み取り専用・書込なし）。

「前回発行したトークンがまだ使えるのか」「どのシートに登録済みなのか」を確かめるためのもの。
長期トークンは60日有効なので、生きていれば認可からやり直す必要はない。

★トークンの値は絶対に表示しない（CLAUDE.md §17b）。表示するのは「あるか」「いつ更新したか」だけ。

使い方（シートIDは §17b によりコードに書かない・引数で渡す）:
  set -a; . ./.env; set +a
  python3 scripts/check_accounts.py --sheet-id <ID> [--sheet-id <ID2> ...]
  python3 scripts/check_accounts.py --sheet-id <ID> --verify   # 実際にAPIを叩いて生死を確認

--verify は Threads API の GET /me と投稿一覧を1回ずつ叩くだけ（読み取り専用・投稿しない）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.accounts_status import summarize_accounts  # noqa: E402
from threads_poster.sheets import with_retry  # noqa: E402

MARK = {"ok": "✓", "warn": "△", "expired": "×", "missing": "×", "unknown": "?"}
LABEL = {
    "ok": "使える",
    "warn": "まもなく失効",
    "expired": "失効済み（取り直しが必要）",
    "missing": "トークン未登録",
    "unknown": "更新日時が空（次回の投稿ジョブで自動補完される）",
}


def open_sheet(sheet_id: str):
    import gspread
    from google.oauth2.service_account import Credentials
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_file:
        info = json.load(open(os.path.expanduser(sa_file)))
    elif sa_json:
        info = json.loads(sa_json)
    else:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE か GOOGLE_SERVICE_ACCOUNT_JSON が必要です")
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return with_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id))


def verify_live(token: str) -> str:
    """トークンが実際に生きているか確認する（読み取りのみ）。結果の短い文字列を返す。"""
    import requests
    try:
        r = requests.get("https://graph.threads.net/v1.0/me",
                         params={"fields": "id,username", "access_token": token}, timeout=30)
        me = r.json()
    except Exception as e:  # noqa: BLE001 ネットワーク断など
        return f"確認できず（通信エラー: {type(e).__name__}）"
    if "id" not in me:
        # エラー本文にトークンは含まれないが、念のため message だけを拾う
        msg = (me.get("error") or {}).get("message", "")[:80]
        return f"使えない（{msg}）"
    # 収集に必要な権限があるかも確認する（投稿一覧を1件だけ取得）
    try:
        r2 = requests.get(f"https://graph.threads.net/v1.0/{me['id']}/threads",
                          params={"fields": "id", "limit": 1, "access_token": token}, timeout=30)
        ok_read = "data" in r2.json()
    except Exception:  # noqa: BLE001
        ok_read = False
    return (f"生きている（@{me.get('username', '?')}）"
            + ("・投稿一覧の取得OK" if ok_read else "・**投稿一覧が取得できない＝収集用の権限が不足**"))


def check(sheet_id: str, verify: bool) -> int:
    sh = open_sheet(sheet_id)
    print(f"\n=== シート: {sh.title}（ID先頭 {sheet_id[:8]}…）===")
    titles = [w.title for w in with_retry(sh.worksheets)]
    if "accounts" not in titles:
        print("  accounts タブがありません")
        return 0
    ws = with_retry(lambda: sh.worksheet("accounts"))
    rows = with_retry(ws.get_all_records)
    # 見出しが日本語/英語どちらでも読めるよう内部キーへ寄せる
    from threads_poster.sheets import header_maps, ACCOUNTS_FIELD_ALIASES
    to_internal, _ = header_maps(list(rows[0].keys()) if rows else [], ACCOUNTS_FIELD_ALIASES)
    norm = [{to_internal.get(k, k): v for k, v in r.items()} for r in rows]

    summary = summarize_accounts(norm)
    if not summary:
        print("  登録アカウントなし")
        return 0
    n_bad = 0
    for s in summary:
        kind = "外注" if s["is_outsourced"] else "自社"
        left = f"残り{s['days_left']}日" if s["days_left"] is not None else "残り不明"
        print(f"  {MARK[s['status']]} {s['account']:<22} [{kind}] {LABEL[s['status']]}・{left}")
        if not s["has_user_id"]:
            print("      ⚠ ユーザーIDが空（setup_account.py で登録し直してください）")
        if s["status"] in ("expired", "missing"):
            n_bad += 1
        if verify and s["has_token"]:
            token = ""
            for r in norm:
                if str(r.get("account")) == s["account"]:
                    token = str(r.get("access_token") or "")
            if token:
                print(f"      実機確認: {verify_live(token)}")
    return n_bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet-id", action="append", required=True,
                    help="確認するスプレッドシートID（複数指定可・公開repoに書かないこと）")
    ap.add_argument("--verify", action="store_true",
                    help="Threads API を実際に叩いてトークンの生死を確認する（読み取りのみ）")
    args = ap.parse_args()
    bad = 0
    for sid in args.sheet_id:
        bad += check(sid, args.verify)
    print("\n※ トークンの値は表示していません（§17b）。")
    if bad:
        print(f"※ {bad}件が失効/未登録です。該当アカウントだけ取り直してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
