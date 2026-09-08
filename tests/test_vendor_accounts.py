"""外注アカウント（運用種別＝外注）の扱いを検証する。

背景（2026-09-08）: 自社4アカに加え、外注先が運用する2アカを**データ収集と外注管理のためだけ**に
システムへ載せる。外注アカは「こちらが投稿を書かない」ので、自社アカと同じ扱いにすると事故る:
  - 投稿してしまう（外注先のアカウントに勝手に投稿＝重大事故）
  - AI生成してしまう（不要な課金＋外注先の運用と二重投稿）
  - 在庫ゼロで在庫アラートが鳴り続ける（投稿しないのだから在庫は常にゼロ）
このテストは上の3つを機械で禁止する。収集だけは行う（＝主目的）。

実行: python3 -m pytest tests/test_vendor_accounts.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.sheets import is_outsourced  # noqa: E402


def test_empty_role_is_own_account():
    """運用種別が空＝自社。既存4アカのシートを一切触らずに今までどおり動くための後方互換。"""
    assert is_outsourced({"account": "own_sample"}) is False
    assert is_outsourced({"account": "own_sample", "role": ""}) is False
    assert is_outsourced({"account": "own_sample", "role": "   "}) is False
    print("  ✓ 運用種別が空/未設定なら自社扱い（後方互換）OK")


def test_gaichu_is_outsourced():
    """シートに「外注」と書かれていれば外注アカ。表記ゆれ・前後空白も拾う。"""
    assert is_outsourced({"account": "vendor_sample_a", "role": "外注"}) is True
    assert is_outsourced({"account": "vendor_sample_b", "role": " 外注 "}) is True
    assert is_outsourced({"account": "x", "role": "outsourced"}) is True
    assert is_outsourced({"account": "x", "role": "OUTSOURCED"}) is True
    print("  ✓ 「外注」/outsourced を外注アカと判定 OK")


def test_own_role_is_not_outsourced():
    """明示的に「自社」と書いた場合も当然 自社。"""
    assert is_outsourced({"account": "x", "role": "自社"}) is False
    assert is_outsourced({"account": "x", "role": "own"}) is False
    print("  ✓ 「自社」/own は自社扱い OK")


def test_unknown_role_is_treated_as_own():
    """未知の値は自社扱い（＝安全側ではない方に倒さない）。

    ここは判断が要る: 未知値を外注扱いにすると「投稿が突然止まる」、自社扱いにすると
    「外注アカに投稿してしまう」。後者のほうが被害が大きいように見えるが、外注アカは
    そもそも投稿タブ(投稿_<acc>)を作らない運用なので投稿対象の行が存在せず投稿は起きない。
    一方、自社アカの運用種別を打ち間違えただけで投稿が全停止するほうが実害が大きいため、
    未知値は自社扱いとし、代わりに呼び出し側で警告ログを出す方針にする。
    """
    assert is_outsourced({"account": "x", "role": "がいちゅう"}) is False
    assert is_outsourced({"account": "x", "role": "vendor"}) is False
    print("  ✓ 未知の運用種別は自社扱い（打ち間違いで投稿が全停止しない）OK")


# ---------------------------------------------------------------------------
# 投稿（publisher）: 外注アカには投稿しない／トークン更新だけは続ける
# ---------------------------------------------------------------------------
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from threads_poster.sheets import MemoryStore  # noqa: E402
from threads_poster.publisher import Publisher  # noqa: E402

TZ = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=TZ)


class FakeClient:
    """公開されたら記録するだけのダミー。実APIは叩かない（tests/test_logic.py と同じ契約）。"""
    calls: list = []

    def __init__(self, user_id, access_token):
        self.user_id = user_id

    def post(self, text=None, media_type="TEXT", image_url=None, video_url=None,
             media_urls=None, reply_to_id=None, reply_control=None):
        FakeClient.calls.append(("publish", self.user_id, text))
        return f"TID-{text}"


def _store_with_both_kinds():
    """自社1アカ・外注1アカ。どちらにも「今すぐ公開すべき行」が存在する状態を作る。

    外注アカに投稿タブは作らない運用だが、事故（人が誤って行を入れた・タブを流用した）でも
    投稿されないことを保証したいので、あえて公開対象の行を置いてテストする。
    """
    accounts = [
        {"account": "own_acc", "user_id": "u-own", "access_token": "t-own",
         "token_updated_at": "2026-09-08 00:00:00"},
        {"account": "vendor_acc", "user_id": "u-vendor", "access_token": "t-vendor",
         "token_updated_at": "2026-09-08 00:00:00", "role": "外注"},
    ]
    posts = [
        {"row_id": "own-1", "account": "own_acc", "post_datetime": "2026-09-08 11:00",
         "text": "自社の投稿", "status": "queued"},
        {"row_id": "vendor-1", "account": "vendor_acc", "post_datetime": "2026-09-08 11:00",
         "text": "外注アカに入り込んだ行", "status": "queued"},
    ]
    return MemoryStore(accounts, posts)


def test_publisher_never_posts_to_outsourced_account():
    """外注アカの行は公開対象から外れる。自社アカは今までどおり公開される。"""
    FakeClient.calls = []
    store = _store_with_both_kinds()
    pub = Publisher(store, client_factory=FakeClient, now_fn=lambda: NOW)
    res = pub.run()

    published_users = [c[1] for c in FakeClient.calls if c[0] == "publish"]
    assert "u-vendor" not in published_users, "外注アカに投稿してしまった（重大事故）"
    assert published_users == ["u-own"], f"自社アカの投稿が出ていない: {published_users}"
    assert res["posted"] == 1
    print("  ✓ 外注アカには投稿せず、自社アカだけ公開 OK")


def test_outsourced_row_is_not_marked_error():
    """外注アカの行を error にしない（毎回エラー通知が飛ぶのを防ぐ）。"""
    FakeClient.calls = []
    store = _store_with_both_kinds()
    Publisher(store, client_factory=FakeClient, now_fn=lambda: NOW).run()

    vendor_row = [p for p in store.posts if p["row_id"] == "vendor-1"][0]
    assert str(vendor_row.get("status") or "").lower() != "error", \
        "外注アカの行を error にすると毎回失敗メールが飛ぶ"
    assert not vendor_row.get("posted_id"), "外注アカの行に投稿後IDが書かれている"
    print("  ✓ 外注アカの行は error にせず素通り OK")


def test_token_refresh_still_covers_outsourced_accounts():
    """外注アカもトークン更新の対象に残す。60日で失効すると収集が止まるため。"""
    accounts = [
        {"account": "vendor_acc", "user_id": "u-vendor", "access_token": "t-vendor",
         "token_updated_at": "", "role": "外注"},
    ]
    store = MemoryStore(accounts, [])
    Publisher(store, client_factory=FakeClient, now_fn=lambda: NOW).run()

    assert store.accounts[0]["token_updated_at"] == "2026-09-08 12:00:00", \
        "外注アカがトークン更新の対象から漏れている（60日で失効し収集が止まる）"
    print("  ✓ 外注アカもトークン更新の対象に残る OK")


# ---------------------------------------------------------------------------
# 在庫監視（monitor）: 外注アカを監視対象から外す
# ---------------------------------------------------------------------------
from threads_poster.inventory import monitored_accounts  # noqa: E402


def test_monitor_excludes_outsourced_accounts():
    """外注アカは投稿しない＝在庫は常にゼロ。監視に含めると毎日 critical で鳴り続ける。

    在庫ゼロは run を exit 2（Actions が赤）にするため、外注アカを含めると
    「常に赤い」状態になり、本物の停止を見逃す。監視の意味そのものが壊れる。
    """
    rows = [
        {"account": "own_a"},
        {"account": "own_b", "role": "自社"},
        {"account": "vendor_a", "role": "外注"},
        {"account": "vendor_b", "role": "outsourced"},
    ]
    assert monitored_accounts(rows) == ["own_a", "own_b"]
    print("  ✓ 外注アカを在庫監視から除外 OK")


def test_monitor_skips_rows_without_account_name():
    """アカウント名が空の行（シートの空行）は無視する。既存 main_monitor と同じ挙動を保つ。"""
    rows = [{"account": ""}, {"account": None}, {"account": "own_a"}]
    assert monitored_accounts(rows) == ["own_a"]
    print("  ✓ 空アカウント行は無視 OK")


# ---------------------------------------------------------------------------
# 収集（collector）: 本文も保存する（使い回し率の算出に必要）
# ---------------------------------------------------------------------------
from threads_poster.collector import Collector  # noqa: E402


class FakeInsightClient:
    """投稿一覧とインサイトを返すダミー。API は叩かない。"""

    def __init__(self, user_id, access_token):
        self.user_id = user_id

    def get_user_insights(self, metrics=None):
        return {"views": 100, "followers_count": 10}

    def list_media(self):
        return [{"id": "m1", "permalink": "https://example.test/1",
                 "timestamp": "2026-09-07T12:00:00+0000", "media_type": "TEXT",
                 "text": "月収40万。寮あり。\n未経験OK。"}]

    def get_media_insights(self, media_id, metrics=None):
        return {"views": 60, "likes": 1, "replies": 0, "reposts": 0, "quotes": 0, "shares": 0}


def test_collector_saves_post_text():
    """本文をシートに保存する。使い回し率は本文の照合でしか出せないため。

    Threads API の投稿一覧は元から本文を返しており（threads_api.list_media の fields に text あり）、
    これまではコード側で本文長だけ保存して本文を捨てていた。API 呼び出しは増えない。
    """
    store = MemoryStore([{"account": "vendor_acc", "user_id": "u1",
                          "access_token": "t1", "role": "外注"}], [])
    Collector(store, client_factory=FakeInsightClient,
              now_fn=lambda: NOW).run()

    rows = store.get_insights("vendor_acc")
    assert rows, "インサイトが1件も保存されていない"
    assert rows[0].get("text") == "月収40万。寮あり。\n未経験OK。", \
        f"本文が保存されていない: {rows[0].get('text')!r}"
    print("  ✓ 収集時に本文を保存 OK")


def test_collector_still_saves_text_len():
    """既存の本文長も引き続き保存する（過去の分析・レポートが壊れないように）。"""
    store = MemoryStore([{"account": "vendor_acc", "user_id": "u1", "access_token": "t1"}], [])
    Collector(store, client_factory=FakeInsightClient,
              now_fn=lambda: NOW).run()

    assert store.get_insights("vendor_acc")[0]["text_len"] == len("月収40万。寮あり。\n未経験OK。")
    print("  ✓ 本文長も従来どおり保存 OK")


# ---------------------------------------------------------------------------
# 外注管理指標（vendor.summarize_activity）: 純関数・AI不使用
# ---------------------------------------------------------------------------
from threads_poster.vendor import summarize_activity  # noqa: E402


def _ins(pid, dt, text, views=10):
    """インサイト1行ぶんの最小データ（collector が書く形）。"""
    return {"posted_id": pid, "post_datetime": dt, "text": text, "views": views,
            "snapshot_date": "2026-09-08"}


def test_counts_posts_and_active_days():
    """投稿本数と稼働日数。外注が実際に手を動かした量の基本指標。"""
    rows = [
        _ins("1", "2026-09-04 10:00", "A"),
        _ins("2", "2026-09-04 20:00", "B"),
        _ins("3", "2026-09-06 15:00", "C"),
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["posts"] == 3
    assert s["active_days"] == 2      # 09-04 と 09-06
    print("  ✓ 投稿本数・稼働日数 OK")


def test_reuse_rate_counts_duplicate_texts():
    """使い回し率＝「実際に何本書いたか」を測る中心指標。

    同じ本文（空白を除いて完全一致）の2回目以降を使い回しと数える。
    今回の実測では151投稿中60本(40%)が使い回しで、これが制作量の実態だった。
    """
    rows = [
        _ins("1", "2026-09-04 10:00", "月収40万。寮あり。"),
        _ins("2", "2026-09-05 10:00", "月収40万。 寮あり。"),   # 空白違い＝同一とみなす
        _ins("3", "2026-09-06 10:00", "未経験OK。"),
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["posts"] == 3
    assert s["unique_texts"] == 2
    assert s["reused"] == 1
    assert s["reuse_rate"] == 33       # 1/3 を四捨五入した%
    print("  ✓ 使い回し率（空白差を無視した完全一致）OK")


def test_empty_text_is_not_counted_as_duplicate():
    """本文が空の行（取得漏れ）を「全部同じ本文」と誤判定しない。

    本文列を追加する前に収集された過去行は本文が空になる。これを重複と数えると
    使い回し率が100%に見えて誤った判断につながるため、集計から除外する。
    """
    rows = [
        _ins("1", "2026-09-04 10:00", ""),
        _ins("2", "2026-09-05 10:00", ""),
        _ins("3", "2026-09-06 10:00", "本文あり"),
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["posts"] == 3
    assert s["unique_texts"] == 1      # 本文がある1本だけ
    assert s["reused"] == 0
    assert s["text_missing"] == 2      # 本文が取れていない件数を明示して誤読を防ぐ
    print("  ✓ 本文が空の行を重複と誤判定しない OK")


def test_hour_distribution():
    """投稿時間帯。今回の分析で「午後12〜16時が強い」と分かった軸。"""
    rows = [
        _ins("1", "2026-09-04 10:30", "A"),
        _ins("2", "2026-09-04 15:00", "B"),
        _ins("3", "2026-09-05 15:40", "C"),
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["by_hour"][10] == 1
    assert s["by_hour"][15] == 2
    assert s["by_hour"][3] == 0
    print("  ✓ 時間帯分布 OK")


def test_window_excludes_older_posts():
    """集計期間（既定7日）の外は数えない。週次レポート用の窓。"""
    rows = [
        _ins("1", "2026-09-07 10:00", "新しい"),
        _ins("2", "2026-08-01 10:00", "古い"),
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["posts"] == 1
    print("  ✓ 期間窓の外を除外 OK")


def test_dedups_daily_snapshots_of_same_post():
    """同じ投稿の日次スナップショットを重複カウントしない。

    インサイトは posted_id × 取得日 で毎日1行ずつ積まれる。素直に数えると
    「1本の投稿が7本」に見え、外注の作業量を過大評価してしまう。
    """
    rows = [
        _ins("1", "2026-09-06 10:00", "同じ投稿", views=10) | {"snapshot_date": "2026-09-06"},
        _ins("1", "2026-09-06 10:00", "同じ投稿", views=50) | {"snapshot_date": "2026-09-07"},
    ]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["posts"] == 1, "同じ投稿の日次スナップショットを別々に数えている"
    assert s["views_total"] == 50, "最新スナップショットの表示数を使うべき"
    print("  ✓ 日次スナップショットの重複排除（最新を採用）OK")


def test_no_posts_is_reported_not_crashed():
    """投稿ゼロでも落ちない（外注が完全に止まった週こそ検知したい）。"""
    s = summarize_activity([], days=7, today=NOW.date())
    assert s["posts"] == 0 and s["active_days"] == 0
    assert s["reuse_rate"] == 0 and s["unique_texts"] == 0
    print("  ✓ 投稿ゼロでも算出できる OK")


# ---------------------------------------------------------------------------
# 週次（weekly）: 外注アカはAI生成の対象にしない
# ---------------------------------------------------------------------------
from main_weekly import should_generate_for_account  # noqa: E402


def test_weekly_never_generates_for_outsourced_account():
    """外注アカにAI生成をかけない。

    理由は2つ。(1) 生成した投稿は誰も公開しない（外注アカに投稿しないため）ので
    ANTHROPIC_API_KEY の課金だけが増える。(2) 外注先が自分で書いた投稿と二重になる。
    """
    ok, reason = should_generate_for_account({"account": "vendor_acc", "role": "外注"})
    assert ok is False
    assert "外注" in reason, f"理由が分かる文言になっていない: {reason}"
    print("  ✓ 外注アカは生成しない（理由つき）OK")


def test_weekly_still_generates_for_own_account():
    """自社アカは今までどおり生成対象（この判定でしか止めない）。"""
    ok, _ = should_generate_for_account({"account": "own_acc"})
    assert ok is True
    ok2, _ = should_generate_for_account({"account": "own_acc", "role": "自社"})
    assert ok2 is True
    print("  ✓ 自社アカは生成対象のまま OK")


# ---------------------------------------------------------------------------
# 外注管理レポート（HTML）
# ---------------------------------------------------------------------------
from threads_poster.html_report import build_vendor_report  # noqa: E402


def _vendor_row(account, **over):
    base = {"account": account, "business": "seizogyo", "posts": 12, "active_days": 5,
            "idle_days": 2, "unique_texts": 7, "reused": 5, "reuse_rate": 42,
            "text_missing": 0, "by_hour": [0] * 24, "views_total": 800,
            "views_median": 20, "low_views": 3, "days": 7}
    base.update(over)
    return base


def test_vendor_report_shows_each_account():
    """外注2アカを1通にまとめる（アカウントごとに別メールだと管理しづらい）。"""
    html = build_vendor_report([_vendor_row("vendor_sample_a"), _vendor_row("vendor_sample_b")],
                               "2026-09-08 06:00")
    assert "vendor_sample_a" in html and "vendor_sample_b" in html
    assert html.lstrip().startswith("<")
    print("  ✓ 外注レポートに全アカウントが載る OK")


def test_vendor_report_shows_reuse_rate():
    """使い回し率＝実際の制作量。外注管理で最も見たい数字なのでレポートに必ず出す。"""
    html = build_vendor_report([_vendor_row("v1", reuse_rate=78, unique_texts=5, posts=23)],
                               "2026-09-08 06:00")
    assert "78" in html, "使い回し率が出ていない"
    assert "5" in html, "新規本文数が出ていない"
    print("  ✓ 使い回し率と新規本文数を表示 OK")


def test_vendor_report_flags_no_new_text():
    """新規本文ゼロ＝その週は1本も書いていない。埋もれないよう強調する。

    実測では 9/3・9/4・9/5 の3日間が新規ゼロ（全て使い回し）だった。
    数字を並べるだけだと見落とすので、レポート側で警告として出す。
    """
    html = build_vendor_report([_vendor_row("v1", unique_texts=0, reused=8, reuse_rate=100)],
                               "2026-09-08 06:00")
    assert "要確認" in html, "新規ゼロが警告として出ていない"
    print("  ✓ 新規本文ゼロを警告表示 OK")


def test_vendor_report_handles_zero_posts():
    """投稿ゼロの週でも落ちずにレポートを出す（止まったことこそ知りたい）。"""
    html = build_vendor_report([_vendor_row("v1", posts=0, active_days=0, unique_texts=0,
                                            reused=0, reuse_rate=0, views_total=0,
                                            views_median=0, low_views=0)],
                               "2026-09-08 06:00")
    assert "v1" in html and "要確認" in html
    print("  ✓ 投稿ゼロでもレポートが出る OK")


# ---------------------------------------------------------------------------
# 週次の配線: 外注アカだけを集めて作業量レポートの材料にする
# ---------------------------------------------------------------------------
from main_weekly import vendor_activity_rows  # noqa: E402


def test_vendor_activity_rows_only_includes_outsourced():
    """自社アカは外注レポートに混ぜない（自社は従来の週次レポートで見る）。"""
    accounts = [{"account": "own_a"}, {"account": "vendor_a", "role": "外注"}]
    insights = [
        {"account": "own_a", "posted_id": "o1", "post_datetime": "2026-09-06 10:00",
         "text": "自社", "views": 100, "snapshot_date": "2026-09-08"},
        {"account": "vendor_a", "posted_id": "v1", "post_datetime": "2026-09-06 10:00",
         "text": "外注", "views": 50, "snapshot_date": "2026-09-08"},
    ]
    rows = vendor_activity_rows(MemoryStore(accounts, [], insights=insights),
                                accounts, business="seizogyo", today=NOW.date())
    assert [r["account"] for r in rows] == ["vendor_a"]
    assert rows[0]["business"] == "seizogyo"
    assert rows[0]["posts"] == 1
    print("  ✓ 外注アカだけを集計 OK")


def test_vendor_activity_rows_empty_when_no_outsourced():
    """外注アカが1つも無い事業では空リスト（＝外注メールを送らない）。"""
    accounts = [{"account": "own_a"}, {"account": "own_b", "role": "自社"}]
    assert vendor_activity_rows(MemoryStore(accounts, [], insights=[]),
                                accounts, business="meguri", today=NOW.date()) == []
    print("  ✓ 外注アカが無ければ空 OK")


# ---------------------------------------------------------------------------
# 中央値を主指標にする（2026-09-06 横断分析設計の必須要件を取り込み）
#
# 「連携直後に委託先アカウントの1ヶ月分を実測したところ、平均表示と中央値表示で評価が
#  逆転するケースが実際に出た。1本の突出した投稿が平均を押し上げ、残り半数が表示ひと桁」
# → 平均だけで判断すると外注先への指示が逆になるため、中央値を主・平均を併記する。
# ---------------------------------------------------------------------------

def test_mean_and_median_are_both_reported():
    """平均と中央値の両方を出す。乖離そのものが判断材料になるため。"""
    rows = [_ins(str(i), "2026-09-06 10:00", f"本文{i}", views=v)
            for i, v in enumerate([1, 2, 3, 4, 2000], start=1)]
    s = summarize_activity(rows, days=7, today=NOW.date())
    assert s["views_median"] == 3, "中央値が実態（ほとんど伸びていない）を表していない"
    assert s["views_mean"] == 402, "平均が併記されていない"
    print("  ✓ 中央値と平均を併記（乖離が見える）OK")


def test_report_marks_median_as_primary():
    """レポート上で中央値が主指標だと分かるようにする（平均だけ見て誤判断させない）。"""
    html = build_vendor_report(
        [_vendor_row("v1", views_median=3, views_mean=402, views_total=2010)],
        "2026-09-08 06:00")
    assert "中央値" in html
    assert "402" in html, "平均が併記されていない"
    assert "主" in html or "実態" in html, "どちらを見るべきかの手がかりが無い"
    print("  ✓ レポートで中央値が主指標と分かる OK")


# ---------------------------------------------------------------------------
# トークン取得スクリプト: 外注アカには投稿権限を付けない
#
# 2026-09-06 横断分析設計の「やらないこと」より:
#   「分析専用アカウントへの自動投稿（トークンに投稿権限を付与しない）」
# コード側で投稿を止めているだけでなく、**トークンそのものに投稿権限を持たせない**ことで
# 二重に守る（万一コードの分岐をすり抜けても、APIが投稿を拒否する）。
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402


def _load_script(name):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_auth_scope_for_outsourced_has_no_publish_permission():
    """外注アカ用のスコープに threads_content_publish を含めない。"""
    gau = _load_script("get_auth_url")
    scope = gau.build_scope(collect_only=True)
    assert "threads_content_publish" not in scope, \
        "外注アカのトークンに投稿権限が付いている（設計の『やらないこと』違反）"
    assert "threads_basic" in scope and "threads_manage_insights" in scope, \
        "収集に必要な権限が欠けている"
    print("  ✓ 外注アカ用スコープは収集のみ（投稿権限なし）OK")


def test_auth_scope_for_own_account_keeps_publish_permission():
    """自社アカは従来どおり投稿権限を含む（既存の取得手順を壊さない）。

    §19 の教訓: 過去に投稿スコープだけで取得し、収集開始時に取り直す羽目になった。
    自社アカは投稿と収集の両方が必要。
    """
    gau = _load_script("get_auth_url")
    scope = gau.build_scope(collect_only=False)
    for s in ("threads_basic", "threads_content_publish", "threads_manage_insights"):
        assert s in scope, f"{s} が欠けている"
    print("  ✓ 自社アカ用スコープは従来どおり OK")


# ---------------------------------------------------------------------------
# 登録スクリプト: 外注アカにテスト投稿を作らせない
# ---------------------------------------------------------------------------

def test_setup_account_refuses_test_post_for_outsourced():
    """外注アカに --add-test-post を付けたら実行前に止める。

    テスト投稿は「投稿_<acc>」タブに行を作る。外注アカは投稿タブを作らない運用なので、
    ここで作ってしまうと運用ルールが崩れる（publisher は止めるが、シートに不要な行が残る）。
    人の操作ミスをスクリプト側で拒否する。
    """
    sa = _load_script("setup_account")
    ok, reason = sa.validate_options(role="外注", add_test_post=True)
    assert ok is False
    assert "外注" in reason and "テスト投稿" in reason, f"理由が不親切: {reason}"
    print("  ✓ 外注アカへのテスト投稿を拒否 OK")


def test_setup_account_allows_test_post_for_own():
    """自社アカは従来どおりテスト投稿を作れる（既存手順を壊さない）。"""
    sa = _load_script("setup_account")
    assert sa.validate_options(role="", add_test_post=True)[0] is True
    assert sa.validate_options(role="自社", add_test_post=True)[0] is True
    print("  ✓ 自社アカのテスト投稿は従来どおり OK")


def test_setup_account_rejects_unknown_role():
    """運用種別の打ち間違いを登録時に弾く。

    is_outsourced() は未知の値を自社扱いにする（投稿が全停止しない方に倒す）ため、
    「外注のつもりで打ち間違えた」場合は自社扱いになってしまう。登録の入口で止める。
    """
    sa = _load_script("setup_account")
    ok, reason = sa.validate_options(role="がいちゅう", add_test_post=False)
    assert ok is False
    assert "運用種別" in reason
    print("  ✓ 運用種別の打ち間違いを登録時に弾く OK")
