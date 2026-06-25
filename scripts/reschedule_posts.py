#!/usr/bin/env python3
"""既存の予約投稿（status=queued）の post_datetime を新スケジュールへ一括再配置する。

用途：generator が旧ロジックで「1日1本・21時固定」に並べてしまった queued 投稿を、
新方針（schedule.build_schedule＝1日4本・昼1＋夜3・最低間隔30分・ランダム配置）へ詰め直す。

安全:
  - 既定は **dry-run**（読むだけ・old→new を表示）。実書込は `--apply` を明示したときのみ。
  - 書込は `GoogleSheetStore.update_post`（アカウント別タブ限定・RAW・対象フィールドのみ）を再利用。
    → posted/投稿後ID/row_id 等は触らず、post_datetime セルだけ更新（冪等・二重投稿リスクなし）。
  - status は変えない（queued のまま）。本文・row_id も不変。

例:
  # 確認（dry-run）
  python3 scripts/reschedule_posts.py --account takumi_kojo_navi \
      --sa ~/.config/threads-poster/service-account.json \
      --sheet-id <製造業シートID>
  # 反映
  python3 scripts/reschedule_posts.py --account takumi_kojo_navi --sheet-id <ID> --apply
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import GoogleSheetStore  # noqa: E402
from threads_poster.schedule import build_schedule  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="対象アカウント（投稿_<account> タブ）")
    ap.add_argument("--sheet-id", required=True, help="対象スプレッドシートID")
    ap.add_argument("--sa", default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE",
                    os.path.expanduser("~/.config/threads-poster/service-account.json")),
                    help="サービスアカウントJSONのパス")
    ap.add_argument("--status", default="queued", help="再配置対象の状態（既定 queued）")
    ap.add_argument("--tz", default="Asia/Tokyo")
    ap.add_argument("--seed", type=int, default=None, help="乱数seed（再現用・省略で都度ランダム）")
    ap.add_argument("--apply", action="store_true", help="実際に書き込む（既定はdry-run）")
    args = ap.parse_args()

    import json
    with open(args.sa, encoding="utf-8") as f:
        sa_info = json.load(f)
    tz = ZoneInfo(args.tz)
    now = datetime.now(tz)

    store = GoogleSheetStore(sa_info, args.sheet_id)
    posts = store.get_posts()
    # 対象＝指定アカウント＋指定status。現在の post_datetime 昇順（無いものは末尾）で順序を保つ。
    targets = [p for p in posts
               if str(p.get("account")) == args.account
               and str(p.get("status") or "").lower() == args.status.lower()
               and str(p.get("row_id") or "").strip()]
    targets.sort(key=lambda p: (str(p.get("post_datetime") or "9999"), str(p.get("row_id"))))

    if not targets:
        print(f"対象なし（account={args.account} / status={args.status}）。", file=sys.stderr)
        return 0

    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    new_dts = build_schedule(len(targets), start_date=now, tz=tz, rng=rng)

    print(f"=== 再配置プレビュー（{len(targets)} 件 / {'APPLY' if args.apply else 'DRY-RUN'}）===")
    print(f"{'row_id':<22} | {'旧 post_datetime':<18} -> 新 post_datetime")
    for p, new in zip(targets, new_dts):
        old = str(p.get("post_datetime") or "(空)")
        print(f"{str(p['row_id']):<22} | {old:<18} -> {new}")

    if not args.apply:
        print("\n(dry-run) 反映するには --apply を付けて再実行してください。", file=sys.stderr)
        return 0

    for p, new in zip(targets, new_dts):
        store.update_post(str(p["row_id"]), {"post_datetime": new}, account=args.account)
    print(f"\n✓ {len(targets)} 件の post_datetime を更新しました。", file=sys.stderr)

    # 読み戻し検証
    after = {str(p["row_id"]): p.get("post_datetime")
             for p in store.get_posts() if str(p.get("account")) == args.account}
    ok = all(after.get(str(p["row_id"])) == new for p, new in zip(targets, new_dts))
    print("✓ 読み戻し検証: 一致" if ok else "✗ 読み戻し検証: 不一致あり（要確認）", file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
