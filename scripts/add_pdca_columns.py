"""投稿タブへPDCA型ラベル3列を追加し、お手本DB_<acc>・仮説ログ タブを作成する（冪等）。

使い方（シートIDは§17bによりコードに書かない・引数で渡す）:
  DRY-RUN:  python3 scripts/add_pdca_columns.py --sheet-id <ID> [--sheet-id <ID2> ...]
  実書込:   python3 scripts/add_pdca_columns.py --sheet-id <ID> --apply

認証: .env の GOOGLE_SERVICE_ACCOUNT_FILE（scripts/local_run.sh と同じ流儀）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.sheets import (  # noqa: E402
    EXEMPLAR_FIELD_ALIASES, EXEMPLAR_TAB_PREFIX, HYPOTHESIS_HEADER, HYPOTHESIS_TAB,
    POSTS_TAB_PREFIX, with_retry,
)

NEW_POST_COLUMNS = ["フック型", "内容型", "参照お手本ID"]
EXEMPLAR_HEADER = [names[0] for names in EXEMPLAR_FIELD_ALIASES.values()]


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


def migrate(sheet_id: str, apply: bool) -> None:
    sh = open_sheet(sheet_id)
    tabs = {w.title: w for w in with_retry(sh.worksheets)}
    accounts = [t[len(POSTS_TAB_PREFIX):] for t in tabs if t.startswith(POSTS_TAB_PREFIX)]
    print(f"--- sheet={sheet_id[:8]}… 投稿タブ={len(accounts)}件 ---")

    for acc in accounts:
        ws = tabs[f"{POSTS_TAB_PREFIX}{acc}"]
        header = with_retry(lambda: ws.row_values(1))
        missing = [c for c in NEW_POST_COLUMNS if c not in header]
        if not missing:
            print(f"  投稿_{acc}: 3列あり（スキップ）")
        else:
            print(f"  投稿_{acc}: 追加 {missing}" + ("" if apply else "（DRY-RUN）"))
            if apply:
                # 見出し行の右端に不足列だけ追記（既存列は動かさない＝データ非破壊）
                with_retry(lambda: ws.update(
                    [[*missing]],
                    range_name=f"{gspread_col(len(header) + 1)}1",
                    value_input_option="RAW"))

    for acc in accounts:
        title = f"{EXEMPLAR_TAB_PREFIX}{acc}"
        if title in tabs:
            print(f"  {title}: あり（スキップ）")
        else:
            print(f"  {title}: 作成" + ("" if apply else "（DRY-RUN）"))
            if apply:
                ws = with_retry(lambda: sh.add_worksheet(title, rows=200, cols=len(EXEMPLAR_HEADER)))
                with_retry(lambda: ws.append_row(EXEMPLAR_HEADER, value_input_option="RAW"))

    if HYPOTHESIS_TAB in tabs:
        print(f"  {HYPOTHESIS_TAB}: あり（スキップ）")
    else:
        print(f"  {HYPOTHESIS_TAB}: 作成" + ("" if apply else "（DRY-RUN）"))
        if apply:
            ws = with_retry(lambda: sh.add_worksheet(HYPOTHESIS_TAB, rows=500, cols=len(HYPOTHESIS_HEADER)))
            with_retry(lambda: ws.append_row(HYPOTHESIS_HEADER, value_input_option="RAW"))


def gspread_col(n: int) -> str:
    """1始まりの列番号→A1表記の列文字（1→A, 27→AA）。"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


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
