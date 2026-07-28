"""在庫ランウェイ監視（main_monitor / inventory.summarize / アラートHTML）の検証。

背景（2026-07-28）: 投稿ワークフローは在庫ゼロでも成功で終わるため、4アカウントが
9〜19日停止していたのに Actions は緑のまま約1,290回回り続けた。監視の対象を
「ジョブが落ちたか」から「投稿が出せる在庫があるか」へ移すのがこのモジュール。

実行: python3 -m pytest tests/test_monitor.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime  # noqa: E402

from threads_poster.inventory import runway_severity, summarize  # noqa: E402
from threads_poster.html_report import build_inventory_alert  # noqa: E402

NOW = datetime(2026, 7, 28, 13, 0)


def p(acc, dt, status="queued"):
    return {"row_id": f"{acc}-{dt}", "account": acc, "post_datetime": dt, "status": status}


def test_severity_levels():
    assert runway_severity({"pending": 0, "days_left": 0, "overdue": 0}) == "critical"
    assert runway_severity({"pending": 5, "days_left": 1, "overdue": 0}) == "warning"
    assert runway_severity({"pending": 5, "days_left": 9, "overdue": 3}) == "warning"  # 滞留あり
    assert runway_severity({"pending": 12, "days_left": 3, "overdue": 0}) == "ok"
    print("  ✓ 深刻度の判定（在庫ゼロ=critical / 残りわずか・滞留=warning）OK")


def test_summarize_sorts_most_severe_first():
    posts = [
        p("healthy", "2026-08-05 12:00"), p("healthy", "2026-08-04 12:00"),
        p("low", "2026-07-29 12:00"),
        p("dead", "2026-07-10 12:00", "posted"),
    ]
    rows = summarize(posts, ["healthy", "low", "dead"], now=NOW)
    assert [r["account"] for r in rows] == ["dead", "low", "healthy"], [r["account"] for r in rows]
    assert rows[0]["severity"] == "critical" and rows[-1]["severity"] == "ok"
    assert "在庫ゼロ" in rows[0]["message"]
    print("  ✓ 深刻な順に並ぶ＋1行サマリが付く OK")


def test_alert_html_leads_with_state():
    rows = summarize([p("dead", "2026-07-10 12:00", "posted")], ["dead"], now=NOW)
    html = build_inventory_alert(rows, "2026-07-28 08:00")
    assert "在庫ゼロ" in html and "投稿が1本も出ません" in html
    assert "dead" in html and html.startswith("<!doctype html>")
    print("  ✓ アラートHTMLが状態を最初に出す OK")


def test_alert_html_healthy_state():
    posts = [p("ok1", "2026-08-05 12:00")]
    html = build_inventory_alert(summarize(posts, ["ok1"], now=NOW), "2026-07-28 08:00")
    assert "全アカウント正常" in html
    print("  ✓ 正常時のHTMLも壊れない OK")


def test_send_alerts_is_silent_when_all_healthy():
    # 毎日の無害メールで通知が麻痺するのを防ぐ＝正常なら送らない
    import main_monitor
    rows = summarize([p("ok1", "2026-08-05 12:00")], ["ok1"], now=NOW)
    for r in rows:
        r["business"] = "seizogyo"
    sent = []
    orig = main_monitor.send_html
    main_monitor.send_html = lambda *a, **k: sent.append(a)
    try:
        failed = main_monitor.send_alerts(rows, "2026-07-28", {
            "ENABLE_EMAIL": "1", "MAIL_USERNAME": "u", "MAIL_PASSWORD": "p", "MAIL_TO": "to@x"})
    finally:
        main_monitor.send_html = orig
    assert sent == [] and failed == 0
    print("  ✓ 全て正常ならメールを送らない OK")


def test_send_alerts_groups_by_recipient_and_flags_subject():
    import main_monitor
    rows = summarize([p("dead", "2026-07-10 12:00", "posted"), p("dead2", "2026-07-11 12:00", "posted")],
                     ["dead", "dead2"], now=NOW)
    rows[0]["business"], rows[1]["business"] = "seizogyo", "seizogyo2"
    sent = []
    orig = main_monitor.send_html
    main_monitor.send_html = lambda user, pw, sender, to, subject, html, **k: sent.append((to, subject))
    try:
        failed = main_monitor.send_alerts(rows, "2026-07-28", {
            "ENABLE_EMAIL": "1", "MAIL_USERNAME": "u", "MAIL_PASSWORD": "p",
            "MAIL_TO": "owner@x", "MAIL_TO_SEIZOGYO2": "owner@x, staff@x"})
    finally:
        main_monitor.send_html = orig
    assert failed == 0
    assert len(sent) == 2, sent                                    # 宛先ごとに1通
    assert {t for t, _ in sent} == {"owner@x", "owner@x, staff@x"}
    assert all(s.startswith("【要確認】") for _, s in sent), sent   # 件名で埋もれない
    print("  ✓ 宛先ごとに1通・件名に【要確認】 OK")


def test_send_alerts_noop_without_enable_email():
    import main_monitor
    assert main_monitor.send_alerts([], "2026-07-28", {}) == 0
    print("  ✓ ENABLE_EMAIL 未設定なら何もしない OK")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n全 {len(fns)} 件 PASS")
