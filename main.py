"""
エントリーポイント。GitHub Actions / cron から実行される。
環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON : サービスアカウントJSON (文字列)
  SPREADSHEET_ID              : スプレッドシートID
  TZ_NAME                     : タイムゾーン (既定 Asia/Tokyo)
  MAX_POSTS_PER_DAY           : 1アカウント1日上限 (既定 50)
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


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    tz_name = os.environ.get("TZ_NAME", "Asia/Tokyo")
    max_per_day = int(os.environ.get("MAX_POSTS_PER_DAY", "50"))
    dry_run = os.environ.get("DRY_RUN") == "1"

    if not sa_json or not spreadsheet_id:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と SPREADSHEET_ID が必要です")
        return 1

    store = GoogleSheetStore(json.loads(sa_json), spreadsheet_id)
    pub = Publisher(store, tz_name=tz_name, max_posts_per_day=max_per_day, dry_run=dry_run)
    res = pub.run()
    log.info("完了: %s", res)
    return 0 if res["error"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
