"""外注アカウント運用に必要な列を既存シートへ追加する（冪等・DRY-RUN既定）。

追加するもの:
  1. accounts タブ … 「運用種別」列（空＝自社 / 「外注」＝外注アカ）
  2. インサイト_<acc> タブ … 「本文」列（外注の使い回し率の算出に必要）

どちらも見出し行の右端に足すだけで、既存の列とデータは一切動かさない。

使い方（シートIDは §17b によりコードに書かない・引数で渡す）:
  DRY-RUN:  python3 scripts/add_vendor_columns.py --sheet-id <ID> [--sheet-id <ID2> ...]
  実書込:   python3 scripts/add_vendor_columns.py --sheet-id <ID> --apply

認証: .env の GOOGLE_SERVICE_ACCOUNT_FILE（scripts/local_run.sh と同じ流儀）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.sheets import INSIGHTS_TAB_PREFIX, with_retry  # noqa: E402

ACCOUNTS_TAB = "accounts"
ACCOUNTS_NEW_COLUMNS = ["運用種別"]
INSIGHTS_NEW_COLUMNS = ["本文"]


def open_sheet(sheet_id: str):
    import gspread
    from google.oauth2.service_account import Credentials
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_file:
        info = json.load(open(sa_file))
    elif sa_json:
        info = json.loads(sa_json)
    else:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE か GOOGLE_SERVICE_ACCOUNT_JSON が必要です")
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return with_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id))


def gspread_col(n: int) -> str:
    """1始まりの列番号→A1表記の列文字（1→A, 27→AA）。"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def add_columns(ws, new_columns: list[str], label: str, apply: bool) -> None:
    """見出し行の右端に不足列だけ追記する（既存列は動かさない＝データ非破壊）。"""
    header = with_retry(lambda: ws.row_values(1))
    missing = [c for c in new_columns if c not in header]
    if not missing:
        print(f"  {label}: 列あり（スキップ）")
        return
    print(f"  {label}: 追加 {missing}" + ("" if apply else "（DRY-RUN）"))
    if not apply:
        return
    # 既存タブはグリッド幅=列数ちょうどで作られていることが多く、右端追記が
    # exceeds grid limits で落ちる。先に必要幅までグリッドを拡張する。
    need = len(header) + len(missing)
    if ws.col_count < need:
        with_retry(lambda: ws.add_cols(need - ws.col_count))
    with_retry(lambda: ws.update([[*missing]],
                                 range_name=f"{gspread_col(len(header) + 1)}1",
                                 value_input_option="RAW"))


def migrate(sheet_id: str, apply: bool) -> None:
    sh = open_sheet(sheet_id)
    tabs = {w.title: w for w in with_retry(sh.worksheets)}
    insight_tabs = [t for t in tabs if t.startswith(INSIGHTS_TAB_PREFIX)]
    print(f"--- sheet={sheet_id[:8]}… インサイトタブ={len(insight_tabs)}件 ---")

    if ACCOUNTS_TAB in tabs:
        add_columns(tabs[ACCOUNTS_TAB], ACCOUNTS_NEW_COLUMNS, ACCOUNTS_TAB, apply)
    else:
        print(f"  {ACCOUNTS_TAB}: タブが見つかりません（要確認）")

    for title in sorted(insight_tabs):
        add_columns(tabs[title], INSIGHTS_NEW_COLUMNS, title, apply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet-id", action="append", required=True,
                    help="対象スプレッドシートID（複数指定可・公開repoに書かないこと）")
    ap.add_argument("--apply", action="store_true", help="実際に書き込む（既定はDRY-RUN）")
    args = ap.parse_args()
    for sid in args.sheet_id:
        migrate(sid, args.apply)
    print("完了" + ("" if args.apply else "（DRY-RUN・書込なし。--apply で実行）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
