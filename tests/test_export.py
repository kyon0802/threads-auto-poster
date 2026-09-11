"""全投稿データのエクスポート（アカウント別・月別）の検証。

背景（2026-09-11）: 全アカ・全期間の投稿を1枚のスプレッドシートに集約して毎日自動更新する。
インサイトは posted_id × 取得日 で日次に積まれるため、素直に出すと1投稿が日数ぶんに増える。
また本文は 2026-09-11 に列を追加したため古い行は空で、投稿タブから補完する必要がある。
この2点をテストで固定する。

実行: python3 -m pytest tests/test_export.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.export import build_rows, monthly_summary  # noqa: E402


def _ins(pid, dt, snap, views, text=""):
    return {"posted_id": pid, "post_datetime": dt, "snapshot_date": snap,
            "views": views, "text": text, "likes": 1, "replies": 0,
            "reposts": 0, "quotes": 0, "shares": 0, "permalink": f"http://x/{pid}"}


def test_one_row_per_post_not_per_snapshot():
    """日次スナップショットを畳んで1投稿1行にする。素直に出すと投稿数が日数倍に膨らむ。"""
    rows = build_rows("acc", [_ins("p1", "2026-09-01 10:00", "2026-09-09", 10),
                              _ins("p1", "2026-09-01 10:00", "2026-09-10", 50)], {})
    assert len(rows) == 1
    assert rows[0]["views"] == 50, "最新スナップショットの表示数を採用すべき"
    print("  ✓ 1投稿1行・最新の表示数を採用 OK")


def test_backfills_text_from_posts_tab():
    """本文が空なら投稿タブ（自社が書いた原稿）から補完する。

    本文列は 2026-09-11 に追加したため、それ以前に収集を終えた古い投稿は本文が空になる。
    自社アカはこちらが書いた原稿が投稿タブに残っているので復元できる。
    """
    rows = build_rows("acc", [_ins("p1", "2026-09-01 10:00", "2026-09-10", 10, text="")],
                      {"p1": {"text": "復元された本文", "row_id": "r-1"}})
    assert rows[0]["text"] == "復元された本文"
    assert rows[0]["text_source"] == "投稿タブ"
    print("  ✓ 本文を投稿タブから補完 OK")


def test_keeps_api_text_when_present():
    """APIで取れている本文を投稿タブで上書きしない（実際に投稿された文面が正）。"""
    rows = build_rows("acc", [_ins("p1", "2026-09-01 10:00", "2026-09-10", 10, text="APIの本文")],
                      {"p1": {"text": "投稿タブの本文", "row_id": "r-1"}})
    assert rows[0]["text"] == "APIの本文"
    assert rows[0]["text_source"] == "API収集"
    print("  ✓ API本文を優先 OK")


def test_adds_month_and_hour_for_filtering():
    """月・時・曜日を列として持たせる（人がフィルタで絞れるように）。"""
    r = build_rows("acc", [_ins("p1", "2026-09-03 14:05", "2026-09-10", 10, "x")], {})[0]
    assert r["month"] == "2026-09" and r["hour"] == 14 and r["weekday"] == "木"
    print("  ✓ 月・時・曜日を付与 OK")


def test_skips_rows_without_datetime():
    """投稿日時が無い行は出さない（月別集計が壊れるため）。"""
    assert build_rows("acc", [_ins("p1", "", "2026-09-10", 10, "x")], {}) == []
    print("  ✓ 日時なしの行を除外 OK")


def test_monthly_summary_uses_median():
    """月別サマリは中央値を主指標にする（1本の当たりで平均が跳ねるため）。"""
    rows = build_rows("acc", [_ins(f"p{i}", f"2026-09-0{i} 10:00", "2026-09-10", v, "t")
                              for i, v in enumerate([1, 2, 3, 4, 900], start=1)], {})
    s = monthly_summary(rows)[0]
    assert s["posts"] == 5
    assert s["views_median"] == 3, "中央値が実態を示すべき"
    assert s["views_mean"] == 182, "平均も併記する（乖離を見せるため）"
    print("  ✓ 月別サマリは中央値が主・平均を併記 OK")


def test_monthly_summary_splits_by_month():
    """月をまたぐと別行になる。"""
    rows = build_rows("acc", [_ins("p1", "2026-08-31 10:00", "2026-09-10", 5, "a"),
                              _ins("p2", "2026-09-01 10:00", "2026-09-10", 7, "b")], {})
    s = monthly_summary(rows)
    assert [x["month"] for x in s] == ["2026-08", "2026-09"]
    print("  ✓ 月別に分割 OK")


# ---------------------------------------------------------------------------
# 閲覧用シートにだけ含める「読み取り専用の追加シート」
#
# 廃止したアカウント（例: 2026-07-28 廃止の占い「結」）は BUSINESSES に入れられない。
# 入れると post/weekly/monitor まで動き出してしまうため。しかし過去データは記録として
# 残したいので、**エクスポートだけが見る追加シート**を別の環境変数で渡せるようにする。
# ---------------------------------------------------------------------------
from main_export import resolve_export_sheets  # noqa: E402


def test_includes_extra_sheets_for_export_only():
    """EXPORT_EXTRA_SHEETS のシートを、通常の事業シートに足して返す。"""
    env = {"BUSINESSES": '[{"name":"biz1","spreadsheet_id":"S1"}]',
           "EXPORT_EXTRA_SHEETS": '[{"name":"retired","spreadsheet_id":"S2"}]'}
    assert resolve_export_sheets(env) == [("biz1", "S1"), ("retired", "S2")]
    print("  ✓ 追加シートを含めて返す OK")


def test_works_without_extra_sheets():
    """未設定なら通常の事業シートだけ（後方互換）。"""
    env = {"BUSINESSES": '[{"name":"biz1","spreadsheet_id":"S1"}]'}
    assert resolve_export_sheets(env) == [("biz1", "S1")]
    print("  ✓ 未設定なら従来どおり OK")


def test_ignores_duplicate_sheet_ids():
    """同じシートIDを二重に読まない（投稿が二重に出るのを防ぐ）。"""
    env = {"BUSINESSES": '[{"name":"biz1","spreadsheet_id":"S1"}]',
           "EXPORT_EXTRA_SHEETS": '[{"name":"dup","spreadsheet_id":"S1"}]'}
    assert resolve_export_sheets(env) == [("biz1", "S1")]
    print("  ✓ 重複シートIDを除外 OK")


def test_bad_json_does_not_break_export():
    """追加シートの指定が壊れていても、通常の事業分の書き出しは続ける。"""
    env = {"BUSINESSES": '[{"name":"biz1","spreadsheet_id":"S1"}]',
           "EXPORT_EXTRA_SHEETS": "これはJSONではない"}
    assert resolve_export_sheets(env) == [("biz1", "S1")]
    print("  ✓ 壊れた指定を無視して継続 OK")
