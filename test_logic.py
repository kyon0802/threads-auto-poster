"""
ロジック検証用テスト（API不要）。
fake clientで「公開」を擬似実行し、ツリー連結・時刻判定・レート制限・保留を確認。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from threads_poster.sheets import MemoryStore
from threads_poster.publisher import Publisher

TZ = ZoneInfo("Asia/Tokyo")


class FakeClient:
    """publish時に reply_to_id が正しく渡るか記録する擬似クライアント。"""
    instances = []

    def __init__(self, user_id, access_token):
        self.user_id = user_id
        self.calls = []
        FakeClient.instances.append(self)

    def post(self, text=None, media_type="TEXT", image_url=None, video_url=None,
             media_urls=None, reply_to_id=None, reply_control=None):
        call = {"text": text, "media_type": media_type, "reply_to_id": reply_to_id,
                "media_urls": media_urls}
        self.calls.append(call)
        # 返すIDは textに紐づけて識別しやすく
        return f"TID-{text}"


def make_store():
    accounts = [
        {"account": "uranai", "user_id": "111", "access_token": "tok-uranai",
         "token_updated_at": "2026-06-08 00:00:00", "daily_count": "", "daily_count_date": ""},
        {"account": "rerise", "user_id": "222", "access_token": "tok-rerise",
         "token_updated_at": "2026-06-08 00:00:00", "daily_count": "", "daily_count_date": ""},
    ]
    posts = [
        # ツリー: 親P1 -> 子P2 -> 孫P3 (uranai)
        {"row_id": "P1", "account": "uranai", "post_datetime": "2026-06-08 09:00",
         "text": "親", "media_type": "TEXT", "media_url": "", "reply_to": "",
         "reply_control": "", "status": "", "posted_id": "", "posted_at": "", "error": ""},
        {"row_id": "P2", "account": "uranai", "post_datetime": "2026-06-08 09:01",
         "text": "子", "media_type": "TEXT", "media_url": "", "reply_to": "P1",
         "reply_control": "", "status": "", "posted_id": "", "posted_at": "", "error": ""},
        {"row_id": "P3", "account": "uranai", "post_datetime": "2026-06-08 09:02",
         "text": "孫", "media_type": "TEXT", "media_url": "", "reply_to": "P2",
         "reply_control": "", "status": "", "posted_id": "", "posted_at": "", "error": ""},
        # 別アカウント単発
        {"row_id": "R1", "account": "rerise", "post_datetime": "2026-06-08 08:30",
         "text": "求人", "media_type": "TEXT", "media_url": "", "reply_to": "",
         "reply_control": "everyone", "status": "", "posted_id": "", "posted_at": "", "error": ""},
        # まだ未来 -> 公開されないはず
        {"row_id": "F1", "account": "uranai", "post_datetime": "2026-06-09 09:00",
         "text": "未来", "media_type": "TEXT", "media_url": "", "reply_to": "",
         "reply_control": "", "status": "", "posted_id": "", "posted_at": "", "error": ""},
    ]
    return MemoryStore(accounts, posts)


def fixed_now():
    return datetime(2026, 6, 8, 9, 5, tzinfo=TZ)


print("=== TEST 1: 通常実行 (親→子→孫が正しい順序とreply_to_idで公開) ===")
FakeClient.instances = []
store = make_store()
pub = Publisher(store, client_factory=FakeClient, now_fn=fixed_now, max_posts_per_day=50)
res = pub.run()
print("結果:", res)

posts = {p["row_id"]: p for p in store.get_posts()}
print("\n各行の状態:")
for rid in ["P1", "P2", "P3", "R1", "F1"]:
    p = posts[rid]
    print(f"  {rid}: status={p['status']:7} posted_id={p['posted_id']:8} reply_to={p['reply_to'] or '-'}")

# 検証
assert posts["P1"]["status"] == "posted"
assert posts["P2"]["status"] == "posted"
assert posts["P3"]["status"] == "posted"
assert posts["R1"]["status"] == "posted"
assert posts["F1"]["status"] == "", "未来の投稿は公開されてはいけない"

# reply_to_id が「親のposted_id」で渡っているか
calls = {}
for inst in FakeClient.instances:
    for c in inst.calls:
        calls[c["text"]] = c
assert calls["親"]["reply_to_id"] is None
assert calls["子"]["reply_to_id"] == "TID-親", f"子のreply_to_id不正: {calls['子']['reply_to_id']}"
assert calls["孫"]["reply_to_id"] == "TID-子", f"孫のreply_to_id不正: {calls['孫']['reply_to_id']}"
assert calls["求人"]["reply_to_id"] is None
print("\n  ✓ ツリーの親子連結 OK (子→親ID, 孫→子ID)")
print("  ✓ 未来投稿は未公開 OK")

# アカウント別カウント
accts = {a["account"]: a for a in store.get_accounts()}
print(f"\n  daily_count uranai={accts['uranai']['daily_count']} (期待3), rerise={accts['rerise']['daily_count']} (期待1)")
assert accts["uranai"]["daily_count"] == 3
assert accts["rerise"]["daily_count"] == 1
print("  ✓ アカウント別カウント OK")


print("\n=== TEST 2: 親が未来 → 子は保留(deferred)され公開されない ===")
FakeClient.instances = []
store2 = make_store()
# 親P1を未来にずらす
for p in store2.get_posts():
    if p["row_id"] == "P1":
        p["post_datetime"] = "2026-06-08 23:00"  # now(9:05)より後
pub2 = Publisher(store2, client_factory=FakeClient, now_fn=fixed_now)
res2 = pub2.run()
print("結果:", res2)
posts2 = {p["row_id"]: p for p in store2.get_posts()}
assert posts2["P1"]["status"] == "", "親(未来)は未公開のはず"
assert posts2["P2"]["status"] == "", "親未公開なら子も保留(未公開)のはず"
assert res2["deferred"] >= 1
print("  ✓ 親未公開時、子は保留され誤投稿しない OK")


print("\n=== TEST 3: レート制限 (max=1) ===")
FakeClient.instances = []
store3 = make_store()
pub3 = Publisher(store3, client_factory=FakeClient, now_fn=fixed_now, max_posts_per_day=1)
res3 = pub3.run()
print("結果:", res3)
accts3 = {a["account"]: a for a in store3.get_accounts()}
# uranaiは上限1なので1件だけ公開、残りはskip
assert accts3["uranai"]["daily_count"] == 1, f"上限超過: {accts3['uranai']['daily_count']}"
print("  ✓ 1アカウント1日上限を超えない OK")

print("\n=== TEST 4: 再実行で二重投稿しない (冪等性) ===")
FakeClient.instances = []
store4 = make_store()
pub4 = Publisher(store4, client_factory=FakeClient, now_fn=fixed_now)
pub4.run()
first = sum(len(i.calls) for i in FakeClient.instances)
FakeClient.instances = []
pub4.run()  # 2回目
second = sum(len(i.calls) for i in FakeClient.instances)
print(f"  1回目公開数={first}, 2回目公開数={second} (期待0)")
assert second == 0, "既にpostedの行を再投稿してはいけない"
print("  ✓ 冪等性 OK (2回目は何も投稿しない)")

print("\n=== TEST 5: CAROUSEL の media_url を分割して media_urls で渡す ===")
FakeClient.instances = []
accounts5 = [
    {"account": "acc", "user_id": "1", "access_token": "t",
     "token_updated_at": "2026-06-08 00:00:00", "daily_count": "", "daily_count_date": ""},
]
posts5 = [
    {"row_id": "C1", "account": "acc", "post_datetime": "2026-06-08 09:00",
     "text": "カルーセル", "media_type": "CAROUSEL",
     "media_url": "https://x/a.jpg, https://x/b.jpg", "reply_to": "",
     "reply_control": "", "status": "", "posted_id": "", "posted_at": "", "error": ""},
]
store5 = MemoryStore(accounts5, posts5)
pub5 = Publisher(store5, client_factory=FakeClient, now_fn=fixed_now)
pub5.run()
c1 = next(c for inst in FakeClient.instances for c in inst.calls if c["text"] == "カルーセル")
assert c1["media_type"] == "CAROUSEL"
assert c1["media_urls"] == ["https://x/a.jpg", "https://x/b.jpg"], f"分割不正: {c1['media_urls']}"
assert {p["row_id"]: p for p in store5.get_posts()}["C1"]["status"] == "posted"
print("  ✓ CAROUSELのmedia_urlを2件に分割して渡す OK")


print("\n=== TEST 6: DRY_RUN はシートを書き換えない ===")
FakeClient.instances = []
store6 = make_store()
pub6 = Publisher(store6, client_factory=FakeClient, now_fn=fixed_now, dry_run=True)
res6 = pub6.run()
print("結果:", res6)
posts6 = {p["row_id"]: p for p in store6.get_posts()}
for rid in ["P1", "P2", "P3", "R1"]:
    assert posts6[rid]["status"] == "", f"dry-runで {rid} の status が書き換わった: {posts6[rid]['status']!r}"
accts6 = {a["account"]: a for a in store6.get_accounts()}
assert accts6["uranai"]["daily_count"] in ("", None), "dry-runで daily_count が書き換わってはいけない"
assert res6["posted"] >= 1, "dry-runでも公開対象は検出する"
assert sum(len(i.calls) for i in FakeClient.instances) == 0, "dry-runで実APIを叩いてはいけない"
print("  ✓ DRY_RUN中はstore未変更・実API未実行・対象検出のみ OK")


print("\n=== TEST 7: with_retry が一過性(502)エラーを再試行して成功する ===")
from threads_poster.sheets import with_retry


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAPIError(Exception):
    """gspread.exceptions.APIError を模した擬似例外（.response.status_code を持つ）。"""
    def __init__(self, status_code):
        super().__init__(f"APIError {status_code}")
        self.response = _FakeResp(status_code)


_attempts = {"n": 0}
_slept = []
def _flaky_then_ok():
    _attempts["n"] += 1
    if _attempts["n"] < 3:
        raise _FakeAPIError(502)
    return "OK"
_result = with_retry(_flaky_then_ok, attempts=5, base_delay=0.01,
                     sleep=lambda d: _slept.append(d))
assert _result == "OK", _result
assert _attempts["n"] == 3, _attempts
assert len(_slept) == 2, f"sleep回数が不正(2を期待): {_slept}"
print(f"  ✓ 502を2回再試行して3回目で成功 OK (backoff={_slept})")


print("\n=== TEST 8: with_retry が非一過性(404)は再試行せず即送出する ===")
_attempts2 = {"n": 0}
def _hard_fail():
    _attempts2["n"] += 1
    raise _FakeAPIError(404)
try:
    with_retry(_hard_fail, attempts=5, base_delay=0.01, sleep=lambda d: None)
    assert False, "404 は送出されるべき"
except _FakeAPIError as e:
    assert e.response.status_code == 404
assert _attempts2["n"] == 1, f"非一過性は1回で諦めるべき: {_attempts2['n']}"
print("  ✓ 404は再試行せず即エラー OK")


print("\n=== TEST 9: with_retry が試行回数を使い切ったら最後の一過性エラーを送出 ===")
_attempts3 = {"n": 0}
def _always_503():
    _attempts3["n"] += 1
    raise _FakeAPIError(503)
try:
    with_retry(_always_503, attempts=3, base_delay=0.01, sleep=lambda d: None)
    assert False, "試行を使い切ったら送出されるべき"
except _FakeAPIError as e:
    assert e.response.status_code == 503
assert _attempts3["n"] == 3, f"attempts=3 のはず: {_attempts3['n']}"
print("  ✓ 試行を使い切ったら一過性エラーを送出 OK")


print("\n=== TEST 10: with_retry が network例外(応答前の接続断/タイムアウト)も再試行する ===")
import requests
_attempts4 = {"n": 0}
def _conn_then_ok():
    _attempts4["n"] += 1
    if _attempts4["n"] < 2:
        raise requests.exceptions.ConnectionError("connection reset by peer")  # .response is None
    return "OK"
_r4 = with_retry(_conn_then_ok, attempts=4, base_delay=0.01, sleep=lambda d: None)
assert _r4 == "OK", _r4
assert _attempts4["n"] == 2, f"ConnectionError は再試行されるべき: {_attempts4['n']}"
# ReadTimeout も同様
_attempts5 = {"n": 0}
def _timeout_then_ok():
    _attempts5["n"] += 1
    if _attempts5["n"] < 2:
        raise requests.exceptions.ReadTimeout("read timed out")
    return "OK"
assert with_retry(_timeout_then_ok, attempts=4, base_delay=0.01, sleep=lambda d: None) == "OK"
assert _attempts5["n"] == 2
print("  ✓ ConnectionError / ReadTimeout を再試行して回復 OK")


print("\n=== TEST 11: with_retry は一過性でない通常例外(ValueError)を再試行しない ===")
_attempts6 = {"n": 0}
def _value_error():
    _attempts6["n"] += 1
    raise ValueError("bug, not transient")
try:
    with_retry(_value_error, attempts=5, base_delay=0.01, sleep=lambda d: None)
    assert False, "ValueError は即送出されるべき"
except ValueError:
    pass
assert _attempts6["n"] == 1, f"非一過性は1回で諦めるべき: {_attempts6['n']}"
print("  ✓ ValueError等の非一過性例外は再試行せず即送出 OK")


print("\n=== TEST 12: 投稿IDが空の行は公開しない（冪等性が壊れるため）===")
FakeClient.instances = []
acc12 = [{"account": "acc", "user_id": "1", "access_token": "t",
          "token_updated_at": "2026-06-08 00:00:00", "daily_count": "", "daily_count_date": ""}]
posts12 = [
    {"row_id": "", "account": "acc", "post_datetime": "2026-06-08 09:00", "text": "IDなし",
     "media_type": "TEXT", "media_url": "", "reply_to": "", "reply_control": "",
     "status": "", "posted_id": "", "posted_at": "", "error": ""},
    {"row_id": "OK1", "account": "acc", "post_datetime": "2026-06-08 09:00", "text": "IDあり",
     "media_type": "TEXT", "media_url": "", "reply_to": "", "reply_control": "",
     "status": "", "posted_id": "", "posted_at": "", "error": ""},
]
store12 = MemoryStore(acc12, posts12)
res12 = Publisher(store12, client_factory=FakeClient, now_fn=fixed_now).run()
p12 = {p["row_id"]: p for p in store12.get_posts()}
assert p12[""]["status"] == "", "空row_idの行は公開してはいけない"
assert p12["OK1"]["status"] == "posted"
assert res12["posted"] == 1, res12
posted_texts = [c["text"] for inst in FakeClient.instances for c in inst.calls]
assert "IDなし" not in posted_texts, "空row_idの本文を公開してはいけない"
print("  ✓ 空row_idの行はスキップ・公開されない OK")


print("\n=== TEST 13: update_postは空row_idで誤って別行を書き換えない ===")
store13 = MemoryStore([], [
    {"row_id": "", "account": "acc", "status": "", "text": "x"},
    {"row_id": "A", "account": "acc", "status": "", "text": "y"},
])
store13.update_post("", {"status": "posted"})
assert store13.get_posts()[0]["status"] == "", "空キーで先頭行を書き換えてはいけない"
print("  ✓ 空row_idでの書き戻しは無視 OK")


print("\n=== TEST 14: update_postはaccount指定でタブ跨ぎ（別アカウントの同一row_id）を誤更新しない ===")
store14 = MemoryStore([], [
    {"row_id": "1", "account": "A", "status": "", "text": "a"},
    {"row_id": "1", "account": "B", "status": "", "text": "b"},
])
store14.update_post("1", {"status": "posted"}, account="B")
ps14 = store14.get_posts()
assert ps14[0]["status"] == "", "別アカウントA(同一row_id)を触ってはいけない"
assert ps14[1]["status"] == "posted", "指定アカウントBの行だけ更新されるべき"
print("  ✓ account限定でタブ跨ぎ誤更新を防止 OK")


print("\n=== TEST 15: GoogleSheetStore.update_post のタブ限定/None全走査/空IDガード/legacyフォールスルー ===")
from threads_poster.sheets import GoogleSheetStore


class _FakeWS:
    def __init__(self, title):
        self.title = title


_calls = []
def _fake_update_in(ws, aliases, key_internal, key_val, fields):
    _calls.append(ws.title)
    return True  # どのタブでも row_id が一致したものとして扱う


# __init__（Google接続）を回避して posts_tabs/_update_in を差し替える。
store15 = GoogleSheetStore.__new__(GoogleSheetStore)
store15.posts_tabs = [(_FakeWS("投稿_takumi_kojo_navi"), "takumi_kojo_navi"),
                      (_FakeWS("投稿_miko_yui_musubi"), "miko_yui_musubi")]
store15._update_in = _fake_update_in

# account=miko → takumiタブはスキップ、mikoタブだけ更新
_calls.clear()
store15.update_post("1", {"status": "posted"}, account="miko_yui_musubi")
assert _calls == ["投稿_miko_yui_musubi"], f"miko指定なのに別タブを触った: {_calls}"

# account=None → 最初のタブで一致して終了（従来挙動）
_calls.clear()
store15.update_post("1", {"status": "posted"}, account=None)
assert _calls == ["投稿_takumi_kojo_navi"], f"account=Noneで全走査されていない: {_calls}"

# 空row_id → どのタブも触らない
_calls.clear()
store15.update_post("", {"status": "posted"}, account="miko_yui_musubi")
assert _calls == [], f"空row_idで書き戻した: {_calls}"

# legacy 単一 posts タブ（tab_account=None）→ account指定でもフォールスルーして更新
store15b = GoogleSheetStore.__new__(GoogleSheetStore)
store15b.posts_tabs = [(_FakeWS("posts"), None)]
store15b._update_in = _fake_update_in
_calls.clear()
store15b.update_post("X", {"status": "posted"}, account="anything")
assert _calls == ["posts"], f"legacy postsタブが後方互換で通らない: {_calls}"
print("  ✓ account限定・None全走査・空IDガード・legacyフォールスルー OK")


print("\n========== 全テスト PASS ==========")
