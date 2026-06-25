"""Phase 2（analyzer / compliance / generator）のロジック検証。実行: python3 test_phase2.py"""
from datetime import datetime
from zoneinfo import ZoneInfo

from threads_poster.sheets import MemoryStore
from threads_poster.analyzer import analyze_insights, Analyzer
from threads_poster.compliance import check_post, extract_ng_words
from threads_poster.generator import Generator, GeneratorError, build_prompt

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 6, 24, 10, 0, tzinfo=JST)


def ins(pid, dt, views, er, tl, tree=""):
    return {"account": "a1", "posted_id": pid, "snapshot_date": "2026-06-24", "post_datetime": dt,
            "views": views, "engagement_rate": er, "text_len": tl, "is_tree": tree}


def test_analyze():
    rows = [ins("1", "2026-06-22 21:00", 1000, 0.05, 120),
            ins("2", "2026-06-23 08:00", 100, 0.01, 50),
            ins("3", "2026-06-22 21:30", 800, 0.04, 150, tree="ツリー")]
    a = analyze_insights(rows)
    assert a["n_posts"] == 3
    # 夜(18-23)に2本、平均ER高
    night = [t for t in a["by_time"] if t[0].startswith("夜")][0]
    assert night[1] == 2, night
    assert a["top"][0]["posted_id"] == "1"  # 最大表示
    print("  ✓ analyze_insights（軸別集計＋上位）OK")


def test_analyze_latest_snapshot():
    # 同じ投稿の複数スナップショット → 最新(snapshot_date最大)を採用
    rows = [{"account": "a1", "posted_id": "x", "snapshot_date": "2026-06-23", "post_datetime": "2026-06-22 21:00",
             "views": 10, "engagement_rate": 0.01, "text_len": 100},
            {"account": "a1", "posted_id": "x", "snapshot_date": "2026-06-24", "post_datetime": "2026-06-22 21:00",
             "views": 999, "engagement_rate": 0.09, "text_len": 100}]
    a = analyze_insights(rows)
    assert a["n_posts"] == 1 and a["top"][0]["views"] == 999, a
    print("  ✓ 最新スナップショットを採用（同一投稿の重複を排除）OK")


def test_analyze_totals_and_top_er():
    rows = [ins("1", "2026-06-22 21:00", 1000, 0.05, 120),
            ins("2", "2026-06-23 08:00", 100, 0.01, 50),
            ins("3", "2026-06-22 21:30", 800, "", 150)]  # ER空＝ER順の対象外
    # likes 等を足してリアクション合計を検証
    rows[0]["likes"], rows[0]["replies"] = 3, 2
    a = analyze_insights(rows)
    assert a["total_views"] == 1900, a["total_views"]
    assert a["total_reactions"] == 5, a["total_reactions"]   # likes3+replies2
    assert a["avg_er"] == round((0.05 + 0.01) / 2, 4), a["avg_er"]
    # ER順TOP：ERが入っている2件のみ・降順
    assert [e["posted_id"] for e in a["top_er"]] == ["1", "2"], a["top_er"]
    # 表示順TOPは views 降順
    assert [e["posted_id"] for e in a["top"]][:3] == ["1", "3", "2"], a["top"]
    print("  ✓ analyze 合計KPI＋ER順TOP5 OK")


def test_analyze_er_zero_is_kept():
    # ER=0.0（表示はあるが反応ゼロ）は欠落ではなく有効値。avg_er と top_er に含めるべき。
    rows = [ins("1", "2026-06-22 21:00", 500, 0.0, 100),     # float 0.0
            {"account": "a1", "posted_id": "2", "snapshot_date": "2026-06-24",
             "post_datetime": "2026-06-22 22:00", "views": 300, "engagement_rate": 0, "text_len": 100},  # int 0（シート由来）
            ins("3", "2026-06-23 20:00", 200, 0.04, 100)]
    a = analyze_insights(rows)
    assert a["avg_er"] == round((0.0 + 0.0 + 0.04) / 3, 4), a["avg_er"]   # 0.0 を平均に含める
    assert len(a["top_er"]) == 3, a["top_er"]                              # 0.0 もランキング対象
    assert a["top_er"][0]["posted_id"] == "3"                             # 0.04 が先頭
    print("  ✓ analyze ER=0.0 を有効値として集計（avg/ランキングに含める）OK")


def test_strategy_compliance_gate():
    from threads_poster.strategy import generate_strategy
    store = MemoryStore([{"account": "takumi_kojo_navi"}], [])
    store.profiles = {"takumi_kojo_navi": {"声": "現場目線"}}
    store.guideline = [{"分類": "NGワード（自動遮断）", "ルール": "絶対 / 今すぐDM", "重大度": "高"}]
    fake = lambda p: {  # noqa: E731
        "direction": "夜の時間帯に主要投稿を寄せる。100字前後を基準にする。",
        "focus": ["夜(18-23)に主要投稿", "100字前後", "共感フックで始める"],
        "examples": [
            {"狙い": "共感", "本文": "がんばっても報われない夜に。明日は少しラクにいきましょう。"},   # 合格
            {"狙い": "煽り", "本文": "絶対に稼げます。今すぐDM。"},                                  # NG（絶対/今すぐDM）
            {"狙い": "誘導", "本文": "詳細は https://line.me/x へ"},                                # NG（URL）
        ],
    }
    s = generate_strategy(store, "takumi_kojo_navi", {"by_time": []}, generate_fn=fake)
    assert s is not None and len(s["examples"]) == 1, s
    assert s["examples"][0]["aim"] == "共感"
    assert len(s["focus"]) == 3 and s["direction"]
    print("  ✓ strategy（方針＋例文・NG例はコンプラで除外）OK")


def test_html_report_renders_text_and_strategy():
    from threads_poster.html_report import build_html
    analysis = {"n_posts": 2, "total_views": 1900, "total_reactions": 5, "avg_er": 0.03,
                "by_time": [("夜(18-23)", 2, 900, 0.04)], "by_weekday": [], "by_length": [], "by_tree": [],
                "top": [{"posted_id": "1", "post_datetime": "2026-06-22 21:00", "views": 1000,
                         "engagement_rate": 0.05, "likes": 3, "replies": 2, "reposts": 0,
                         "text_len": 120, "text": "現場の本音をひとつ。\n手取りの話をします。"}],
                "top_er": []}
    strategy = {"direction": "夜に寄せる。", "focus": ["夜に主要投稿"],
                "examples": [{"aim": "共感", "text": "明日は少しラクに。"}]}
    html = build_html("takumi_kojo_navi", analysis, "2026-06-25", theme="seizo", strategy=strategy)
    assert "現場の本音をひとつ。<br>手取りの話をします。" in html  # 本文全文＋改行保持
    assert "1,000" in html and "5.00%" in html                       # 数値整形・ER%
    assert "来週の方針" in html and "明日は少しラクに。" in html       # 方針＋例文
    assert html.startswith("<!doctype html>") and "<table" in html    # メール安全なtable
    print("  ✓ html_report（本文全文＋数値整形＋方針＋例文・table）OK")


def test_compliance():
    ng = extract_ng_words([{"分類": "NGワード（自動遮断）", "ルール": "絶対 / 必ず / 今すぐDM"}])
    assert "絶対" in ng and "今すぐDM" in ng, ng
    ok, _ = check_post("いい一日を。", ng)
    assert ok
    bad, reasons = check_post("絶対に稼げます", ng)
    assert not bad and any("絶対" in r for r in reasons), reasons
    bad2, r2 = check_post("詳しくは https://line.me/x へ", ng)
    assert not bad2 and any("URL" in r for r in r2), r2
    bad3, r3 = check_post("あ" * 600, ng)
    assert not bad3 and any("文字数" in r for r in r3), r3
    print("  ✓ compliance（NGワード/URL/文字数の遮断）OK")


def test_generator_gate_missing_profile():
    store = MemoryStore([{"account": "a1"}], [])
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    # profiles 未設定 → 生成中止
    try:
        Generator(store, "a1", generate_fn=lambda p: ["x"], now_fn=lambda: NOW).run({})
        assert False, "ゲートが効かなかった"
    except GeneratorError:
        pass
    print("  ✓ 必須タブ存在ゲート（プロフィール空→生成中止）OK")


def test_generator_pipeline():
    store = MemoryStore([{"account": "miko_yui_musubi"}], [])
    store.profiles = {"miko_yui_musubi": {"人格・声": "巫女の結。所感形。", "CTA": "プロフ動線"}}
    store.guideline = [{"分類": "NGワード（自動遮断）", "ルール": "絶対 / 必ず / 結ばれる", "重大度": "高"}]
    cands = [
        "今日、お社に来たあなたへ。焦らなくて大丈夫のようですよ。",       # 合格
        "絶対に結ばれる縁です。",                                       # NG（絶対/結ばれる）
        "詳細は https://line.me/yui まで",                            # NG（URL）
    ]
    res = Generator(store, "miko_yui_musubi", generate_fn=lambda p: cands,
                    now_fn=lambda: NOW, status="draft").run({}, candidates=cands)
    assert len(res["kept"]) == 1 and len(res["rejected"]) == 2, res
    # draft で投稿タブに入る（自動公開されない状態）
    assert len(store.posts) == 1 and store.posts[0]["status"] == "draft"
    assert store.posts[0]["row_id"].startswith("miko-g20260624"), store.posts[0]
    print("  ✓ generator パイプライン（生成→ゲート→draft投入）OK")


def test_generator_with_schedule_fn_4_per_day():
    """schedule_fn を渡すと 1日4本・昼1＋夜3・最低間隔30分で予約される（製造業の設定）。"""
    import random
    from threads_poster.schedule import build_schedule
    store = MemoryStore([{"account": "takumi_kojo_navi"}], [])
    store.profiles = {"takumi_kojo_navi": {"声": "現場目線"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    cands = [f"製造業の話 その{i}。" for i in range(6)]  # 6本 → 翌日4本＋翌々日2本
    Generator(store, "takumi_kojo_navi", generate_fn=lambda p: cands, now_fn=lambda: NOW,
              status="queued", schedule_fn=build_schedule, rng=random.Random(0)
              ).run({}, candidates=cands)
    assert len(store.posts) == 6, store.posts
    from collections import Counter
    days = Counter(p["post_datetime"].split(" ")[0] for p in store.posts)
    assert days["2026-06-25"] == 4 and days["2026-06-26"] == 2, days  # NOW=06-24 → 翌日06-25
    # 各時刻が方針どおり（昼11:30-12:30 ＋ 夜18:00-23:00・最低間隔30分）
    d1 = sorted(p["post_datetime"] for p in store.posts if p["post_datetime"].startswith("2026-06-25"))
    mins = [int(s[11:13]) * 60 + int(s[14:16]) for s in d1]
    assert 11 * 60 + 30 <= mins[0] <= 12 * 60 + 30, mins
    for m in mins[1:]:
        assert 18 * 60 <= m <= 23 * 60, mins
    for a, b in zip(mins, mins[1:]):
        assert b - a >= 30, mins
    print("  ✓ generator＋schedule_fn（1日4本・昼1＋夜3・最低間隔30分）OK")


def test_generator_legacy_schedule_unchanged():
    """schedule_fn なし＝従来どおり翌日から1日1本・21時固定（占い等は不変）。"""
    store = MemoryStore([{"account": "miko_yui_musubi"}], [])
    store.profiles = {"miko_yui_musubi": {"声": "巫女"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    cands = ["所感その1。", "所感その2。"]
    Generator(store, "miko_yui_musubi", generate_fn=lambda p: cands, now_fn=lambda: NOW,
              status="draft").run({}, candidates=cands)
    dts = sorted(p["post_datetime"] for p in store.posts)
    assert dts == ["2026-06-25 21:00", "2026-06-26 21:00"], dts  # 翌日から1日1本・21時
    print("  ✓ generator 従来挙動（schedule_fn なし＝1日1本21時）維持 OK")


def test_per_account_email_sending():
    """アカウントごとに1通ずつ・正しい件名で送る（送信は注入したフェイクで検証）。"""
    from main_weekly import send_account_reports
    sent_calls = []

    def fake_send(user, password, sender, to, subject, html, attachment_name=None):
        sent_calls.append({"to": to, "subject": subject, "html": html, "att": attachment_name})

    reports = [
        {"account": "takumi_kojo_navi", "label": "製造業", "html": "<b>製造業</b>", "filename": "a.html"},
        {"account": "miko_yui_musubi", "label": "占い", "html": "<b>占い</b>", "filename": "b.html"},
    ]
    sent, failed = send_account_reports(reports, user="u@x.com", password="pw",
                                        to="dest@x.com", gen_date="2026-06-25", send_fn=fake_send)
    assert (sent, failed) == (2, 0), (sent, failed)
    assert len(sent_calls) == 2                                   # アカウント数だけ送る
    assert sent_calls[0]["subject"] == "【Threads週次】製造業｜takumi_kojo_navi（2026-06-25）"
    assert sent_calls[1]["subject"] == "【Threads週次】占い｜miko_yui_musubi（2026-06-25）"
    assert all(c["to"] == "dest@x.com" for c in sent_calls)       # 同じ宛先に別々の通
    print("  ✓ アカウントごとに個別メール送信（件名・通数）OK")


def test_email_send_failure_isolated():
    """1通失敗しても他は送る＆失敗数を返す。"""
    from main_weekly import send_account_reports
    def flaky(user, pw, sender, to, subject, html, attachment_name=None):
        if "miko" in subject:
            raise RuntimeError("smtp boom")
    reports = [{"account": "takumi_kojo_navi", "label": "製造業", "html": "x", "filename": "a.html"},
               {"account": "miko_yui_musubi", "label": "占い", "html": "y", "filename": "b.html"}]
    sent, failed = send_account_reports(reports, user="u", password="p", to="d", send_fn=flaky)
    assert (sent, failed) == (1, 1), (sent, failed)
    print("  ✓ メール1通失敗が他通を止めない（失敗数を返す）OK")


def test_mailer_build_message():
    from threads_poster.mailer import build_message
    msg = build_message("from@x.com", "to@x.com", "件名テスト", "<b>本文</b>",
                        attachment_bytes=b"<b>x</b>", attachment_name="r.html")
    assert msg["Subject"] == "件名テスト" and msg["To"] == "to@x.com" and msg["From"] == "from@x.com"
    raw = msg.as_string()
    assert "text/html" in raw and "attachment" in raw and "r.html" in raw
    # ヘッダに改行（インジェクション）は拒否
    try:
        build_message("f@x", "t@x", "件名\r\nBcc: evil@x", "<b>x</b>")
        assert False, "改行ヘッダを拒否しなかった"
    except ValueError:
        pass
    print("  ✓ mailer.build_message（HTML本文＋HTML添付＋ヘッダ改行拒否）OK")


def test_mailer_closes_connection_on_failure():
    """smtp_factory 経路：login が失敗しても接続を必ず閉じる（try/finally）。"""
    from threads_poster.mailer import send_message, build_message
    events = []
    class FakeSMTP:
        def __init__(self, host, port): events.append("open")
        def login(self, u, p): raise RuntimeError("auth fail")
        def sendmail(self, *a): events.append("sent")
        def quit(self): events.append("quit")
    msg = build_message("f@x", "t@x", "s", "<b>x</b>")
    try:
        send_message("u", "p", msg, smtp_factory=lambda h, port: FakeSMTP(h, port))
        assert False, "例外が伝播しなかった"
    except RuntimeError:
        pass
    assert events == ["open", "quit"], events  # login失敗でも quit が呼ばれる
    print("  ✓ mailer 送信失敗時も接続を閉じる（try/finally）OK")


def test_build_prompt_includes_guideline():
    p = build_prompt("a1", {"声": "x"}, [{"分類": "法令", "ルール": "誇大NG", "重大度": "高"}],
                     {"by_time": [("夜(18-23)", 3, 500, 0.04)]}, 3)
    assert "誇大NG" in p and "夜(18-23)" in p
    print("  ✓ build_prompt（プロフィール/ガイドライン/勝ちパターンを内包）OK")


if __name__ == "__main__":
    print("=== Phase 2 テスト ===")
    test_analyze()
    test_analyze_latest_snapshot()
    test_analyze_totals_and_top_er()
    test_analyze_er_zero_is_kept()
    test_strategy_compliance_gate()
    test_html_report_renders_text_and_strategy()
    test_compliance()
    test_generator_gate_missing_profile()
    test_generator_pipeline()
    test_generator_with_schedule_fn_4_per_day()
    test_generator_legacy_schedule_unchanged()
    test_per_account_email_sending()
    test_email_send_failure_isolated()
    test_mailer_build_message()
    test_mailer_closes_connection_on_failure()
    test_build_prompt_includes_guideline()
    print("========== 全テスト PASS ==========")
