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


def test_main_calls_n_posts_for_with_four_args():
    """main_weekly.py の main() 内 n_posts_for(...) 呼び出しは4引数であること。

    引数不一致（3引数のまま）は TypeError になり、main() の per-account try/except
    に握り潰されて failures += 1 されるだけになる。結果、全アカウントで生成0本・
    週次レポートメールが1通も出ない・sort_posts_tab も走らないという広範囲の
    サイレント停止を招く。114本のユニットテストが緑でもこの呼び出し不整合は検出
    できなかったため、シグネチャを機械的に固定して同種の再発を防ぐ。
    """
    import ast

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, "main_weekly.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break
    assert main_func is not None, "main_weekly.py に関数 main が見つかりません"

    calls = [
        node for node in ast.walk(main_func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "n_posts_for"
    ]
    assert calls, "main() 内に n_posts_for(...) 呼び出しが見つかりません（見落とし防止）"
    for call in calls:
        assert len(call.args) == 4, (
            f"n_posts_for の位置引数は4つのはずが {len(call.args)} 個でした: "
            f"{ast.dump(call)}"
        )
    print("  ✓ main() 内の n_posts_for 呼び出しは4引数 OK")


def test_weekly_yml_gen_posts_vars_have_no_fallback():
    """weekly.yml の GEN_POSTS_<事業名> にフォールバック（||）が無いこと。

    n_posts_for は環境変数が「空」のときだけ曜日ベースの動的計算（日=12本/水=16本）に
    フォールバックする。weekly.yml 側で `${{ vars.GEN_POSTS_X || '12' }}` のように
    フォールバック値を入れると Variable が常に非空になり、動的計算が本番で一度も
    発動しなくなる（このバグ自体が今回の修正対象だった）。GEN_POSTS_PER_ACCOUNT は
    4本/日スケジュール対象外の事業向けの既定値でフォールバックの対象外。
    """
    import re

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, ".github", "workflows", "weekly.yml")
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    target_lines = [
        line for line in lines
        if re.search(r"\bGEN_POSTS_(?!PER_ACCOUNT\b)\w+\s*:", line)
    ]
    assert target_lines, "weekly.yml に GEN_POSTS_<事業名> の行が見つかりません（見落とし防止）"
    for line in target_lines:
        assert "||" not in line, f"フォールバックが残っています: {line.strip()}"
    print("  ✓ weekly.yml の GEN_POSTS_<事業名> にフォールバック無し OK")


def test_decide_gates_by_weekday():
    """日=生成+レポート / 水=生成のみ / それ以外=何もしない。"""
    from main_weekly import decide_gates
    assert decide_gates(date(2026, 9, 6), {}) == (True, True)     # 日
    assert decide_gates(date(2026, 9, 9), {}) == (True, False)    # 水
    assert decide_gates(date(2026, 9, 7), {}) == (False, False)   # 月
    assert decide_gates(date(2026, 9, 12), {}) == (False, False)  # 土
    print("  ✓ decide_gates（日=生成+報告 / 水=生成のみ / 他=なし）OK")


def test_decide_gates_force_cycle_forces_both():
    """FORCE_CYCLE=1 は手動実行用。生成もレポートも両方走らせる。"""
    from main_weekly import decide_gates
    assert decide_gates(date(2026, 9, 7), {"FORCE_CYCLE": "1"}) == (True, True)
    assert decide_gates(date(2026, 9, 9), {"FORCE_CYCLE": "1"}) == (True, True)
    print("  ✓ decide_gates（FORCE_CYCLE=1 で両方強制）OK")


if __name__ == "__main__":
    test_is_cycle_day_sunday_and_wednesday()
    test_is_report_day_sunday_only()
    test_days_until_next_cycle()
    test_two_cycles_cover_the_week_without_gap_or_overlap()
    test_n_posts_for_is_dynamic_by_weekday()
    test_n_posts_for_zero_variable_turns_generation_off()
    test_n_posts_for_explicit_override_is_backward_compatible()
    test_n_posts_for_empty_variable_falls_back_to_dynamic()
    test_main_calls_n_posts_for_with_four_args()
    test_weekly_yml_gen_posts_vars_have_no_fallback()
    test_decide_gates_by_weekday()
    test_decide_gates_force_cycle_forces_both()
    print("========== 全テスト PASS ==========")
