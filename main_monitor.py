"""投稿在庫（ランウェイ）の日次監視エントリ（monitor.yml から実行）。

★なぜ必要か（2026-07-28の障害）
投稿ワークフローは「公開対象0件」でも成功で終わるため、生成が止まって在庫が尽きても
Actions は緑のまま回り続ける。実際に4アカウント全てが9〜19日間停止していたのに、
停止後も約1,290回「成功」し続けて誰も気づけなかった。

そこで監視の対象を「ジョブが落ちたか」から **「投稿が出せる在庫があるか」** に移す。
このワークフローはシートを読むだけ（書込なし・AI不使用・課金なし）で、
在庫ゼロや残りわずかを検知したらメールで知らせ、run 自体も失敗させる
（Actions一覧が赤になるのも二重のシグナルになる。正常なら緑のまま）。

環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON / BUSINESSES または SPREADSHEET_ID（投稿系と共通ルーティング）
  RUNWAY_WARN_DAYS   … 残り何日を切ったら警告か（既定2）
  ENABLE_EMAIL / MAIL_USERNAME / MAIL_PASSWORD / MAIL_TO / MAIL_TO_<NAME>
  TZ_NAME            … 既定 Asia/Tokyo
"""
import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from threads_poster.sheets import GoogleSheetStore
from threads_poster.inventory import summarize
from threads_poster.html_report import build_inventory_alert
from threads_poster.mailer import send_html
from main import resolve_business_sheets
from main_weekly import POSTS_PER_DAY, recipients_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main_monitor")


def collect(sheets, sa_info, now, warn_days: int) -> tuple[list[dict], int]:
    """全事業の在庫状況を集める。(行, 事業レベルの失敗数) を返す。"""
    rows, failures = [], 0
    for name, sid in sheets:
        try:
            store = GoogleSheetStore(sa_info, sid)
            accounts = [a["account"] for a in store.get_accounts() if a.get("account")]
            posts = store.get_posts()
        except Exception as e:  # noqa: BLE001 1事業の失敗で他事業の監視を止めない
            failures += 1
            log.exception("事業 '%s' の在庫確認に失敗: %s", name, e)
            continue
        for r in summarize(posts, accounts, now=now, posts_per_day=POSTS_PER_DAY):
            r["business"] = name
            rows.append(r)
    order = {"critical": 0, "warning": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["severity"]], r["days_left"]))
    return rows, failures


def send_alerts(rows, gen_date, env) -> int:
    """深刻度に応じてアラートメールを送る。送信失敗数を返す。

    宛先は週次レポートと同じ事業別ルーティング（MAIL_TO_<NAME> → MAIL_TO）。
    在庫が全て正常なら送らない（毎日の無害メールで通知が麻痺するのを避ける）。
    """
    if env.get("ENABLE_EMAIL") != "1":
        return 0
    bad = [r for r in rows if r["severity"] != "ok"]
    if not bad:
        log.info("全アカウント正常のためメールは送りません")
        return 0
    user, pw = env.get("MAIL_USERNAME"), env.get("MAIL_PASSWORD")
    if not (user and pw):
        log.error("ENABLE_EMAIL=1 だが MAIL_USERNAME/MAIL_PASSWORD 未設定のため送信できません")
        return 1
    default_to = env.get("MAIL_TO") or user
    # 宛先ごとに1通にまとめる（同じ人に事業数ぶん届かないように）
    by_to: dict[str, list[dict]] = {}
    for r in rows:
        by_to.setdefault(recipients_for(r["business"], env, default_to), []).append(r)

    failed = 0
    for to, group in by_to.items():
        n_crit = sum(1 for r in group if r["severity"] == "critical")
        if not any(r["severity"] != "ok" for r in group):
            continue  # この宛先の担当分は全て正常
        subject = (f"【要確認】Threads投稿在庫アラート｜"
                   + (f"{n_crit}アカウント在庫ゼロ" if n_crit else "在庫が残りわずか")
                   + f"（{gen_date}）")
        try:
            send_html(user, pw, f"Threads在庫監視 <{user}>", to, subject,
                      build_inventory_alert(group, gen_date))
            log.info("アラート送信OK: %s → %s", [r["account"] for r in group], to)
        except Exception:  # noqa: BLE001 1通の失敗で他を止めない
            failed += 1
            log.exception("アラート送信失敗: %s", to)
    return failed


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    tz_name = os.environ.get("TZ_NAME", "Asia/Tokyo")
    warn_days = int(os.environ.get("RUNWAY_WARN_DAYS", "2"))
    sheets = resolve_business_sheets(os.environ)
    if not sa_json or not sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と (BUSINESSES または SPREADSHEET_ID) が必要です")
        return 1

    now = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    gen_date = now.strftime("%Y-%m-%d %H:%M")
    rows, failures = collect(sheets, json.loads(sa_json), now, warn_days)

    for r in rows:
        level = {"critical": log.error, "warning": log.warning, "ok": log.info}[r["severity"]]
        level("[%s] %s", r["business"], r["message"])

    failures += send_alerts(rows, gen_date, os.environ)

    n_crit = sum(1 for r in rows if r["severity"] == "critical")
    n_warn = sum(1 for r in rows if r["severity"] == "warning")
    log.info("在庫監視 完了: 対象%dアカウント / 在庫ゼロ%d / 残りわずか%d / 事業レベル失敗%d",
             len(rows), n_crit, n_warn, failures)
    # 在庫ゼロがある間は run を失敗させ、Actions一覧でも赤く見えるようにする
    # （メールが埋もれても気づけるように二重化する。正常に戻れば緑に戻る）。
    return 2 if (n_crit or failures) else 0


if __name__ == "__main__":
    sys.exit(main())
