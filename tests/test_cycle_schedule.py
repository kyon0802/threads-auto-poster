"""PDCAサイクルの曜日ゲート（生成=日・水／レポート=日）。API不要・純関数のみ。

2026-09-06(日) / 09-07(月) / 09-08(火) / 09-09(水) / 09-10(木) / 09-11(金) / 09-12(土) / 09-13(日)
を基準日に使う（実在の曜日）。
"""
import os
import sys
from datetime import date

# リポジトリルートを import パスに追加（tests/ からの直実行・pytest 両対応）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_is_cycle_day_sunday_and_wednesday():
    """生成サイクル日は日曜と水曜だけ。"""
    from main_weekly import is_cycle_day
    assert is_cycle_day(date(2026, 9, 6))    # 日
    assert is_cycle_day(date(2026, 9, 9))    # 水
    assert is_cycle_day(date(2026, 9, 13))   # 翌週の日
    for d in (7, 8, 10, 11, 12):             # 月・火・木・金・土
        assert not is_cycle_day(date(2026, 9, d)), d
    print("  ✓ is_cycle_day（日・水のみ True）OK")


def test_is_report_day_sunday_only():
    """レポート日は日曜だけ。水曜は生成日だがレポート日ではない。"""
    from main_weekly import is_report_day
    assert is_report_day(date(2026, 9, 6))
    assert not is_report_day(date(2026, 9, 9))
    for d in (7, 8, 10, 11, 12):
        assert not is_report_day(date(2026, 9, d)), d
    print("  ✓ is_report_day（日のみ True）OK")


def test_days_until_next_cycle():
    """次の生成日までの日数＝そのサイクルで埋める日数。"""
    from main_weekly import days_until_next_cycle
    assert days_until_next_cycle(date(2026, 9, 6)) == 3    # 日→水（月火水）
    assert days_until_next_cycle(date(2026, 9, 9)) == 4    # 水→日（木金土日）
    assert days_until_next_cycle(date(2026, 9, 7)) == 2    # 月→水（FORCE_CYCLE時）
    assert days_until_next_cycle(date(2026, 9, 12)) == 1   # 土→日
    print("  ✓ days_until_next_cycle（日→3 / 水→4）OK")


def test_two_cycles_cover_the_week_without_gap_or_overlap():
    """2サイクルの合計が7日＝在庫に穴も重複も出ない（週2回運用の要）。"""
    from main_weekly import days_until_next_cycle
    total = days_until_next_cycle(date(2026, 9, 6)) + days_until_next_cycle(date(2026, 9, 9))
    assert total == 7, total
    print("  ✓ 週の被覆（3日+4日=7日）OK")


def test_n_posts_for_is_dynamic_by_weekday():
    """4本/日事業の生成本数は曜日で変わる（日=3日分12本 / 水=4日分16本）。"""
    from main_weekly import n_posts_for
    sunday, wednesday = date(2026, 9, 6), date(2026, 9, 9)
    assert n_posts_for("seizogyo", {}, 5, sunday) == 12
    assert n_posts_for("seizogyo", {}, 5, wednesday) == 16
    assert n_posts_for("seizogyo2", {}, 5, sunday) == 12
    assert n_posts_for("seizogyo3", {}, 5, wednesday) == 16
    assert n_posts_for("meguri", {}, 5, wednesday) == 16
    assert n_posts_for("other", {}, 5, wednesday) == 5   # 4本/日対象外は既定のまま
    print("  ✓ n_posts_for（曜日で12/16本・対象外は既定）OK")


def test_n_posts_for_zero_variable_turns_generation_off():
    """GEN_POSTS_<NAME>=0 は「その事業だけ生成オフ」として残す（用途を0に限定）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "0"}, 5, date(2026, 9, 9)) == 0
    print("  ✓ GEN_POSTS_<NAME>=0（生成オフ）OK")


def test_n_posts_for_explicit_override_is_backward_compatible():
    """1以上の明示指定は後方互換で残す（運用では設定しない）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "9"}, 5, date(2026, 9, 9)) == 9
    print("  ✓ GEN_POSTS_<NAME>=9（明示上書き）OK")


def test_n_posts_for_empty_variable_falls_back_to_dynamic():
    """Variable を削除/空にしたら動的計算に戻る（空文字を int() して落ちないこと）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": ""}, 5, date(2026, 9, 6)) == 12
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "  "}, 5, date(2026, 9, 9)) == 16
    print("  ✓ 空のGEN_POSTS_<NAME>は動的計算にフォールバック OK")


if __name__ == "__main__":
    test_is_cycle_day_sunday_and_wednesday()
    test_is_report_day_sunday_only()
    test_days_until_next_cycle()
    test_two_cycles_cover_the_week_without_gap_or_overlap()
    test_n_posts_for_is_dynamic_by_weekday()
    test_n_posts_for_zero_variable_turns_generation_off()
    test_n_posts_for_explicit_override_is_backward_compatible()
    test_n_posts_for_empty_variable_falls_back_to_dynamic()
    print("========== 全テスト PASS ==========")
