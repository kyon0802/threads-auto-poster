#!/usr/bin/env python3
"""ローカルのナレッジ(.md 等)を、事業シートの「ナレッジ_<account>」タブへ同期する。

生成エンジン(generator)はこのタブを読んで濃いプロンプトを作る。ナレッジは“事業ノウハウ”なので
**非公開シートにのみ**置く（公開repo禁止・§17b）。横槍＝ローカルのナレッジを更新して本スクリプトを
再実行するだけ（または タブを直接編集）。Google Sheets のセル上限(約5万字)を超える分はチャンク分割する。

使い方:
  GOOGLE_SERVICE_ACCOUNT_FILE=~/.config/threads-poster/service-account.json \
  python3 scripts/sync_knowledge.py --sheet <SPREADSHEET_ID> --account <acc> --file <ローカルの.mdパス>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import KNOWLEDGE_TAB_PREFIX  # noqa: E402

CHUNK = 40000  # 1セルあたりの最大文字数（Sheetsの上限~5万に対し安全側）


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not p:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が必要です")
    with open(os.path.expanduser(p)) as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True, help="事業シートのスプレッドシートID")
    ap.add_argument("--account", required=True, help="アカウント名（タブ ナレッジ_<account> になる）")
    ap.add_argument("--file", required=True, help="同期するローカルのナレッジファイル")
    args = ap.parse_args()

    import gspread
    from google.oauth2.service_account import Credentials

    with open(os.path.expanduser(args.file)) as f:
        text = f.read()
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]

    gc = gspread.authorize(Credentials.from_service_account_info(
        load_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"]))
    sh = gc.open_by_key(args.sheet)
    title = f"{KNOWLEDGE_TAB_PREFIX}{args.account}"
    existing = {w.title: w for w in sh.worksheets()}
    ws = existing.get(title) or sh.add_worksheet(title=title, rows=max(20, len(chunks) + 5), cols=2)
    ws.clear()
    rows = [["内容（生成エンジンが読む全文・直接編集可）"]] + [[c] for c in chunks]
    sh.values_update(f"'{title}'!A1", params={"valueInputOption": "RAW"}, body={"values": rows})
    print(f"✓ {sh.title} / {title}: {len(text):,}字 を {len(chunks)}セルに同期しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
