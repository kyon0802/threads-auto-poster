"""
ストレージ層。
スプレッドシートを「投稿キュー」と「アカウント/トークン保管庫」の両方に使う。
- GoogleSheetStore: 本番 (gspread + サービスアカウント)。見出しは日本語/英語どちらでも動く。
  投稿タブは「アカウントごとに分割」できる（タブ名 "投稿_<account>"）。後方互換で単一 "posts" タブも可。
- MemoryStore: テスト用 (ロジック検証)。内部キー(英語)で保持。

コードは常に「内部キー(英語)」で読み書きし、シートの見出しは下の *_FIELD_ALIASES のどれでもよい。

スプレッドシート構成（正規＝日本語見出し）:
  シート "accounts": アカウント / ユーザーID / アクセストークン / トークン更新日時 / 本日投稿数 / カウント日付
  投稿タブ "投稿_<account>": 投稿ID / 投稿日時 / 本文 / メディア種類 / メディアURL /
                            返信先ID / 返信できる人 / 状態 / 投稿後ID / 投稿実施日時 / エラー
    （アカウントはタブ名から決まるため「アカウント」列は持たない）
  後方互換: 単一 "posts" タブ（行の「アカウント」列でアカウントを指定）も読める。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger("sheets")

# Google Sheets API のサーバ側瞬断とみなす HTTP ステータス。
# 502/503/504 = ゲートウェイ/一時障害、500 = サーバ内部エラー、429 = レート制限。
# いずれも Google 自身が「少し待って再試行」を指示する一過性エラーで、再試行で回復する。
# （2026-06-18 に accounts タブ読込中の 502 で run が落ち、誤報メールが出たため導入）
_TRANSIENT_HTTP_STATUS = (429, 500, 502, 503, 504)


def _is_transient(exc) -> bool:
    """一過性（再試行で回復し得る）のサーバ/ネットワーク障害か判定する。True のものだけ再試行。
    1) gspread.exceptions.APIError 等で HTTP ステータスが _TRANSIENT_HTTP_STATUS のとき。
    2) 応答到達前の network 障害（接続リセット/タイムアウト/DNS瞬断）。requests はこれらを
       ConnectionError/Timeout で投げ .response is None になるため、(1) では拾えない。
    上記以外（404/403 等の恒久エラーや通常のバグ例外）は False＝再試行しない。"""
    resp = getattr(exc, "response", None)
    if getattr(resp, "status_code", None) in _TRANSIENT_HTTP_STATUS:
        return True
    from requests.exceptions import ConnectionError as ReqConnectionError, Timeout
    return isinstance(exc, (ReqConnectionError, Timeout))


def with_retry(fn, *, attempts: int = 5, base_delay: float = 2.0, sleep=None, on_retry=None):
    """fn() を実行し、一過性のサーバエラーなら指数バックオフで再試行する。
    - 一過性でない例外はそのまま即送出（再試行しない）。
    - attempts 回試して最後も一過性エラーなら、その例外を送出する。
    sleep / on_retry は注入可能（テスト用）。実運用の sleep は time.sleep。"""
    if sleep is None:
        import time
        sleep = time.sleep
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient(exc) or i == attempts - 1:
                raise
            delay = base_delay * (2 ** i)
            if on_retry is not None:
                on_retry(i + 1, delay, exc)
            else:
                status = getattr(getattr(exc, "response", None), "status_code", "?")
                log.warning("Google Sheets 一過性エラー(HTTP %s)。%.1f秒後に再試行 (%d/%d)",
                            status, delay, i + 1, attempts)
            sleep(delay)
    raise last_exc  # 到達しない（最終試行は上の raise で抜ける）


# 内部キー(英語) -> 受け付ける見出し名（先頭=正規の日本語見出し。英語の旧名も後方互換で受理）。
ACCOUNTS_FIELD_ALIASES = {
    "account":          ["アカウント", "account"],
    "user_id":          ["ユーザーID", "user_id"],
    "access_token":     ["アクセストークン", "access_token"],
    "token_updated_at": ["トークン更新日時", "token_updated_at"],
    "daily_count":      ["本日投稿数", "daily_count"],
    "daily_count_date": ["カウント日付", "daily_count_date"],
}
POSTS_FIELD_ALIASES = {
    "row_id":        ["投稿ID", "row_id"],
    "account":       ["アカウント", "account"],
    "post_datetime": ["投稿日時", "post_datetime"],
    "text":          ["本文", "text"],
    "media_type":    ["メディア種類", "media_type"],
    "media_url":     ["メディアURL", "media_url"],
    "reply_to":      ["返信先ID", "返信先ID（ツリー用）", "返信先", "reply_to"],
    "reply_control": ["返信できる人", "reply_control"],
    "status":        ["状態", "status"],
    "posted_id":     ["投稿後ID", "posted_id"],
    "posted_at":     ["投稿実施日時", "posted_at"],
    "error":         ["エラー", "error"],
}

# アカウント別の投稿タブ名は「投稿_<account>」。<account> は accounts タブのアカウント名と一致させる。
POSTS_TAB_PREFIX = "投稿_"


# ---- インサイト収集（Collector・Phase 1）。タブは「インサイト_<account>」「アカウント指標_<account>」 ----
INSIGHTS_FIELD_ALIASES = {
    "posted_id":       ["投稿後ID", "posted_id"],
    "row_id":          ["投稿ID", "row_id"],
    "snapshot_date":   ["取得日", "snapshot_date"],
    "permalink":       ["permalink", "リンク"],
    "post_datetime":   ["投稿日時", "post_datetime"],
    "media_type":      ["メディア種類", "media_type"],
    "text_len":        ["本文長", "text_len"],
    "is_tree":         ["ツリー", "ツリー有無", "is_tree"],
    "views":           ["表示回数", "views"],
    "likes":           ["いいね", "likes"],
    "replies":         ["返信", "replies"],
    "reposts":         ["リポスト", "reposts"],
    "quotes":          ["引用", "quotes"],
    "shares":          ["シェア", "shares"],
    "engagement_rate": ["エンゲージ率", "engagement_rate"],
    "collected_at":    ["取得時刻", "collected_at"],
}
ACCOUNT_METRICS_FIELD_ALIASES = {
    "snapshot_date":   ["取得日", "snapshot_date"],
    "followers_count": ["フォロワー数", "followers_count"],
    "views":           ["表示回数", "views"],
    "likes":           ["いいね", "likes"],
    "replies":         ["返信", "replies"],
    "reposts":         ["リポスト", "reposts"],
    "quotes":          ["引用", "quotes"],
    "collected_at":    ["取得時刻", "collected_at"],
}
INSIGHTS_TAB_PREFIX = "インサイト_"
ACCOUNT_METRICS_TAB_PREFIX = "アカウント指標_"

# ---- Phase 2（分析・レポート・生成）のタブ ----
INSIGHTS_ANALYSIS_TAB_PREFIX = "インサイト分析_"   # 週次集計（システム書込）
PROFILE_TAB_PREFIX = "プロフィール_"               # 声/テーマ/お手本/NG（人が curation）
GUIDELINE_TAB = "ガイドライン"                      # 規約/法令/NGワード（事業共通・人が curation）
WEEKLY_REPORT_TAB = "週次レポート"                   # 週次サマリ（システム追記）
KNOWLEDGE_TAB_PREFIX = "ナレッジ_"                  # 生成エンジンが読む事業ナレッジ全文（非公開シートのみ・§17b）


def canonical_headers(aliases: dict) -> list[str]:
    """新規シート作成・日本語化に使う「正規（日本語）見出し」の並び。"""
    return [names[0] for names in aliases.values()]


def per_account_post_headers() -> list[str]:
    """アカウント別タブの見出し（アカウントはタブ名で判るので「アカウント」列は持たない）。"""
    return [names[0] for key, names in POSTS_FIELD_ALIASES.items() if key != "account"]


def header_maps(header_row, aliases: dict):
    """実シートの見出し行から双方向マップを作る。
      to_internal: 実見出し -> 内部キー(英語)
      to_header:   内部キー(英語) -> 実見出し（シートに実在する見出し）
    大文字小文字・前後空白は無視して照合する。
    """
    lookup = {}
    for internal, names in aliases.items():
        for n in names:
            lookup[str(n).strip().lower()] = internal
    to_internal, to_header = {}, {}
    for h in header_row:
        internal = lookup.get(str(h).strip().lower())
        if internal:
            to_internal[h] = internal
            to_header.setdefault(internal, h)
    return to_internal, to_header


class Store(ABC):
    @abstractmethod
    def get_accounts(self) -> list[dict]: ...
    @abstractmethod
    def update_account(self, account: str, fields: dict) -> None: ...
    @abstractmethod
    def get_posts(self) -> list[dict]: ...
    @abstractmethod
    def update_post(self, row_id: str, fields: dict, account: str | None = None) -> None: ...
    @abstractmethod
    def upsert_insight(self, account: str, posted_id: str, snapshot_date: str, fields: dict) -> None: ...
    @abstractmethod
    def upsert_insights_bulk(self, account: str, snapshot_date: str, rows: list[dict]) -> None: ...
    @abstractmethod
    def upsert_account_metric(self, account: str, snapshot_date: str, fields: dict) -> None: ...


# ---------------- 本番: Google Sheets ----------------
class GoogleSheetStore(Store):
    def __init__(self, service_account_info: dict, spreadsheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        gc = gspread.authorize(creds)
        # 以降の gspread 呼び出しは Google 側の一過性 5xx/429 を with_retry で吸収する。
        self.sh = with_retry(lambda: gc.open_by_key(spreadsheet_id))
        self.ws_accounts = with_retry(lambda: self.sh.worksheet("accounts"))
        self.posts_tabs = self._discover_posts_tabs()

    def _discover_posts_tabs(self):
        """投稿タブの一覧 [(worksheet, account_or_None)] を返す。
        - "投稿_<account>" タブ: アカウントはタブ名から決まる
        - 1つも無ければ後方互換で単一 "posts" タブ（アカウントは行の「アカウント」列から）
        """
        tabs = []
        for ws in with_retry(self.sh.worksheets):
            if ws.title.startswith(POSTS_TAB_PREFIX):
                tabs.append((ws, ws.title[len(POSTS_TAB_PREFIX):]))
        if not tabs:
            try:
                tabs.append((with_retry(lambda: self.sh.worksheet("posts")), None))
            except Exception:  # noqa: BLE001
                pass
        return tabs

    def _read(self, ws, aliases) -> list[dict]:
        records = with_retry(ws.get_all_records)  # 1行目をヘッダとして dict のリスト
        if not records:
            return []
        to_internal, _ = header_maps(records[0].keys(), aliases)
        # 見出しを内部キー(英語)に翻訳（未知列はそのまま温存）
        return [{to_internal.get(k, k): v for k, v in r.items()} for r in records]

    def get_accounts(self) -> list[dict]:
        return self._read(self.ws_accounts, ACCOUNTS_FIELD_ALIASES)

    def get_posts(self) -> list[dict]:
        out = []
        for ws, account in self.posts_tabs:
            for row in self._read(ws, POSTS_FIELD_ALIASES):
                if account is not None:
                    row["account"] = account  # アカウントをタブ名から注入
                out.append(row)
        return out

    def _update_in(self, ws, aliases, key_internal: str, key_val: str, fields: dict) -> bool:
        """ws 内で key を探し、見つかれば更新して True。無ければ False。"""
        import gspread

        header = with_retry(lambda: ws.row_values(1))
        to_internal, to_header = header_maps(header, aliases)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        key_header = to_header.get(key_internal)
        if not key_header or key_header not in col_index:
            return False
        key_cells = with_retry(lambda: ws.col_values(col_index[key_header]))
        target_row = None
        for idx, val in enumerate(key_cells[1:], start=2):  # ヘッダ除く
            if str(val) == str(key_val):
                target_row = idx
                break
        if target_row is None:
            return False
        # 内部キー -> 実見出し -> 列番号 に変換し、1回の API 呼び出しでまとめて更新（RAW）。
        cells = [
            gspread.Cell(target_row, col_index[to_header[internal]], "" if value is None else str(value))
            for internal, value in fields.items()
            if internal in to_header and to_header[internal] in col_index
        ]
        if cells:
            # 特定セルへの上書き（追記ではない）なので、一過性エラーでの再試行は冪等。
            with_retry(lambda: ws.update_cells(cells, value_input_option="RAW"))
        return True

    def update_account(self, account: str, fields: dict) -> None:
        self._update_in(self.ws_accounts, ACCOUNTS_FIELD_ALIASES, "account", account, fields)

    def update_post(self, row_id: str, fields: dict, account: str | None = None) -> None:
        # 空の row_id は書き戻さない（誤って「最初の空ID行」を書き換えると冪等性が壊れるため）。
        if not str(row_id).strip():
            log.warning("空の投稿IDでの書き戻しを無視しました（冪等性保護）: %s", fields)
            return
        # 投稿タブを順に探し、最初に見つかった投稿IDの行を更新する。
        # account 指定時は、アカウント別タブ（投稿_<account>）のうち一致するタブだけを対象にする
        # （別アカウントのタブにある同一 row_id を誤って書き換えないため）。
        for ws, tab_account in self.posts_tabs:
            if account is not None and tab_account is not None and str(tab_account) != str(account):
                continue
            if self._update_in(ws, POSTS_FIELD_ALIASES, "row_id", row_id, fields):
                return

    # ---- インサイト収集（Collector・Phase 1）。タブが無ければ作成し、キーで冪等 upsert ----
    def _get_or_create_ws(self, title: str, headers: list[str]):
        cache = getattr(self, "_ins_ws_cache", None)
        if cache is None:
            cache = self._ins_ws_cache = {}
        if title in cache:  # 同一run内で worksheets() を繰り返さない（API呼び出し節約）
            return cache[title]
        existing = {ws.title: ws for ws in with_retry(self.sh.worksheets)}
        if title in existing:
            ws = existing[title]
            if not with_retry(lambda: ws.row_values(1)):  # ヘッダ未設定なら入れる
                with_retry(lambda: self.sh.values_update(
                    f"'{title}'!A1", params={"valueInputOption": "RAW"}, body={"values": [headers]}))
        else:
            ws = with_retry(lambda: self.sh.add_worksheet(title=title, rows=2000, cols=max(20, len(headers))))
            with_retry(lambda: self.sh.values_update(
                f"'{title}'!A1", params={"valueInputOption": "RAW"}, body={"values": [headers]}))
        cache[title] = ws
        return ws

    def _upsert_row(self, title: str, aliases: dict, key_internals: list[str], key_vals: list, fields: dict) -> None:
        """(key_internals==key_vals) の行を探して上書き、無ければ追記。全セル RAW(文字列)。"""
        import gspread

        ws = self._get_or_create_ws(title, canonical_headers(aliases))
        header = with_retry(lambda: ws.row_values(1))
        to_internal, to_header = header_maps(header, aliases)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        records = with_retry(ws.get_all_records)
        merged = dict(zip(key_internals, [str(v) for v in key_vals]))
        merged.update(fields)
        target = None
        for idx, rec in enumerate(records, start=2):  # ヘッダ除く（2始まり）
            rint = {to_internal.get(k, k): v for k, v in rec.items()}
            if all(str(rint.get(ki, "")) == str(kv) for ki, kv in zip(key_internals, key_vals)):
                target = idx
                break
        if target is not None:  # 上書き（冪等）
            cells = [gspread.Cell(target, col_index[to_header[i]], "" if v is None else str(v))
                     for i, v in merged.items() if i in to_header and to_header[i] in col_index]
            if cells:
                with_retry(lambda: ws.update_cells(cells, value_input_option="RAW"))
        else:  # 追記
            row = [""] * len(header)
            for i, v in merged.items():
                if i in to_header and to_header[i] in col_index:
                    row[col_index[to_header[i]] - 1] = "" if v is None else str(v)
            with_retry(lambda: ws.append_row(row, value_input_option="RAW"))

    def upsert_insight(self, account: str, posted_id: str, snapshot_date: str, fields: dict) -> None:
        self._upsert_row(f"{INSIGHTS_TAB_PREFIX}{account}", INSIGHTS_FIELD_ALIASES,
                         ["posted_id", "snapshot_date"], [posted_id, snapshot_date], fields)

    def upsert_insights_bulk(self, account: str, snapshot_date: str, rows: list[dict]) -> None:
        """インサイトタブを**1回だけ読み**、(posted_id, snapshot_date) で update/append を
        それぞれ1回のAPIにまとめる。投稿ごとに読み書きするとSheetsのレート上限(429)に当たるため。"""
        if not rows:
            return
        import gspread

        title = f"{INSIGHTS_TAB_PREFIX}{account}"
        aliases = INSIGHTS_FIELD_ALIASES
        ws = self._get_or_create_ws(title, canonical_headers(aliases))
        header = with_retry(lambda: ws.row_values(1))
        to_internal, to_header = header_maps(header, aliases)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        existing = with_retry(ws.get_all_records)  # ← タブ全体の読み込みは run中1回だけ
        idx = {}
        for i, rec in enumerate(existing, start=2):
            rint = {to_internal.get(k, k): v for k, v in rec.items()}
            idx[(str(rint.get("posted_id", "")), str(rint.get("snapshot_date", "")))] = i
        next_row = len(existing) + 2
        cells, appends = [], []
        for r in rows:
            full = dict(r)
            full["snapshot_date"] = snapshot_date
            key = (str(full.get("posted_id", "")), str(snapshot_date))
            rownum = idx.get(key)
            if rownum:  # 既存行を上書き（冪等）
                for internal, v in full.items():
                    if internal in to_header and to_header[internal] in col_index:
                        cells.append(gspread.Cell(rownum, col_index[to_header[internal]], "" if v is None else str(v)))
            else:  # 新規行
                vals = [""] * len(header)
                for internal, v in full.items():
                    if internal in to_header and to_header[internal] in col_index:
                        vals[col_index[to_header[internal]] - 1] = "" if v is None else str(v)
                appends.append(vals)
                idx[key] = next_row
                next_row += 1
        if cells:  # 上書き分をまとめて1回
            with_retry(lambda: ws.update_cells(cells, value_input_option="RAW"))
        if appends:  # 追記分をまとめて1回
            with_retry(lambda: ws.append_rows(appends, value_input_option="RAW"))

    def upsert_account_metric(self, account: str, snapshot_date: str, fields: dict) -> None:
        self._upsert_row(f"{ACCOUNT_METRICS_TAB_PREFIX}{account}", ACCOUNT_METRICS_FIELD_ALIASES,
                         ["snapshot_date"], [snapshot_date], fields)

    # ---- Phase 2（分析/レポート/生成）の読み書き ----
    def _read_tab_raw(self, title: str) -> list[dict]:
        existing = {w.title: w for w in with_retry(self.sh.worksheets)}
        ws = existing.get(title)
        if ws is None:
            return []
        return with_retry(ws.get_all_records)

    def get_insights(self, account: str) -> list[dict]:
        recs = self._read_tab_raw(f"{INSIGHTS_TAB_PREFIX}{account}")
        if not recs:
            return []
        to_internal, _ = header_maps(list(recs[0].keys()), INSIGHTS_FIELD_ALIASES)
        return [{to_internal.get(k, k): v for k, v in r.items()} for r in recs]

    def get_profile(self, account: str) -> dict:
        """プロフィール_<acc>（項目/内容）を {項目: 内容} で返す。"""
        return {str(r.get("項目")): str(r.get("内容"))
                for r in self._read_tab_raw(f"{PROFILE_TAB_PREFIX}{account}") if r.get("項目")}

    def get_guideline(self) -> list[dict]:
        """ガイドライン（分類/ルール/重大度）を行のリストで返す。"""
        return [{"分類": str(r.get("分類", "")), "ルール": str(r.get("ルール", "")),
                 "重大度": str(r.get("重大度", ""))}
                for r in self._read_tab_raw(GUIDELINE_TAB) if r.get("ルール")]

    def get_knowledge(self, account: str) -> str:
        """ナレッジ_<acc> タブの全文（A列のチャンクを結合）。生成プロンプトの濃い知識源。"""
        title = f"{KNOWLEDGE_TAB_PREFIX}{account}"
        existing = {w.title: w for w in with_retry(self.sh.worksheets)}
        ws = existing.get(title)
        if ws is None:
            return ""
        col = with_retry(lambda: ws.col_values(1))
        return "\n".join(c for c in col[1:] if c)  # 1行目はヘッダ

    def write_analysis(self, account: str, header: list[str], rows: list[list]) -> None:
        title = f"{INSIGHTS_ANALYSIS_TAB_PREFIX}{account}"
        ws = self._get_or_create_ws(title, header)
        with_retry(ws.clear)
        body = [header] + [["" if c is None else str(c) for c in r] for r in rows]
        with_retry(lambda: self.sh.values_update(f"'{title}'!A1",
                   params={"valueInputOption": "RAW"}, body={"values": body}))

    def append_report(self, header: list[str], row: list) -> None:
        ws = self._get_or_create_ws(WEEKLY_REPORT_TAB, header)
        with_retry(lambda: ws.append_row(["" if c is None else str(c) for c in row],
                                         value_input_option="RAW"))

    def add_post(self, account: str, fields: dict) -> None:
        """生成投稿を 投稿_<account> タブへ追記（generator 用）。fields は内部キー。"""
        title = f"{POSTS_TAB_PREFIX}{account}"
        existing = {w.title: w for w in with_retry(self.sh.worksheets)}
        ws = existing.get(title)
        if ws is None:
            raise RuntimeError(f"投稿タブ '{title}' がありません")
        header = with_retry(lambda: ws.row_values(1))
        _, to_header = header_maps(header, POSTS_FIELD_ALIASES)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        row = [""] * len(header)
        for internal, v in fields.items():
            if internal in to_header and to_header[internal] in col_index:
                row[col_index[to_header[internal]] - 1] = "" if v is None else str(v)
        with_retry(lambda: ws.append_row(row, value_input_option="RAW"))


# ---------------- テスト用: メモリ ----------------
class MemoryStore(Store):
    def __init__(self, accounts: list[dict], posts: list[dict],
                 insights: list[dict] | None = None, account_metrics: list[dict] | None = None):
        self.accounts = accounts
        self.posts = posts
        self.insights = insights if insights is not None else []
        self.account_metrics = account_metrics if account_metrics is not None else []

    def get_accounts(self) -> list[dict]:
        return self.accounts

    def get_posts(self) -> list[dict]:
        return self.posts

    def update_account(self, account: str, fields: dict) -> None:
        for a in self.accounts:
            if str(a["account"]) == str(account):
                a.update(fields)

    def update_post(self, row_id: str, fields: dict, account: str | None = None) -> None:
        if not str(row_id).strip():
            return  # 空キーは書き込まない（誤って別行を更新しないため）
        for p in self.posts:
            if str(p["row_id"]) == str(row_id) and (account is None or str(p.get("account")) == str(account)):
                p.update(fields)

    def upsert_insight(self, account: str, posted_id: str, snapshot_date: str, fields: dict) -> None:
        for r in self.insights:
            if (str(r.get("account")) == str(account)
                    and str(r.get("posted_id")) == str(posted_id)
                    and str(r.get("snapshot_date")) == str(snapshot_date)):
                r.update(fields)
                return
        rec = {"account": account, "posted_id": posted_id, "snapshot_date": snapshot_date}
        rec.update(fields)
        self.insights.append(rec)

    def upsert_insights_bulk(self, account: str, snapshot_date: str, rows: list[dict]) -> None:
        for r in rows:
            pid = r.get("posted_id")
            self.upsert_insight(account, pid, snapshot_date,
                                {k: v for k, v in r.items() if k != "posted_id"})

    def upsert_account_metric(self, account: str, snapshot_date: str, fields: dict) -> None:
        for r in self.account_metrics:
            if str(r.get("account")) == str(account) and str(r.get("snapshot_date")) == str(snapshot_date):
                r.update(fields)
                return
        rec = {"account": account, "snapshot_date": snapshot_date}
        rec.update(fields)
        self.account_metrics.append(rec)

    # ---- Phase 2（テスト用）----
    def get_insights(self, account: str) -> list[dict]:
        return [r for r in self.insights if str(r.get("account")) == str(account)]

    def get_profile(self, account: str) -> dict:
        return getattr(self, "profiles", {}).get(account, {})

    def get_guideline(self) -> list[dict]:
        return getattr(self, "guideline", [])

    def get_knowledge(self, account: str) -> str:
        return getattr(self, "knowledge", {}).get(account, "")

    def write_analysis(self, account: str, header: list[str], rows: list[list]) -> None:
        if not hasattr(self, "analyses"):
            self.analyses = {}
        self.analyses[account] = {"header": header, "rows": rows}

    def append_report(self, header: list[str], row: list) -> None:
        if not hasattr(self, "reports"):
            self.reports = []
        self.reports.append(row)

    def add_post(self, account: str, fields: dict) -> None:
        rec = dict(fields)
        rec["account"] = account
        self.posts.append(rec)
