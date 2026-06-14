"""
ストレージ層。
スプレッドシートを「投稿キュー」と「アカウント/トークン保管庫」の両方に使う。
- GoogleSheetStore: 本番 (gspread + サービスアカウント)
- MemoryStore: テスト用 (ロジック検証)

スプレッドシート構成:
  シート1 "accounts": account / user_id / access_token / token_updated_at / daily_count / daily_count_date
  シート2 "posts":    row_id / account / post_datetime / text / media_type / media_url /
                      reply_to / reply_control / status / posted_id / posted_at / error
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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

    def _records(self, ws) -> list[dict]:
        return ws.get_all_records()  # 1行目をヘッダとして dict のリスト

    def get_accounts(self) -> list[dict]:
        return self._records(self.ws_accounts)

    def get_posts(self) -> list[dict]:
        return self._records(self.ws_posts)

    def _update_row(self, ws, key_col: str, key_val: str, fields: dict) -> None:
        import gspread

        header = ws.row_values(1)
        col_index = {name: i + 1 for i, name in enumerate(header)}
        if key_col not in col_index:
            return
        # キー列で該当行を探す
        key_cells = ws.col_values(col_index[key_col])
        target_row = None
        for idx, val in enumerate(key_cells[1:], start=2):  # ヘッダ除く
            if str(val) == str(key_val):
                target_row = idx
                break
        if target_row is None:
            return
        # 複数セルを1回の API 呼び出しでまとめて更新（クォータ節約）。
        # RAW: posted_id 等の文字列を Sheets に数値変換させず、入力どおりに保存する。
        cells = [
            gspread.Cell(target_row, col_index[name], "" if value is None else str(value))
            for name, value in fields.items()
            if name in col_index
        ]
        if cells:
            ws.update_cells(cells, value_input_option="RAW")

    def update_account(self, account: str, fields: dict) -> None:
        self._update_row(self.ws_accounts, "account", account, fields)

    def update_post(self, row_id: str, fields: dict) -> None:
        self._update_row(self.ws_posts, "row_id", row_id, fields)


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
