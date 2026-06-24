"""インサイト収集のエントリーポイント（GitHub Actions: collect.yml から日次実行）。

投稿系 main.py と同じ環境変数（GOOGLE_SERVICE_ACCOUNT_JSON / BUSINESSES または SPREADSHEET_ID）を使い、
同じ多事業ルーティングで事業をループする。**読み取り専用**＝投稿は一切しない。

環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON : サービスアカウントJSON (文字列)
  BUSINESSES / SPREADSHEET_ID : 収集対象シート（main.py と共通の resolve_business_sheets）
  TZ_NAME                     : 既定 Asia/Tokyo
  LOOKBACK_DAYS               : 何日前までの投稿を収集対象にするか（既定 60）
  DRY_RUN                     : "1" なら書き込まない（疎通確認）
"""
import os
import json
import logging
import sys

from threads_poster.sheets import GoogleSheetStore
from threads_poster.collector import Collector
from main import resolve_business_sheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main_collect")


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    tz_name = os.environ.get("TZ_NAME", "Asia/Tokyo")
    dry_run = os.environ.get("DRY_RUN") == "1"
    lookback = int(os.environ.get("LOOKBACK_DAYS", "60"))
    sheets = resolve_business_sheets(os.environ)

    if not sa_json or not sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と (BUSINESSES または SPREADSHEET_ID) が必要です")
        return 1

    sa_info = json.loads(sa_json)
    totals = {"accounts": 0, "media": 0, "errors": 0, "skipped_old": 0}
    failures = 0
    for name, sid in sheets:
        log.info("=== 事業 '%s' のインサイト収集 (sheet=%s…) ===", name, str(sid)[:10])
        try:
            store = GoogleSheetStore(sa_info, sid)
            res = Collector(store, tz_name=tz_name, dry_run=dry_run, lookback_days=lookback).run()
            for k in totals:
                totals[k] += res.get(k, 0)
            # 何も収集できず error だけ＝トークン/スコープ等の systemic 失敗 → loud fail（通知）。
            if res["accounts"] == 0 and res["media"] == 0 and res["errors"] > 0:
                failures += 1
                log.error("事業 '%s': 何も収集できず error のみ（threads_manage_insights / トークンを要確認）", name)
        except Exception as e:  # noqa: BLE001  1事業の失敗で他事業を止めない
            failures += 1
            log.exception("事業 '%s' の収集に失敗: %s", name, e)

    log.info("完了(全事業合算): %s / 事業レベル失敗=%d", totals, failures)
    # 個別メディアの error は許容（best-effort）。事業レベル失敗のみ run を失敗扱いにして通知する。
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
