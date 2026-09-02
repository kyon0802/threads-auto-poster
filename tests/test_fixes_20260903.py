"""2026-09-03 運用監査で見つかった3問題の修正テスト。

A. 型語彙のアカウント別化（澪の占い専用6型がコードの汎用6型と衝突して分裂した）
B. Google Sheets 429 対策（worksheets() のキャッシュ＋分クォータを跨ぐ再試行）
C. 生成が既存在庫を見ず「翌日から」始まる（FORCE_CYCLE 時に次サイクルと二重予約）

実行: python3 -m pytest tests/test_fixes_20260903.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from threads_poster.sheets import MemoryStore, GoogleSheetStore, with_retry  # noqa: E402
from threads_poster.generator import (  # noqa: E402
    Generator, build_prompt, hook_types_for, HOOK_TYPES, PROFILE_HOOK_VOCAB_KEY,
)

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 6, 24, 4, 0, tzinfo=JST)


# ---------------------------------------------------------------- A. 型語彙

def test_hook_types_default_when_profile_has_no_vocab():
    assert hook_types_for({"声": "x"}) == HOOK_TYPES


def test_hook_types_from_profile_row_keeps_fullwidth_slash_inside_name():
    """区切りは「 / 」「、」「,」のみ。名前内部の全角スラッシュ（防御線／逆説）は分割しない。"""
    prof = {PROFILE_HOOK_VOCAB_KEY:
            "縁起日×時間限定 / 知的な常識否定 / 防御線／逆説、ラベリング問いかけ, 承認・許可（巡り代弁）"}
    assert hook_types_for(prof) == ["縁起日×時間限定", "知的な常識否定", "防御線／逆説",
                                    "ラベリング問いかけ", "承認・許可（巡り代弁）"]


def test_build_prompt_uses_account_vocab_and_forbids_free_naming():
    vocab = ["縁起日×時間限定", "断定の告知"]
    p = build_prompt("a1", {"声": "x"}, [{"分類": "NG", "ルール": "絶対", "重大度": "高"}],
                     {}, 12, hook_types=vocab)
    assert "縁起日×時間限定" in p and "断定の告知" in p
    assert "数字提示" not in p              # 汎用語彙は出ない
    assert "自由命名" not in p              # 自由命名の許可文言は消えている
    assert "探索枠" in p                    # 探索枠は「少ない型を試す」として残る


def test_generator_reads_vocab_from_profile_and_passes_to_generate_fn():
    """本番経路（candidates=None）では、プロフィールの語彙が生成関数に渡る。"""
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x", PROFILE_HOOK_VOCAB_KEY: "型A / 型B"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    seen = {}

    def fake_gen(prompt, hook_types=None):
        seen["hook_types"] = hook_types
        assert "型A" in prompt
        return [{"text": "本文", "hook_type": "型A", "content_type": "共感", "exemplar_id": ""}]

    Generator(store, "a1", generate_fn=fake_gen, now_fn=lambda: NOW, status="draft").run({})
    assert seen["hook_types"] == ["型A", "型B"]


def test_generator_accepts_legacy_generate_fn_without_kwarg():
    """旧シグネチャ generate_fn(prompt) も引き続き動く（後方互換）。"""
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    res = Generator(store, "a1", generate_fn=lambda p: ["旧形式の本文"],
                    now_fn=lambda: NOW, status="draft").run({})
    assert len(res["written"]) == 1


# ---------------------------------------------------------------- B. 429 対策

class _FakeWs:
    def __init__(self, title):
        self.title = title


class _FakeSh:
    """worksheets() の呼び出し回数を数える擬似 Spreadsheet。"""
    def __init__(self, titles):
        self._titles = list(titles)
        self.calls = 0

    def worksheets(self):
        self.calls += 1
        return [_FakeWs(t) for t in self._titles]


def _store_with_fake_sh(titles):
    st = object.__new__(GoogleSheetStore)  # __init__（実API）を通さずに組み立てる
    st.sh = _FakeSh(titles)
    return st


def test_worksheets_listing_is_cached_within_a_store_instance():
    st = _store_with_fake_sh(["accounts", "投稿_a1"])
    st._ws_by_title()
    st._ws_by_title()
    st._ws_by_title()
    assert st.sh.calls == 1, "同一インスタンス内では worksheets() を1回しか呼ばない"
    assert "投稿_a1" in st._ws_by_title()


def test_worksheets_cache_can_be_refreshed_after_tab_creation():
    st = _store_with_fake_sh(["accounts"])
    st._ws_by_title()
    st.sh._titles.append("お手本DB_a1")       # タブが増えた（add_worksheet 相当）
    assert "お手本DB_a1" not in st._ws_by_title()
    assert "お手本DB_a1" in st._ws_by_title(refresh=True)
    assert st.sh.calls == 2


def test_with_retry_waits_across_a_minute_quota_on_429():
    """429（分あたり読み取り上限）は 2,4,8,16,32 秒と待って6回目で成功できる＝合計62秒＞1分。"""
    class _Resp:
        status_code = 429

    class _Err(Exception):
        response = _Resp()

    n = {"calls": 0}

    def flaky():
        n["calls"] += 1
        if n["calls"] < 6:
            raise _Err("429")
        return "OK"

    delays = []
    assert with_retry(flaky, sleep=delays.append) == "OK"
    assert n["calls"] == 6
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert sum(delays) >= 60


# ---------------------------------------------------------------- C. 在庫を見て生成

def _stocked_store(last_day: str):
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    store.posts = [
        {"row_id": "a1-001", "account": "a1", "post_datetime": "2026-06-25 12:00", "status": "queued"},
        {"row_id": "a1-002", "account": "a1", "post_datetime": f"{last_day} 21:00", "status": "queued"},
        {"row_id": "a1-old", "account": "a1", "post_datetime": "2026-06-20 21:00", "status": "posted"},
        {"row_id": "a1-ret", "account": "a1", "post_datetime": "2026-07-30 21:00", "status": "retired"},
        {"row_id": "b1-001", "account": "b1", "post_datetime": "2026-07-20 21:00", "status": "queued"},
    ]
    return store


def test_generation_starts_after_last_stocked_day():
    """在庫が 06-27 まであれば、生成は 06-28 から（翌日=06-25 に重ねない）。
    retired／他アカウント／公開済みの行は在庫として数えない。"""
    store = _stocked_store("2026-06-27")
    cands = ["本文その1。", "本文その2。"]
    Generator(store, "a1", generate_fn=lambda p: cands, now_fn=lambda: NOW,
              status="queued").run({}, candidates=cands, existing_posts=store.posts)
    new = sorted(p["post_datetime"] for p in store.posts if p["row_id"].startswith("a1-g"))
    assert new == ["2026-06-28 21:00", "2026-06-29 21:00"], new


def test_generation_falls_back_to_tomorrow_when_no_future_stock():
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "x"}}
    store.guideline = [{"分類": "NGワード", "ルール": "絶対", "重大度": "高"}]
    cands = ["本文。"]
    Generator(store, "a1", generate_fn=lambda p: cands, now_fn=lambda: NOW,
              status="queued").run({}, candidates=cands, existing_posts=[])
    assert store.posts[0]["post_datetime"] == "2026-06-25 21:00"


def test_generation_with_schedule_fn_starts_after_stock():
    """1日4本のランダム配置（製造業）でも、開始日は在庫の翌日になる。"""
    import random
    from threads_poster.schedule import build_schedule
    store = _stocked_store("2026-06-27")
    cands = [f"本文{i}。" for i in range(4)]
    Generator(store, "a1", generate_fn=lambda p: cands, now_fn=lambda: NOW,
              status="queued", schedule_fn=build_schedule, rng=random.Random(0)
              ).run({}, candidates=cands, existing_posts=store.posts)
    days = {p["post_datetime"][:10] for p in store.posts if p["row_id"].startswith("a1-g")}
    assert days == {"2026-06-28"}, days
