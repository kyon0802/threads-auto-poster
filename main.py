"""
エントリーポイント。GitHub Actions / cron から実行される。
環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON : サービスアカウントJSON (文字列)
  SPREADSHEET_ID              : スプレッドシートID
  TZ_NAME                     : タイムゾーン (既定 Asia/Tokyo)
  MAX_POSTS_PER_DAY           : 1アカウント1日上限 (既定 50)
  TREE_REPLY_DELAY_SEC        : ツリー返信を空ける秒数 (既定 30)
  DRY_RUN                     : "1" なら実際には投稿しない
"""
import os
import json
import logging
import sys

from threads_poster.sheets import GoogleSheetStore
from threads_poster.publisher import Publisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")


def resolve_business_sheets(env) -> list[tuple[str, str]]:
    """処理対象の (事業名, スプレッドシートID) のリストを返す。

    - 多事業: 環境変数 BUSINESSES の JSON 配列
      例 [{"name":"seizogyo","spreadsheet_id":"..."},{"name":"uranai","spreadsheet_id":"..."}]
    - 単一(後方互換): BUSINESSES が無ければ SPREADSHEET_ID 単体
      （事業名は BUSINESS_NAME・既定 "default"）。
    どちらも無ければ空リスト（呼び出し側でエラー）。
    """
    raw = env.get("BUSINESSES")
    if raw and raw.strip():
        sheets = []
        for b in json.loads(raw):
            sid = b.get("spreadsheet_id") or b.get("id")
            if sid:
                sheets.append((b.get("name", "(no-name)"), sid))
        return sheets
    sid = env.get("SPREADSHEET_ID")
    if sid:
        return [(env.get("BUSINESS_NAME", "default"), sid)]
    return []


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    tz_name = os.environ.get("TZ_NAME", "Asia/Tokyo")
    max_per_day = int(os.environ.get("MAX_POSTS_PER_DAY", "50"))
    tree_delay = int(os.environ.get("TREE_REPLY_DELAY_SEC", "30"))
    dry_run = os.environ.get("DRY_RUN") == "1"
    sheets = resolve_business_sheets(os.environ)

    # キルスイッチ: Variable/Secret PAUSED=1 で投稿を即停止（生成・公開を含む全自動を止める）
    if os.environ.get("PAUSED") == "1":
        log.info("PAUSED=1：一時停止中のため投稿しません（キルスイッチ）")
        return 0

    if not sa_json or not sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と (BUSINESSES または SPREADSHEET_ID) が必要です")
        return 1

    sa_info = json.loads(sa_json)
    totals = {"posted": 0, "skipped": 0, "error": 0, "deferred": 0}
    failures = 0
    for name, sid in sheets:
        log.info("=== 事業 '%s' を処理 (sheet=%s…) ===", name, str(sid)[:10])
        try:
            store = GoogleSheetStore(sa_info, sid)
            pub = Publisher(store, tz_name=tz_name, max_posts_per_day=max_per_day,
                            tree_reply_delay_sec=tree_delay, dry_run=dry_run)
            res = pub.run()
            for k in totals:
                totals[k] += res.get(k, 0)
        except Exception as e:  # 1事業の失敗で他事業を止めない（run全体は失敗扱い→通知）
            failures += 1
            log.exception("事業 '%s' の処理に失敗: %s", name, e)

    log.info("完了(全事業合算): %s / 事業レベル失敗=%d", totals, failures)
    return 0 if (totals["error"] == 0 and failures == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
