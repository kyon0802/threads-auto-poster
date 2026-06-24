"""Collector のロジック検証（API不要・モック）。実行: python3 test_collect.py

prior art: test_logic.py（FakeClient / MemoryStore / now_fn 注入で「外部挙動のみ」を検証）。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from threads_poster.sheets import MemoryStore
from threads_poster.collector import Collector
from threads_poster.threads_api import ThreadsAPIError

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 6, 24, 4, 0, tzinfo=JST)


class FakeClient:
    """list_media / get_media_insights / get_user_insights を返すモック（実APIを叩かない）。"""
    def __init__(self, user_id, access_token, media, insights, user, fail_media):
        self.user_id = user_id
        self._media = media
        self._insights = insights
        self._user = user
        self._fail = set(fail_media or [])

    def list_media(self, **kw):
        return self._media

    def get_media_insights(self, media_id, **kw):
        if media_id in self._fail:
            raise ThreadsAPIError(f"insights失敗 {media_id}")
        return self._insights.get(media_id, {})

    def get_user_insights(self, **kw):
        return self._user


def factory_for(media, insights, user, fail_media=None):
    def f(user_id, access_token):
        return FakeClient(user_id, access_token, media, insights, user, fail_media)
    return f


def acc(name="a1"):
    return [{"account": name, "user_id": "111", "access_token": "tok"}]


def test_basic_and_engagement():
    media = [{"id": "m1", "permalink": "http://x/1", "timestamp": "2026-06-20T10:00:00+0000",
              "media_type": "TEXT", "text": "hello"}]
    insights = {"m1": {"views": 100, "likes": 5, "replies": 3, "reposts": 1, "quotes": 1, "shares": 0}}
    store = MemoryStore(acc(), [])
    res = Collector(store, client_factory=factory_for(media, insights, {"followers_count": 50}),
                    now_fn=lambda: NOW).run()
    assert res["media"] == 1 and res["accounts"] == 1 and res["errors"] == 0, res
    row = store.insights[0]
    # エンゲージ率 = (5+3+1+1)/100 = 0.1
    assert row["views"] == 100 and row["engagement_rate"] == round(10 / 100, 4), row
    assert row["snapshot_date"] == "2026-06-24" and row["text_len"] == 5, row
    assert store.account_metrics[0]["followers_count"] == 50
    print("  ✓ 基本収集＋エンゲージ率算出 OK")


def test_idempotent_same_day():
    media = [{"id": "m1", "timestamp": "2026-06-20T10:00:00+0000", "text": "x"}]
    insights = {"m1": {"views": 10, "likes": 1, "replies": 0, "reposts": 0, "quotes": 0}}
    store = MemoryStore(acc(), [])
    c = Collector(store, client_factory=factory_for(media, insights, {}), now_fn=lambda: NOW)
    c.run(); c.run()  # 同じ snapshot_date で2回
    assert len(store.insights) == 1, f"行が重複した: {len(store.insights)}"
    insights["m1"]["views"] = 999  # 値が変われば上書きされる
    c.run()
    assert len(store.insights) == 1 and store.insights[0]["views"] == 999, store.insights
    print("  ✓ 同日2回でも行が増えず上書き（冪等）OK")


def test_lookback_excludes_old():
    media = [{"id": "old", "timestamp": "2026-01-01T00:00:00+0000", "text": "old"},
             {"id": "new", "timestamp": "2026-06-20T00:00:00+0000", "text": "new"}]
    insights = {"old": {"views": 1}, "new": {"views": 2}}
    store = MemoryStore(acc(), [])
    res = Collector(store, client_factory=factory_for(media, insights, {}),
                    now_fn=lambda: NOW, lookback_days=30).run()
    ids = {r["posted_id"] for r in store.insights}
    assert ids == {"new"} and res["skipped_old"] == 1, (ids, res)
    print("  ✓ lookback 外の古い投稿を除外 OK")


def test_dry_run_no_write():
    media = [{"id": "m1", "timestamp": "2026-06-20T00:00:00+0000", "text": "x"}]
    insights = {"m1": {"views": 10, "likes": 1}}
    store = MemoryStore(acc(), [])
    Collector(store, client_factory=factory_for(media, insights, {"followers_count": 9}),
              now_fn=lambda: NOW, dry_run=True).run()
    assert store.insights == [] and store.account_metrics == [], "dry_run で書込が発生した"
    print("  ✓ dry_run はシートに一切書き込まない OK")


def test_one_media_error_does_not_stop():
    media = [{"id": "good", "timestamp": "2026-06-20T00:00:00+0000", "text": "g"},
             {"id": "bad", "timestamp": "2026-06-20T00:00:00+0000", "text": "b"}]
    insights = {"good": {"views": 5, "likes": 1}}
    store = MemoryStore(acc(), [])
    res = Collector(store, client_factory=factory_for(media, insights, {}, fail_media=["bad"]),
                    now_fn=lambda: NOW).run()
    ids = {r["posted_id"] for r in store.insights}
    assert ids == {"good"} and res["errors"] == 1 and res["media"] == 1, (ids, res)
    print("  ✓ 1件のメディアエラーでも収集全体は止まらない OK")


def test_join_row_id_and_tree():
    media = [{"id": "mZ", "timestamp": "2026-06-20T00:00:00+0000", "text": "child"}]
    insights = {"mZ": {"views": 10, "likes": 2, "replies": 0, "reposts": 0, "quotes": 0}}
    posts = [{"account": "a1", "row_id": "a1-002", "posted_id": "mZ", "reply_to": "a1-001"}]
    store = MemoryStore(acc(), posts)
    Collector(store, client_factory=factory_for(media, insights, {}), now_fn=lambda: NOW).run()
    row = store.insights[0]
    assert row["row_id"] == "a1-002" and row["is_tree"] == "ツリー", row
    print("  ✓ 投稿タブとの結合（row_id / ツリー有無）OK")


def test_missing_views_blank_engagement():
    # views が返らない（開発中 metric）場合、エンゲージ率は空にして誤誘導を避ける
    media = [{"id": "m1", "timestamp": "2026-06-20T00:00:00+0000", "text": "x"}]
    insights = {"m1": {"likes": 4, "replies": 1}}  # views 欠落
    store = MemoryStore(acc(), [])
    Collector(store, client_factory=factory_for(media, insights, {}), now_fn=lambda: NOW).run()
    assert store.insights[0]["engagement_rate"] == "", store.insights[0]
    print("  ✓ views 欠落時はエンゲージ率を空に OK")


if __name__ == "__main__":
    print("=== Collector テスト ===")
    test_basic_and_engagement()
    test_idempotent_same_day()
    test_lookback_excludes_old()
    test_dry_run_no_write()
    test_one_media_error_does_not_stop()
    test_join_row_id_and_tree()
    test_missing_views_blank_engagement()
    print("========== 全テスト PASS ==========")
