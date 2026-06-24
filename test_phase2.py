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


def test_build_prompt_includes_guideline():
    p = build_prompt("a1", {"声": "x"}, [{"分類": "法令", "ルール": "誇大NG", "重大度": "高"}],
                     {"by_time": [("夜(18-23)", 3, 500, 0.04)]}, 3)
    assert "誇大NG" in p and "夜(18-23)" in p
    print("  ✓ build_prompt（プロフィール/ガイドライン/勝ちパターンを内包）OK")


if __name__ == "__main__":
    print("=== Phase 2 テスト ===")
    test_analyze()
    test_analyze_latest_snapshot()
    test_compliance()
    test_generator_gate_missing_profile()
    test_generator_pipeline()
    test_build_prompt_includes_guideline()
    print("========== 全テスト PASS ==========")
