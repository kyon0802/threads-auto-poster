#!/usr/bin/env python3
"""posts タブに入力支援（ドロップダウン/形式チェック）を追加する。
- メディア種類: TEXT/IMAGE/VIDEO/CAROUSEL のドロップダウン（厳格＝リスト外を拒否）
- 状態:        queued/posted/error/publishing のドロップダウン（警告のみ＝システム書込を妨げない）
- 投稿日時:    "YYYY-MM-DD HH:MM" 形式の入力チェック（警告のみ＝タイプミスに赤三角で気づける）

複数アカウントでタブを分けた場合も、同じスクリプトに --tab を変えて各タブへ適用できる。
前提環境変数: GOOGLE_SERVICE_ACCOUNT_FILE/_JSON, SPREADSHEET_ID
使い方:
  set -a; . ./.env; set +a
  python3 scripts/add_validation_ja.py --tab posts
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from string import ascii_uppercase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# YYYY-MM-DD HH:MM（秒は任意、区切りは - か /、桁は1〜2でも可）
DATETIME_RE = r"^\d{4}[-/]\d{1,2}[-/]\d{1,2} \d{1,2}:\d{2}(:\d{2})?$"


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if p:
        with open(os.path.expanduser(p)) as f:
            return json.load(f)
    raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が見つかりません")


def one_of_rule(values, strict):
    return {
        "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
        "strict": strict,
        "showCustomUi": True,  # セルにドロップダウン矢印を表示
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="posts")
    ap.add_argument("--rows", type=int, default=1000)
    args = ap.parse_args()

    import gspread
    from google.oauth2.service_account import Credentials

    sh = gspread.authorize(
        Credentials.from_service_account_info(load_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    ).open_by_key(os.environ["SPREADSHEET_ID"])
    ws = sh.worksheet(args.tab)
    sid = ws.id
    header = ws.row_values(1)

    def rng(name):
        c = header.index(name)
        return {"sheetId": sid, "startRowIndex": 1, "endRowIndex": args.rows,
                "startColumnIndex": c, "endColumnIndex": c + 1}, c

    reqs = []
    if "メディア種類" in header:
        r, _ = rng("メディア種類")
        reqs.append({"setDataValidation": {"range": r, "rule": one_of_rule(["TEXT", "IMAGE", "VIDEO", "CAROUSEL"], True)}})
    if "状態" in header:
        r, _ = rng("状態")
        reqs.append({"setDataValidation": {"range": r, "rule": one_of_rule(["queued", "posted", "error", "publishing"], False)}})
    if "投稿日時" in header:
        r, c = rng("投稿日時")
        letter = ascii_uppercase[c]
        formula = f'=OR({letter}2="",REGEXMATCH(TO_TEXT({letter}2),"{DATETIME_RE}"))'
        reqs.append({"setDataValidation": {"range": r, "rule": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
            "strict": False, "showCustomUi": False,
        }}})

    if reqs:
        sh.batch_update({"requests": reqs})
    print(f"タブ『{args.tab}』に入力支援を追加: メディア種類/状態=ドロップダウン, 投稿日時=形式チェック（{len(reqs)}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
