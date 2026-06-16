#!/usr/bin/env python3
"""投稿キューの状態確認＆簡易バリデーション。
各投稿タブの行を一覧し、公開対象/待機・空日時・ツリー親子の不整合などを警告する。

前提環境変数: GOOGLE_SERVICE_ACCOUNT_FILE/_JSON, SPREADSHEET_ID
使い方:
  set -a; . ./.env; set +a
  python3 scripts/check_sheet.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import GoogleSheetStore, POSTS_FIELD_ALIASES  # noqa: E402
from threads_poster.publisher import _parse_dt  # noqa: E402


def load_sa() -> dict:
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if p:
        with open(os.path.expanduser(p)) as f:
            return json.load(f)
    raise SystemExit("GOOGLE_SERVICE_ACCOUNT_FILE / _JSON が見つかりません")


def main() -> int:
    store = GoogleSheetStore(load_sa(), os.environ["SPREADSHEET_ID"])
    tz = ZoneInfo("Asia/Tokyo")
    now = datetime.now(tz)
    accounts = {str(a.get("account")) for a in store.get_accounts()}
    print(f"現在JST: {now:%Y-%m-%d %H:%M:%S}")
    print(f"登録アカウント: {sorted(accounts)}")

    warn = 0
    for ws, acc in store.posts_tabs:
        rows = store._read(ws, POSTS_FIELD_ALIASES)
        for r in rows:
            if acc is not None:
                r["account"] = acc
        by_id = {str(r.get("row_id")): r for r in rows}
        print(f"\n=== タブ『{ws.title}』 (account={acc}) — {len(rows)}件 ===")
        if acc is not None and acc not in accounts:
            print(f"  ⚠ このタブのアカウント '{acc}' が accounts タブにありません")
            warn += 1
        for r in rows:
            rid = str(r.get("row_id"))
            status = str(r.get("status") or "")
            dt = _parse_dt(r.get("post_datetime"), tz)
            flags = []
            if status in ("", "queued"):
                if dt is None:
                    flags.append("⚠ 投稿日時が空/不正→投稿されない")
                elif dt <= now:
                    flags.append("公開対象(時刻到来)")
                else:
                    flags.append(f"待機(あと{int((dt - now).total_seconds() // 60)}分)")
            else:
                flags.append(f"状態={status}")
            reply_to = str(r.get("reply_to") or "").strip()
            if reply_to:
                parent = by_id.get(reply_to)
                if parent is None:
                    flags.append(f"⚠ 返信先ID '{reply_to}' が同タブに無い")
                    warn += 1
                else:
                    pdt = _parse_dt(parent.get("post_datetime"), tz)
                    if dt and pdt:
                        if pdt > dt:
                            flags.append(f"⚠ 親({reply_to})の日時が子より後→順序NG")
                            warn += 1
                        elif pdt == dt:
                            flags.append(f"ツリー親={reply_to}（同時刻：親が上の行ならOK・1分ずらすと確実）")
                        else:
                            flags.append(f"ツリー親={reply_to}（OK：親が先）")
            body = (r.get("text") or "").replace("\n", " ")[:24]
            print(f"  投稿ID={rid:<4} 日時={str(r.get('post_datetime') or ''):<16} "
                  f"種類={str(r.get('media_type') or ''):<8} 本文={body!r}")
            print(f"        -> {' / '.join(flags)}")
    print(f"\n警告 {warn} 件" + ("（要確認）" if warn else "（問題なし）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
