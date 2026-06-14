"""
ストレージ層。
スプレッドシートを「投稿キュー」と「アカウント/トークン保管庫」の両方に使う。
- GoogleSheetStore: 本番 (gspread + サービスアカウント)。見出しは日本語/英語どちらでも動く。
- MemoryStore: テスト用 (ロジック検証)。内部キー(英語)で保持。

コードは常に「内部キー(英語)」で読み書きし、シートの見出しは下の *_FIELD_ALIASES の
どれでもよい（日本語の正規見出し or 旧英語見出し）。これにより、シートを日本語化しても
プログラムは壊れない。

スプレッドシート構成（正規＝日本語見出し）:
  シート "accounts": アカウント / ユーザーID / アクセストークン / トークン更新日時 / 本日投稿数 / カウント日付
  シート "posts":    投稿ID / アカウント / 投稿日時 / 本文 / メディア種類 / メディアURL /
                     返信先ID / 返信できる人 / 状態 / 投稿後ID / 投稿実施日時 / エラー
"""
from __future__ import annotations

from abc import ABC, abstractmethod


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


def canonical_headers(aliases: dict) -> list[str]:
    """新規シート作成・日本語化に使う「正規（日本語）見出し」の並び。"""
    return [names[0] for names in aliases.values()]


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
    def update_post(self, row_id: str, fields: dict) -> None: ...


# ---------------- 本番: Google Sheets ----------------
class GoogleSheetStore(Store):
    def __init__(self, service_account_info: dict, spreadsheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(spreadsheet_id)
        self.ws_accounts = sh.worksheet("accounts")
        self.ws_posts = sh.worksheet("posts")

    def _read(self, ws, aliases) -> list[dict]:
        records = ws.get_all_records()  # 1行目をヘッダとして dict のリスト
        if not records:
            return []
        to_internal, _ = header_maps(records[0].keys(), aliases)
        # 見出しを内部キー(英語)に翻訳（未知列はそのまま温存）
        return [{to_internal.get(k, k): v for k, v in r.items()} for r in records]

    def get_accounts(self) -> list[dict]:
        return self._read(self.ws_accounts, ACCOUNTS_FIELD_ALIASES)

    def get_posts(self) -> list[dict]:
        return self._read(self.ws_posts, POSTS_FIELD_ALIASES)

    def _update_row(self, ws, aliases, key_internal: str, key_val: str, fields: dict) -> None:
        import gspread

        header = ws.row_values(1)
        to_internal, to_header = header_maps(header, aliases)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        key_header = to_header.get(key_internal)
        if not key_header or key_header not in col_index:
            return
        # キー列で該当行を探す
        key_cells = ws.col_values(col_index[key_header])
        target_row = None
        for idx, val in enumerate(key_cells[1:], start=2):  # ヘッダ除く
            if str(val) == str(key_val):
                target_row = idx
                break
        if target_row is None:
            return
        # 内部キー -> 実見出し -> 列番号 に変換し、1回の API 呼び出しでまとめて更新（RAW）。
        cells = [
            gspread.Cell(target_row, col_index[to_header[internal]], "" if value is None else str(value))
            for internal, value in fields.items()
            if internal in to_header and to_header[internal] in col_index
        ]
        if cells:
            ws.update_cells(cells, value_input_option="RAW")

    def update_account(self, account: str, fields: dict) -> None:
        self._update_row(self.ws_accounts, ACCOUNTS_FIELD_ALIASES, "account", account, fields)

    def update_post(self, row_id: str, fields: dict) -> None:
        self._update_row(self.ws_posts, POSTS_FIELD_ALIASES, "row_id", row_id, fields)


# ---------------- テスト用: メモリ ----------------
class MemoryStore(Store):
    def __init__(self, accounts: list[dict], posts: list[dict]):
        self.accounts = accounts
        self.posts = posts

    def get_accounts(self) -> list[dict]:
        return self.accounts

    def get_posts(self) -> list[dict]:
        return self.posts

    def update_account(self, account: str, fields: dict) -> None:
        for a in self.accounts:
            if str(a["account"]) == str(account):
                a.update(fields)

    def update_post(self, row_id: str, fields: dict) -> None:
        for p in self.posts:
            if str(p["row_id"]) == str(row_id):
                p.update(fields)
