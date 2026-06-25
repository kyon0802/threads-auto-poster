"""投稿スケジューラ（threads_poster/schedule.py）のロジック検証。実行: python3 test_schedule.py

製造業アカウントの方針を機械的に保証する:
  - 1日4本（昼1本＋夜3本）
  - 昼は 12:00 前後（±30分＝11:30〜12:30）に1本
  - 夜は 18:00〜23:00 にランダムで3本
  - 投稿どうしの最低間隔は30分（ただし30分等間隔ではなくランダム配置）
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import random

from threads_poster.schedule import (
    daily_slots_minutes,
    build_schedule,
    _random_times_with_min_gap,
)

JST = ZoneInfo("Asia/Tokyo")
NOON_LO, NOON_HI = 11 * 60 + 30, 12 * 60 + 30   # 11:30〜12:30
EVE_LO, EVE_HI = 18 * 60, 23 * 60               # 18:00〜23:00
MIN_GAP = 30


def _minutes_of(dt_str: str) -> int:
    """'YYYY-MM-DD HH:MM' → その日の0時からの経過分。"""
    hh, mm = dt_str.split(" ")[1].split(":")
    return int(hh) * 60 + int(mm)


def test_min_gap_helper_exact_fit():
    # 窓 60分・3本・間隔30 → free=0：唯一解 [start, start+30, start+60]、両端ぴったり。
    rng = random.Random(1)
    t = _random_times_with_min_gap(rng, 0, 60, 3, 30)
    assert t == [0, 30, 60], t
    print("  ✓ 最低間隔ヘルパ（ぴったり収まる窓は一意解）OK")


def test_min_gap_helper_impossible_raises():
    rng = random.Random(1)
    try:
        _random_times_with_min_gap(rng, 0, 50, 3, 30)  # free = 50-60 = -10 < 0
        assert False, "配置不能なのに例外が出なかった"
    except ValueError:
        pass
    print("  ✓ 最低間隔ヘルパ（配置不能はValueError）OK")


def test_daily_slots_shape():
    rng = random.Random(42)
    slots = daily_slots_minutes(rng)
    assert len(slots) == 4, slots                       # 昼1＋夜3
    assert slots == sorted(slots), slots                # 時系列昇順
    assert NOON_LO <= slots[0] <= NOON_HI, slots[0]     # 昼は12時前後
    for s in slots[1:]:
        assert EVE_LO <= s <= EVE_HI, s                 # 夜は18-23時
    print("  ✓ 1日スロット（昼1＋夜3・昇順・窓内）OK")


def test_min_gap_property_many_seeds():
    """多数のseedで、隣接間隔が常に30分以上・全スロットが窓内であることを保証する。"""
    for seed in range(500):
        rng = random.Random(seed)
        slots = daily_slots_minutes(rng)
        assert len(slots) == 4
        for a, b in zip(slots, slots[1:]):
            assert b - a >= MIN_GAP, (seed, slots)      # 最低間隔30分
        assert NOON_LO <= slots[0] <= NOON_HI, (seed, slots)
        for s in slots[1:]:
            assert EVE_LO <= s <= EVE_HI, (seed, slots)
    print("  ✓ 最低間隔30分＋窓内（500 seed 全て）OK")


def test_not_fixed_grid():
    """30分等間隔の固定配置ではなく、夜3本の時刻がseedごとにばらつくこと。"""
    first_eve = set()
    gaps = set()
    for seed in range(200):
        slots = daily_slots_minutes(random.Random(seed))
        first_eve.add(slots[1])
        gaps.add(slots[2] - slots[1])
    assert len(first_eve) > 20, len(first_eve)          # 開始時刻が多様
    assert len(gaps) > 5, gaps                          # 間隔も30固定ではなく多様
    print("  ✓ ランダム配置（固定グリッドでない・時刻と間隔が多様）OK")


def test_daily_slots_noon_evening_guard():
    # 既定は余裕330分でOK。窓を近接させると昼→夜の最低間隔が壊れるので早期に弾く。
    daily_slots_minutes(random.Random(0))  # 既定は例外なし
    try:
        daily_slots_minutes(random.Random(0), noon_center_min=17 * 60, noon_jitter_min=60)
        assert False, "近接窓でガードが効かなかった"
    except ValueError:
        pass
    print("  ✓ 昼→夜の最低間隔ガード（近接窓はValueError／既定はOK）OK")


def test_build_schedule_packs_4_per_day():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=JST)       # 木曜6:00
    out = build_schedule(10, start_date=now, tz=JST, rng=random.Random(7))
    assert len(out) == 10, out
    # 翌日(06-26)から開始・1日4本ずつ → 06-26×4, 06-27×4, 06-28×2
    from collections import Counter
    days = Counter(s.split(" ")[0] for s in out)
    assert days["2026-06-26"] == 4 and days["2026-06-27"] == 4 and days["2026-06-28"] == 2, days
    # 全体が時系列昇順
    assert out == sorted(out), out
    # 各日のスロットが方針どおり（昼1＋夜3、最低間隔30分）
    for day in ("2026-06-26", "2026-06-27"):
        mins = [_minutes_of(s) for s in out if s.startswith(day)]
        assert NOON_LO <= mins[0] <= NOON_HI, mins
        for m in mins[1:]:
            assert EVE_LO <= m <= EVE_HI, mins
        for a, b in zip(mins, mins[1:]):
            assert b - a >= MIN_GAP, mins
    print("  ✓ build_schedule（翌日から4本/日で詰める・各日方針順守）OK")


def test_build_schedule_full_week():
    now = datetime(2026, 6, 29, 6, 0, tzinfo=JST)       # 月曜6:00（週次cron想定）
    out = build_schedule(28, start_date=now, tz=JST, rng=random.Random(3))
    from collections import Counter
    days = Counter(s.split(" ")[0] for s in out)
    assert len(days) == 7 and all(c == 4 for c in days.values()), days
    # 翌日(06-30)開始
    assert min(days) == "2026-06-30", days
    print("  ✓ build_schedule（28本＝7日×4・翌日開始）OK")


def test_build_schedule_deterministic():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=JST)
    a = build_schedule(8, start_date=now, tz=JST, rng=random.Random(99))
    b = build_schedule(8, start_date=now, tz=JST, rng=random.Random(99))
    assert a == b, (a, b)
    print("  ✓ build_schedule（同seedで再現性あり）OK")


if __name__ == "__main__":
    print("=== スケジューラ テスト ===")
    test_min_gap_helper_exact_fit()
    test_min_gap_helper_impossible_raises()
    test_daily_slots_shape()
    test_min_gap_property_many_seeds()
    test_not_fixed_grid()
    test_daily_slots_noon_evening_guard()
    test_build_schedule_packs_4_per_day()
    test_build_schedule_full_week()
    test_build_schedule_deterministic()
    print("========== 全テスト PASS ==========")
