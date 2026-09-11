"""全投稿データを1枚のスプレッドシートへ書き出すエントリ（export.yml から日次実行）。

なぜ必要か（2026-09-11）:
投稿データはアカウントごと・事業ごとにシートが分かれていて、しかもインサイトは日次スナップショットで
積まれるため、人が「全アカの投稿を月別に眺める」ことができなかった。毎回こちらで集計して
ファイルを作るのは続かないので、**1枚のシートを毎日上書きする**形にする。

出力先は Secret `EXPORT_SHEET_ID`（オーナーが作ってサービスアカウントに共有したシート）。
サービスアカウントは Drive の容量を持てずファイルを新規作成できないため、
**シートの器は人が作り、中身はこのジョブが埋める**という分担にしている。

タブ構成:
  読み方          … 列の意味と注意（人向け）
  サマリ_月別      … アカウント×月の集計（中央値が主指標）
  <アカウント名>   … そのアカウントの全投稿（1行1投稿）
  全投稿          … 全アカを1枚に（ピボット用）

読み取り専用の集計ジョブなので、投稿・生成・課金は一切しない。

環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON / BUSINESSES または SPREADSHEET_ID（投稿系と共通ルーティング）
  EXPORT_SHEET_ID     … 書き出し先シートID（必須）
  EXPORT_EXTRA_SHEETS … 閲覧用にだけ含める読み取り専用シート（任意・BUSINESSES と同じJSON形式）。
                        廃止アカウントの過去データを記録として残すために使う
  DRY_RUN         … "1" で書き込まず件数だけ出す
"""
import json
import logging
import os
import sys

from threads_poster.sheets import (
    GoogleSheetStore, INSIGHTS_TAB_PREFIX, POSTS_TAB_PREFIX, with_retry,
)
from threads_poster.export import COLUMNS, build_rows, monthly_summary
from main import resolve_business_sheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main_export")

SUMMARY_HEADERS = ["アカウント", "月", "投稿本数", "表示 合計", "表示 中央値（主）", "表示 平均",
                   "表示0の本数", "表示5以下", "いいね", "返信", "本文あり"]
SUMMARY_KEYS = ["account", "month", "posts", "views_total", "views_median", "views_mean",
                "zero_views", "low_views", "likes", "replies", "with_text"]

README = [
    ["Threads 全投稿データ（自動更新）", ""],
    ["", ""],
    ["更新", "毎日 05:00 JST に自動で上書きされます（手動実行も可）"],
    ["作り方", "GitHub Actions の export.yml が各事業シートを読んでここへ書き出します"],
    ["", ""],
    ["タブの見方", ""],
    ["サマリ_月別", "アカウント×月の集計。まずここを見る"],
    ["各アカウント名", "そのアカウントの全投稿。1行=1投稿。月・時でフィルタできる"],
    ["全投稿", "全アカを1枚に。ピボットテーブル用"],
    ["", ""],
    ["列の意味", ""],
    ["表示", "その投稿が画面に表示された回数（Threadsのインサイト値・更新時点の累積）"],
    ["本文の取得元", "API収集=Threads APIから取得／投稿タブ=こちらが投稿した原稿から復元"],
    ["", ""],
    ["注意", ""],
    ["1", "表示数は累積値。新しい投稿ほど不利なので、月をまたぐ比較は中央値で見ること"],
    ["2", "本文列は2026-09-11に追加したため、それ以前に収集を終えた古い投稿は"],
    ["", "投稿タブから復元している。復元できなかった行は本文が空になる"],
    ["3", "外注アカは投稿タブを持たないため、本文はAPI収集分のみ"],
    ["", ""],
    ["このタブは毎回作り直されます", "書き込んでも次の更新で消えます"],
]


def resolve_export_sheets(env) -> list[tuple[str, str]]:
    """書き出し対象の (事業名, シートID) を返す。

    通常の事業シート（BUSINESSES / SPREADSHEET_ID）に加えて、
    **閲覧用シートにだけ含めたい読み取り専用シート**を `EXPORT_EXTRA_SHEETS` で足せる。

    なぜ分けるか: 廃止したアカウントのシートを BUSINESSES に入れると
    post/weekly/monitor まで動き出してしまう。過去データは記録として残したいので、
    エクスポートだけが見る経路を用意する。指定が壊れていても通常分の書き出しは止めない。
    """
    sheets = list(resolve_business_sheets(env))
    raw = (env.get("EXPORT_EXTRA_SHEETS") or "").strip()
    if raw:
        try:
            for b in json.loads(raw):
                sid = b.get("spreadsheet_id") or b.get("id")
                if sid:
                    sheets.append((b.get("name", "(extra)"), sid))
        except Exception as e:  # noqa: BLE001 追加分の失敗で本体を止めない
            log.warning("EXPORT_EXTRA_SHEETS を読めませんでした（無視して継続）: %s", e)
    seen, out = set(), []
    for name, sid in sheets:      # 同じシートを二重に読むと投稿が二重に出る
        if sid not in seen:
            seen.add(sid); out.append((name, sid))
    return out


def collect_all(sheets, sa_info) -> tuple[dict, int]:
    """全事業から (アカウント名 -> エクスポート行) を集める。(結果, 事業レベル失敗数)。"""
    by_account, failures = {}, 0
    for name, sid in sheets:
        try:
            store = GoogleSheetStore(sa_info, sid)
            book = store.sh
            titles = [w.title for w in with_retry(book.worksheets)]
            # 投稿タブ（自社が書いた原稿）から 投稿後ID -> 本文 を作る（本文補完用）
            posts_by_id = {}
            for t in titles:
                if not t.startswith(POSTS_TAB_PREFIX):
                    continue
                ws = with_retry(lambda t=t: book.worksheet(t))
                for r in with_retry(ws.get_all_records):
                    pid = str(r.get("投稿後ID") or r.get("posted_id") or "").strip()
                    if pid:
                        posts_by_id[pid] = {
                            "text": r.get("本文") or r.get("text") or "",
                            "row_id": r.get("投稿ID") or r.get("row_id") or "",
                            "hook_type": r.get("フック型") or "",
                            "content_type": r.get("内容型") or "",
                        }
            for t in titles:
                if not t.startswith(INSIGHTS_TAB_PREFIX) or "分析" in t:
                    continue
                acc = t[len(INSIGHTS_TAB_PREFIX):]
                ws = with_retry(lambda t=t: book.worksheet(t))
                recs = with_retry(ws.get_all_records)
                rows = build_rows(acc, [{
                    "posted_id": r.get("投稿後ID") or r.get("posted_id"),
                    "post_datetime": r.get("投稿日時") or r.get("post_datetime"),
                    "snapshot_date": r.get("取得日") or r.get("snapshot_date"),
                    "text": r.get("本文") or r.get("text"),
                    "views": r.get("表示回数") or r.get("views"),
                    "likes": r.get("いいね") or r.get("likes"),
                    "replies": r.get("返信") or r.get("replies"),
                    "reposts": r.get("リポスト") or r.get("reposts"),
                    "quotes": r.get("引用") or r.get("quotes"),
                    "shares": r.get("シェア") or r.get("shares"),
                    "permalink": r.get("permalink") or r.get("リンク"),
                } for r in recs], posts_by_id)
                if rows:
                    by_account[acc] = rows
                    log.info("%s: %d本", acc, len(rows))
        except Exception as e:  # noqa: BLE001 1事業の失敗で他事業を止めない
            failures += 1
            log.exception("事業 '%s' の読み取りに失敗: %s", name, e)
    return by_account, failures


def write_sheet(sheet_id: str, sa_info: dict, by_account: dict) -> None:
    """書き出し先シートを作り直す（タブ単位で全置換＝毎回同じ形になる）。"""
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    book = with_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id))

    def put(title: str, values: list[list], freeze_header: bool = True) -> None:
        """タブを用意して全置換する。既存タブは中身だけ消して作り直す。"""
        try:
            ws = with_retry(lambda: book.worksheet(title))
            with_retry(ws.clear)
        except gspread.WorksheetNotFound:
            ws = with_retry(lambda: book.add_worksheet(
                title, rows=max(len(values) + 10, 100), cols=max(len(values[0]) if values else 5, 5)))
        if values:
            with_retry(lambda: ws.update(values, value_input_option="RAW"))
            if freeze_header:
                with_retry(lambda: ws.freeze(rows=1))
        log.info("  タブ '%s' に %d行 書き込み", title, len(values))

    all_rows = [r for rows in by_account.values() for r in rows]
    all_rows.sort(key=lambda r: (r["account"], r["post_datetime"]))

    put("読み方", README, freeze_header=False)
    put("サマリ_月別", [SUMMARY_HEADERS] + [[s[k] for k in SUMMARY_KEYS]
                                          for s in monthly_summary(all_rows)])
    for acc in sorted(by_account):
        put(acc[:99], [[h for h, _ in COLUMNS]] + [[r[k] for _, k in COLUMNS]
                                                   for r in by_account[acc]])
    put("全投稿", [["アカウント"] + [h for h, _ in COLUMNS]]
        + [[r["account"]] + [r[k] for _, k in COLUMNS] for r in all_rows])


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("EXPORT_SHEET_ID")
    dry_run = os.environ.get("DRY_RUN") == "1"
    sheets = resolve_export_sheets(os.environ)
    if not sa_json or not sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と (BUSINESSES または SPREADSHEET_ID) が必要です")
        return 1
    if not sheet_id:
        log.error("EXPORT_SHEET_ID が未設定です（書き出し先シートを作って"
                  "サービスアカウントに編集権限で共有し、そのIDを Secret に入れてください）")
        return 1

    sa_info = json.loads(sa_json)
    by_account, failures = collect_all(sheets, sa_info)
    total = sum(len(v) for v in by_account.values())
    if not by_account:
        log.error("書き出す投稿が1本もありません（収集が動いているか確認してください）")
        return 2

    if dry_run:
        log.info("[dry-run] %dアカウント・%d本（書込なし）", len(by_account), total)
        return 0
    try:
        write_sheet(sheet_id, sa_info, by_account)
    except Exception as e:  # noqa: BLE001
        log.exception("書き出しに失敗: %s", e)
        return 2
    log.info("完了: %dアカウント・%d本を書き出し / 事業レベル失敗=%d",
             len(by_account), total, failures)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
