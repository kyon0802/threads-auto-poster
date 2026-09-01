"""週次レポートの期間窓化・在庫ランウェイ・生成エラー分類の検証。

背景（2026-07-28）: 週次レポートの集計に期間フィルタが無く常に全期間累計だったため、
毎サイクルほぼ同じ数字・同じ「読み解き」が出ていた（TOP1が6回連続で同じ投稿）。
ここでは「直近7日 vs 前7日」の窓と、傾向分析用の長めの窓（既定28日）を検証する。

実行: python3 -m pytest tests/test_report_window.py -q  /  python3 tests/test_report_window.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime  # noqa: E402

from threads_poster.analyzer import analyze_windowed, follower_trend  # noqa: E402
from threads_poster.inventory import compute_runway  # noqa: E402
from threads_poster.errors import classify_generation_error  # noqa: E402

NOW = datetime(2026, 7, 28, 13, 0)  # 基準日（naive JST。シートの投稿日時もJST文字列）


def ins(pid, dt, views, er=0.02, tl=120, tree=""):
    return {"account": "a1", "posted_id": pid, "snapshot_date": "2026-07-28", "post_datetime": dt,
            "views": views, "engagement_rate": er, "text_len": tl, "is_tree": tree}


# ---------------------------------------------------------------- 期間窓

def test_current_and_previous_window_split():
    rows = [
        ins("c1", "2026-07-28 09:00", 100),   # 直近7日(07-22〜07-28)
        ins("c2", "2026-07-22 21:00", 200),   # 直近7日の下端（含む）
        ins("p1", "2026-07-21 21:00", 1000),  # 前7日(07-15〜07-21)の上端（含む）
        ins("p2", "2026-07-15 08:00", 500),   # 前7日の下端（含む）
        ins("old", "2026-06-01 08:00", 9999),  # どちらの窓にも入らない
    ]
    a = analyze_windowed(rows, now=NOW)
    assert a["n_posts"] == 2, a["n_posts"]                    # 上位キー＝直近7日
    assert a["total_views"] == 300, a["total_views"]
    assert a["prev"]["n_posts"] == 2, a["prev"]
    assert a["prev"]["total_views"] == 1500, a["prev"]
    assert a["lifetime"]["n_posts"] == 5, a["lifetime"]       # 累計は参考値として保持
    assert a["period_label"] == "07-22〜07-28", a["period_label"]
    print("  ✓ 直近7日 / 前7日 / 累計 の3系統に分離 OK")


def test_delta_vs_previous_period():
    rows = [ins("c1", "2026-07-25 12:00", 300), ins("p1", "2026-07-18 12:00", 100)]
    a = analyze_windowed(rows, now=NOW)
    assert a["delta"]["total_views"] == 2.0, a["delta"]        # 100 → 300 は +200%
    assert a["delta"]["n_posts"] == 0.0, a["delta"]            # 1本 → 1本 は増減なし
    print("  ✓ 前期比（増減率）の算出 OK")


def test_delta_is_none_when_previous_is_zero():
    # 前期が0のときの増減率は数学的に定義できない → None（レポートでは「—」表示）
    rows = [ins("c1", "2026-07-25 12:00", 300)]
    a = analyze_windowed(rows, now=NOW)
    assert a["delta"]["total_views"] is None, a["delta"]
    assert a["prev"]["n_posts"] == 0
    print("  ✓ 前期ゼロなら増減率は None（0除算・誤誘導を防ぐ）OK")


def test_trend_axes_use_longer_window():
    # 傾向分析（時間帯/曜日など）は7日だと曜日あたり1本になりサンプル不足。
    # 既定28日の窓で集計し、7日窓とは別系統であることを保証する。
    rows = [ins("c1", "2026-07-27 21:00", 100),   # 直近7日にも28日にも入る
            ins("t1", "2026-07-05 21:00", 100),   # 28日窓のみ
            ins("old", "2026-05-01 21:00", 100)]  # 28日窓の外
    a = analyze_windowed(rows, now=NOW)
    assert a["n_posts"] == 1, a["n_posts"]                     # KPIは7日
    assert a["trend_n_posts"] == 2, a["trend_n_posts"]         # 傾向は28日
    night = [t for t in a["by_time"] if t[0].startswith("夜")][0]
    assert night[1] == 2, night                                # 傾向軸は28日窓の件数
    assert a["window_days"] == 7 and a["trend_window_days"] == 28
    print("  ✓ 傾向分析は長め(28日)の窓・KPIは7日窓 OK")


def test_top_ranking_is_scoped_to_current_window():
    # 「TOP1が6サイクル連続で同じ投稿」の原因＝全期間からの選出。窓内に限定する。
    rows = [ins("huge_old", "2026-06-18 21:00", 99999), ins("c1", "2026-07-25 21:00", 10)]
    a = analyze_windowed(rows, now=NOW)
    assert [e["posted_id"] for e in a["top"]] == ["c1"], a["top"]
    print("  ✓ TOP5は直近7日の投稿から選出（過去の大当たりが居座らない）OK")


def test_empty_window_does_not_crash():
    rows = [ins("old", "2026-01-01 21:00", 100)]
    a = analyze_windowed(rows, now=NOW)
    assert a["n_posts"] == 0 and a["total_views"] == 0 and a["top"] == []
    assert a["avg_er"] == ""
    print("  ✓ 窓内が0件でも落ちない OK")


# ---------------------------------------------------------------- フォロワー

def test_follower_trend():
    rows = [{"snapshot_date": "2026-07-21", "followers_count": 150},
            {"snapshot_date": "2026-07-28", "followers_count": 162}]
    f = follower_trend(rows, now=NOW)
    assert f["current"] == 162 and f["prev"] == 150 and f["delta"] == 12, f
    print("  ✓ フォロワー増減（直近 vs 7日前）OK")


def test_follower_trend_without_history():
    f = follower_trend([{"snapshot_date": "2026-07-28", "followers_count": 10}], now=NOW)
    assert f["current"] == 10 and f["prev"] is None and f["delta"] is None, f
    assert follower_trend([], now=NOW)["current"] is None
    print("  ✓ 履歴が無いときは増減 None（0と混同しない）OK")


# ---------------------------------------------------------------- 在庫ランウェイ

def _post(rid, dt, status=""):
    return {"row_id": rid, "account": "a1", "post_datetime": dt, "status": status}


def test_runway_counts_future_pending_only():
    posts = [
        _post("1", "2026-07-29 12:00", "queued"),   # 未来・公開対象
        _post("2", "2026-07-30 12:00", ""),         # 未来・空status も公開対象
        _post("3", "2026-07-29 12:00", "draft"),    # draft は公開されない
        _post("4", "2026-07-29 12:00", "retired"),  # retired も対象外
        _post("5", "2026-07-01 12:00", "posted"),   # 公開済み
    ]
    r = compute_runway(posts, "a1", now=NOW, posts_per_day=4)
    assert r["pending"] == 2, r
    assert r["last_at"].strftime("%Y-%m-%d %H:%M") == "2026-07-30 12:00", r
    assert r["days_left"] == 2, r["days_left"]      # 07-30 まで＝残り2日分
    assert r["overdue"] == 0
    print("  ✓ 在庫ランウェイ＝未来の公開対象だけを数える OK")


def test_runway_zero_inventory_is_detected():
    posts = [_post("1", "2026-07-19 12:00", "posted")]
    r = compute_runway(posts, "a1", now=NOW)
    assert r["pending"] == 0 and r["days_left"] == 0 and r["next_at"] is None
    assert r["last_posted_at"].strftime("%Y-%m-%d") == "2026-07-19"
    assert r["silent_days"] == 9, r["silent_days"]   # 07-19 → 07-28 で9日間の無投稿
    print("  ✓ 在庫ゼロ＋無投稿日数を検出（今回の障害の検知条件）OK")


def test_runway_counts_overdue_rows():
    # 公開時刻を過ぎているのに未公開＝cron停止やトークン失効の痕跡
    posts = [_post("1", "2026-07-27 12:00", "queued"), _post("2", "2026-07-29 12:00", "queued")]
    r = compute_runway(posts, "a1", now=NOW)
    assert r["overdue"] == 1 and r["pending"] == 1, r
    print("  ✓ 時刻到来済みの未公開行を overdue として分離 OK")


def test_runway_ignores_other_accounts():
    posts = [_post("1", "2026-07-29 12:00", "queued")]
    posts[0]["account"] = "other"
    r = compute_runway(posts, "a1", now=NOW)
    assert r["pending"] == 0
    print("  ✓ 他アカウントの在庫を混ぜない OK")


# ---------------------------------------------------------------- 生成エラー分類

def test_classify_generation_error():
    # 2026-07-19以降、4サイクル連続で同じ原因（残高不足）で落ちていたのに
    # 全部が同じ exit 2 で見分けられなかった。件名・レポートに理由を出すための分類。
    assert classify_generation_error(
        Exception("Error code: 400 - {'message': 'Your credit balance is too low to access the "
                  "Anthropic API. Please go to Plans & Billing'}")) == "残高不足"
    assert classify_generation_error(Exception("Error code: 401 - invalid x-api-key")) == "認証エラー"
    assert classify_generation_error(Exception("Error code: 429 - rate_limit_error")) == "レート制限"
    assert classify_generation_error(Exception("Error code: 529 - overloaded_error")) == "一時障害"
    assert classify_generation_error(Exception("なにか未知の失敗")) == "その他のエラー"
    print("  ✓ 生成エラーの分類（残高不足/認証/レート/一時障害）OK")


# ---------------------------------------------------------------- HTML

def test_html_shows_window_delta_runway_and_gen_failure():
    from threads_poster.html_report import build_html
    rows = [ins("c1", "2026-07-25 21:00", 300, 0.05), ins("p1", "2026-07-18 21:00", 100, 0.01)]
    a = analyze_windowed(rows, now=NOW)
    html = build_html("acc1", a, "2026-07-28", theme="seizo",
                      followers={"current": 162, "prev": 150, "delta": 12},
                      runway={"pending": 0, "days_left": 0, "overdue": 0, "silent_days": 9,
                              "next_at": None, "last_posted_at": None},
                      gen_info={"ok": False, "reason": "残高不足",
                                "detail": "credit balance is too low"})
    assert "07-22〜07-28" in html                     # 集計期間が明示される
    assert "前7日" in html                             # 比較対象が明示される
    assert "+200" in html or "200%" in html            # 増減率が出る
    assert "162" in html and "+12" in html             # フォロワー増減
    assert "在庫" in html and "残り0日" in html         # 在庫ランウェイのバッジ
    assert "残高不足" in html                          # 生成失敗が理由つきで見える
    assert html.startswith("<!doctype html>")
    print("  ✓ HTMLに 期間/前期比/フォロワー/在庫/生成失敗理由 が出る OK")


def test_html_backward_compatible_without_new_args():
    # 旧来の analysis 辞書（窓なし）でも落ちずに描画できること
    from threads_poster.html_report import build_html
    analysis = {"n_posts": 2, "total_views": 1900, "total_reactions": 5, "avg_er": 0.03,
                "by_time": [("夜(18-23)", 2, 900, 0.04)], "by_weekday": [], "by_length": [],
                "by_tree": [], "top": [], "top_er": []}
    html = build_html("acc1", analysis, "2026-07-28")
    assert html.startswith("<!doctype html>") and "1,900" in html
    print("  ✓ 旧形式の analysis でも後方互換で描画 OK")


def test_bottom_and_trend_rankings():
    """負けワースト3（views昇順）と、28日窓の trend_top/trend_bottom が出る。"""
    from threads_poster.analyzer import analyze_insights
    def row(pid, dt, views):
        return {"posted_id": pid, "snapshot_date": "2026-09-01",
                "post_datetime": dt, "views": views, "likes": 1,
                "replies": 0, "reposts": 0, "quotes": 0,
                "engagement_rate": 0.1, "text_len": 100}
    rows = [row("p1", "2026-08-30 12:00", 1000),   # 直近7日内・勝ち
            row("p2", "2026-08-29 12:00", 5),      # 直近7日内・負け
            row("p3", "2026-08-10 12:00", 800),    # 7日外・28日内
            row("p4", "2026-08-11 12:00", 2)]      # 7日外・28日内・負け
    a = analyze_insights(rows)
    assert [e["posted_id"] for e in a["bottom"]][:2] == ["p4", "p2"], a["bottom"]
    w = analyze_windowed(rows, now=datetime(2026, 9, 1, 6, 0))
    # trend_* は28日窓＝4本全部が対象。top先頭=p1(1000)、bottom先頭=p4(2)
    assert w["trend_top"][0]["posted_id"] == "p1", w["trend_top"]
    assert w["trend_bottom"][0]["posted_id"] == "p4", w["trend_bottom"]
    print("  ✓ 負けワースト3と28日窓の勝ち/負けランキング OK")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n全 {len(fns)} 件 PASS")


# ---------------------------------------------------------------- 事業別スイッチ

def test_gen_status_per_business():
    # 製造業は過去2回BAN済み。全事業共通の GEN_STATUS しか無いと
    # 「占いは自動公開・製造業は人が確認」ができなかった。
    from main_weekly import gen_status_for
    assert gen_status_for("uranai", {}, "queued") == "queued"                     # 既定にフォールバック
    assert gen_status_for("seizogyo", {"GEN_STATUS_SEIZOGYO": "draft"}, "queued") == "draft"
    assert gen_status_for("seizogyo", {"GEN_STATUS_SEIZOGYO": "  "}, "queued") == "queued"  # 空白は無視
    assert gen_status_for("seizogyo2", {"GEN_STATUS_SEIZOGYO2": "draft"}, "queued") == "draft"
    print("  ✓ GEN_STATUS_<NAME> による事業別 draft/queued 切替 OK")


def test_biz_label_covers_all_live_businesses():
    # 件名に内部キー（seizogyo2 等）が出ると社外の配信先に体裁が悪い
    from main_weekly import BIZ_LABEL, SCHEDULE_FN_BY_BUSINESS
    missing = [b for b in SCHEDULE_FN_BY_BUSINESS if b not in BIZ_LABEL]
    assert not missing, f"メール件名ラベル未登録の事業: {missing}"
    print("  ✓ 全事業にメール件名の日本語ラベルがある OK")


def test_trend_bottom_excludes_unaged_posts():
    """公開後48時間未満の投稿は views が未熟成のため trend_bottom（負け例）に入れない
    （新作＝低views→負け判定→次サイクルも避けられる、の自己強化ループを断つ）。"""
    rows = [
        ins("recent_low", "2026-07-28 01:00", 2),   # NOWの12時間前・views小 → 除外
        ins("old_low", "2026-07-25 13:00", 3),      # NOWの3日前・views小 → 対象
        ins("filler", "2026-07-20 13:00", 500),     # 28日窓内の埋め合わせ
    ]
    w = analyze_windowed(rows, now=NOW)
    bottom_ids = [e["posted_id"] for e in w["trend_bottom"]]
    assert "recent_low" not in bottom_ids, bottom_ids
    assert "old_low" in bottom_ids, bottom_ids
    print("  ✓ trend_bottom は公開後48時間未満の投稿を除外する OK")


def test_enrich_covers_trend_keys():
    """trend_top / trend_bottom にも本文が結合される（生成プロンプト注入の材料）。"""
    from main_weekly import enrich_tops_with_text
    posts = [{"account": "a1", "posted_id": "p1", "text": "勝ち本文"},
             {"account": "a1", "posted_id": "p9", "text": "負け本文"}]
    analysis = {"top": [{"posted_id": "p1"}], "top_er": [],
                "trend_top": [{"posted_id": "p1"}], "trend_bottom": [{"posted_id": "p9"}]}
    enrich_tops_with_text(posts, "a1", analysis)
    assert analysis["trend_top"][0]["text"] == "勝ち本文"
    assert analysis["trend_bottom"][0]["text"] == "負け本文"
