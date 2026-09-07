"""殿堂入り（自アカ長期の当たり投稿DB）と3窓傾向分析の検証。

背景（2026-09-07 実測）: 生データは全部蓄積されているのに、生成AIが見る勝ち投稿は
28日窓のTOP3だけだった。takumi は自己ベスト(3,759表示)を、澪は上位5本すべてを
一度も見ないまま次を作っていた。設計= docs/superpowers/specs/2026-09-07-hall-of-fame-design.md

実行: python3 -m pytest tests/test_hall_of_fame.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime  # noqa: E402

from threads_poster.analyzer import analyze_windowed, analysis_to_rows, TREND_WINDOWS  # noqa: E402
from threads_poster.hall_of_fame import build_hall_of_fame, select_for_prompt  # noqa: E402
from threads_poster.sheets import MemoryStore  # noqa: E402
from threads_poster.generator import build_prompt  # noqa: E402

NOW = datetime(2026, 9, 7, 12, 0)


def ins(pid, dt, views, er=0.01):
    """インサイト行（最新スナップショット1件ぶん）。"""
    return {"posted_id": pid, "snapshot_date": "2026-09-07", "post_datetime": dt,
            "views": views, "likes": 1, "replies": 0, "reposts": 0, "quotes": 0,
            "engagement_rate": er, "text_len": 120}


def post(pid, text, hook="数字提示", content="情報提供"):
    return {"row_id": f"r-{pid}", "account": "a1", "posted_id": pid, "text": text,
            "status": "posted", "hook_type": hook, "content_type": content}


# ---------------------------------------------------------------- 殿堂入りの構築

def test_hall_of_fame_picks_all_time_top_by_views():
    """28日窓の外（3ヶ月前）でも表示回数が高ければ殿堂入りする＝これが今回の目的。"""
    rows = [ins("old", "2026-06-18 21:00", 3759),   # 3ヶ月前の自己ベスト
            ins("mid", "2026-07-03 21:00", 2025),
            ins("new", "2026-09-05 21:00", 1366)]   # 直近28日のTOP
    posts = [post("old", "3ヶ月前の当たり本文"), post("mid", "7月の当たり"), post("new", "直近の当たり")]
    hof = build_hall_of_fame(rows, posts, existing=[], limit=30, now=NOW)
    assert [h["posted_id"] for h in hof] == ["old", "mid", "new"]
    assert hof[0]["rank"] == 1 and hof[0]["views"] == 3759
    assert hof[0]["text"] == "3ヶ月前の当たり本文", "本文が投稿タブから結合されること"
    assert hof[0]["hook_type"] == "数字提示"


def test_hall_of_fame_caps_at_limit():
    rows = [ins(f"p{i}", "2026-08-01 21:00", 1000 - i) for i in range(40)]
    posts = [post(f"p{i}", f"本文{i}") for i in range(40)]  # 純関数の上限検証（重複ゲートは通らない）
    hof = build_hall_of_fame(rows, posts, existing=[], limit=30, now=NOW)
    assert len(hof) == 30
    assert hof[0]["views"] == 1000 and hof[-1]["views"] == 971


def test_hall_of_fame_keeps_protected_rows_and_dedupes():
    """人が手で入れた行（保護が非空）は自動更新で消えない。同じ投稿を二重に載せない。"""
    rows = [ins("a", "2026-08-01 21:00", 5000), ins("b", "2026-08-02 21:00", 4000),
            ins("c", "2026-08-03 21:00", 3000)]
    posts = [post("a", "A本文"), post("b", "B本文"), post("c", "C本文")]
    existing = [
        {"posted_id": "manual1", "post_datetime": "2026-05-01 21:00", "text": "人が選んだ手動投稿",
         "views": 10, "protected": "◯", "note": "個人的に効いた実感"},
        {"posted_id": "b", "post_datetime": "2026-08-02 21:00", "text": "B本文",
         "views": 4000, "protected": "◯", "note": ""},
        {"posted_id": "a", "post_datetime": "2026-08-01 21:00", "text": "A本文",
         "views": 5000, "protected": "", "note": "自動で入った行"},
    ]
    hof = build_hall_of_fame(rows, posts, existing=existing, limit=30, now=NOW)
    ids = [h["posted_id"] for h in hof]
    assert ids[:2] == ["manual1", "b"], "保護行が先頭に温存される"
    assert ids.count("b") == 1, "保護行と自動収穫で重複しない"
    assert "a" in ids and "c" in ids
    assert hof[0]["protected"] == "◯" and hof[0]["note"] == "個人的に効いた実感"
    assert [h["rank"] for h in hof] == list(range(1, len(hof) + 1)), "順位は振り直す"


def test_hall_of_fame_protected_rows_alone_can_fill_limit():
    """保護行だけで上限に達したら自動収穫は入らない（人の意思を上書きしない）。"""
    existing = [{"posted_id": f"m{i}", "post_datetime": "2026-05-01 21:00", "text": f"手動{i}",
                 "views": 1, "protected": "◯"} for i in range(30)]
    rows = [ins("hot", "2026-08-01 21:00", 99999)]
    hof = build_hall_of_fame(rows, [post("hot", "爆伸び")], existing=existing, limit=30, now=NOW)
    assert len(hof) == 30 and "hot" not in [h["posted_id"] for h in hof]


def test_hall_of_fame_skips_posts_without_text():
    """本文が結合できない投稿（投稿タブに無い）は殿堂入りさせない（AIに空を見せない）。"""
    rows = [ins("ghost", "2026-06-01 21:00", 9999), ins("ok", "2026-06-02 21:00", 100)]
    hof = build_hall_of_fame(rows, [post("ok", "実体のある本文")], existing=[], limit=30, now=NOW)
    assert [h["posted_id"] for h in hof] == ["ok"]


# ---------------------------------------------------------------- プロンプト用の抽出

def test_select_for_prompt_rotates_deterministically():
    """同じ日なら同じ3本（再現性）。日が変われば別の3本（模倣の固定化を防ぐ）。"""
    hof = [{"posted_id": f"p{i}", "text": f"本文{i}", "views": 100 - i, "rank": i + 1}
           for i in range(10)]
    a = select_for_prompt(hof, datetime(2026, 9, 7, 6, 0), k=3)
    a2 = select_for_prompt(hof, datetime(2026, 9, 7, 23, 0), k=3)
    b = select_for_prompt(hof, datetime(2026, 9, 10, 6, 0), k=3)
    assert len(a) == 3 and [x["posted_id"] for x in a] == [x["posted_id"] for x in a2]
    assert [x["posted_id"] for x in a] != [x["posted_id"] for x in b]


def test_select_for_prompt_handles_small_pool():
    hof = [{"posted_id": "only", "text": "1本だけ", "views": 5, "rank": 1}]
    got = select_for_prompt(hof, NOW, k=3)
    assert [x["posted_id"] for x in got] == ["only"], "重複させず、あるぶんだけ返す"
    assert select_for_prompt([], NOW, k=3) == []


# ---------------------------------------------------------------- 3窓の傾向分析

def _spread_rows():
    """60日内・180日内・それ以前 に投稿があるデータ。"""
    return ([ins(f"n{i}", "2026-09-01 20:00", 500) for i in range(3)]
            + [ins(f"m{i}", "2026-05-01 20:00", 400) for i in range(3)]     # 180日内・60日外
            + [ins(f"o{i}", "2026-01-01 20:00", 300) for i in range(3)])    # 180日外


def test_analyze_windowed_returns_three_trend_windows():
    a = analyze_windowed(_spread_rows(), now=NOW)
    assert [w[0] for w in TREND_WINDOWS] == ["60日", "180日", "全期間"]
    assert set(a["trends"]) == {"60日", "180日", "全期間"}
    assert a["trends"]["60日"]["n_posts"] == 3
    assert a["trends"]["180日"]["n_posts"] == 6
    assert a["trends"]["全期間"]["n_posts"] == 9
    for w in ("60日", "180日", "全期間"):
        assert {"by_time", "by_weekday", "by_length", "by_tree"} <= set(a["trends"][w])


def test_primary_trend_keys_use_60day_window():
    """既存キー by_* は60日窓（生成が読む主軸・28日→60日でサンプル増）。"""
    a = analyze_windowed(_spread_rows(), now=NOW)
    assert a["trend_n_posts"] == 3
    assert a["trend_window_days"] == 60
    assert a["by_time"] == a["trends"]["60日"]["by_time"]


def test_analysis_to_rows_labels_each_window():
    """インサイト分析タブに3窓が窓名つきで並ぶ（人が開いて区別できる）。"""
    a = analyze_windowed(_spread_rows(), now=NOW)
    axes = {r[0] for r in analysis_to_rows(a)}
    for w in ("60日", "180日", "全期間"):
        assert f"時間帯({w})" in axes, axes


# ---------------------------------------------------------------- 生成プロンプト

def test_build_prompt_includes_hall_of_fame_and_dedupes_with_trend_top():
    hof = [{"posted_id": "old", "text": "3ヶ月前の当たり本文", "views": 3759, "rank": 1},
           {"posted_id": "dup", "text": "直近と同じ投稿", "views": 1366, "rank": 2}]
    analysis = {"trend_n_posts": 20,
                "trend_top": [{"posted_id": "dup", "views": 1366, "text": "直近と同じ投稿"}],
                "trend_bottom": []}
    p = build_prompt("a1", {"声": "x"}, [{"分類": "NG", "ルール": "絶対", "重大度": "高"}],
                     analysis, 12, hall_of_fame=hof)
    assert "殿堂入り" in p
    assert "3ヶ月前の当たり本文" in p and "3759" in p
    assert p.count("直近と同じ投稿") == 1, "trend_top と重複する投稿は殿堂入り側で出さない"


def test_build_prompt_without_hall_of_fame_is_unchanged():
    p = build_prompt("a1", {"声": "x"}, [{"分類": "NG", "ルール": "絶対", "重大度": "高"}], {}, 12)
    assert "殿堂入り" not in p


# ---------------------------------------------------------------- Store

def test_memorystore_hall_of_fame_roundtrip():
    store = MemoryStore([{"account": "a1"}], [])
    assert store.get_hall_of_fame("a1") == []
    store.write_hall_of_fame("a1", [{"rank": 1, "posted_id": "p1", "text": "本文", "views": 900}])
    got = store.get_hall_of_fame("a1")
    assert got[0]["posted_id"] == "p1" and got[0]["views"] == 900


# ---------------------------------------------------------------- 重複ゲート（2026-09-07）
# 9/1のPDCA機能（勝ち投稿の本文をAIに見せる）を入れた直後から、AIが実例を丸写しして
# 完全一致の投稿が量産された（住田 0組→11組・ぱし 0組→15組）。Threadsで同一本文を
# 繰り返すのはスパム判定の引き金で、製造業は過去2回BAN。機械ゲートで公開前に止める。

def test_find_near_duplicate_detects_verbatim_copy():
    from threads_poster.compliance import find_near_duplicate
    existing = ["給料日まであと何日って、数えてない?\n\n毎月カツカツなの、がんばりが足りないからじゃない。"]
    hit = find_near_duplicate(existing[0], existing)
    assert hit is not None, "完全一致は必ず検出する"


def test_find_near_duplicate_detects_light_rewrite():
    from threads_poster.compliance import find_near_duplicate
    a = "30歳で貯金ゼロ、ヤバいって薄々気づいてる?でも、それって今のあなたのせいじゃない。"
    b = "30歳で貯金ゼロ、薄々ヤバいって気づいてる?でもそれ、あなたのせいじゃないかも。"
    assert find_near_duplicate(b, [a]) is not None, "言い回しを変えただけの再投稿も止める"


def test_find_near_duplicate_allows_same_theme_different_post():
    from threads_poster.compliance import find_near_duplicate
    a = "スーツ着て手取り18万、作業服で月収40万の人がいる。見た目で判断する人ほど損をする。"
    b = "工場勤務は底辺だと言われる。でも現場で家族を養っている人を、私は何人も知っている。"
    assert find_near_duplicate(b, [a]) is None, "同じテーマでも別の投稿は通す"


def test_find_near_duplicate_returns_the_matched_text():
    from threads_poster.compliance import find_near_duplicate
    a = "派遣になってタバコをやめた。吸うと身体に悪いし歯も汚れる。"
    assert find_near_duplicate(a, ["まったく別の本文です", a]) == a


def test_generator_rejects_candidates_duplicating_existing_posts():
    """既存の公開/予約と重複する生成はシートに入れない（破棄理由が残る）。"""
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    published = "給料日まであと何日って、数えてない?毎月カツカツなの、がんばりが足りないからじゃない。"
    existing_posts = [{"row_id": "a1-001", "account": "a1", "text": published,
                       "status": "posted", "post_datetime": "2026-09-01 06:45"}]
    from threads_poster.generator import Generator
    cands = [published, "まったく新しい切り口の本文です。現場の話をします。"]
    res = Generator(store, "a1", generate_fn=lambda p: cands, now_fn=lambda: NOW,
                    status="queued").run({}, candidates=cands, existing_posts=existing_posts)
    assert len(res["kept"]) == 1, res["kept"]
    assert len(store.posts) == 1 and store.posts[0]["text"] != published
    assert any("重複" in r for r in res["rejected"][0]["reasons"]), res["rejected"]


def test_generator_compares_against_queued_not_just_posted():
    """同じサイクル内で作った予約分とも重複させない（未公開でも比較対象）。"""
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    queued = "健康保険料 37,612 厚生年金 59,475 という給与明細の話をします。"
    existing_posts = [{"row_id": "a1-q1", "account": "a1", "text": queued,
                       "status": "queued", "post_datetime": "2026-09-30 12:00"}]
    from threads_poster.generator import Generator
    res = Generator(store, "a1", generate_fn=lambda p: [queued], now_fn=lambda: NOW,
                    status="queued").run({}, candidates=[queued], existing_posts=existing_posts)
    assert res["kept"] == [] and store.posts == []


def test_generator_rejects_duplicates_within_the_same_batch():
    """1回の生成の中で同じ本文を2本作ってきたら片方を落とす。"""
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    same = "同じ本文をAIが2本作ってしまったケースです。現場の実感を書きます。"
    from threads_poster.generator import Generator
    res = Generator(store, "a1", generate_fn=lambda p: [same, same], now_fn=lambda: NOW,
                    status="queued").run({}, candidates=[same, same], existing_posts=[])
    assert len(res["kept"]) == 1 and len(store.posts) == 1


def test_select_for_prompt_draws_only_from_the_strongest():
    """ローテーションは上位プール内で回す（30本を平等に回すと弱い例ばかり見せてしまう）。"""
    hof = [{"posted_id": f"p{i}", "text": f"本文{i}", "views": 1000 - i * 100, "rank": i + 1}
           for i in range(30)]
    for day in range(0, 40, 3):
        sel = select_for_prompt(hof, datetime(2026, 9, 7).replace(day=1) if day == 0
                                else datetime(2026, 9, 1).toordinal() and
                                datetime.fromordinal(datetime(2026, 9, 1).toordinal() + day), k=3)
        assert all(h["rank"] <= 10 for h in sel), [h["rank"] for h in sel]
