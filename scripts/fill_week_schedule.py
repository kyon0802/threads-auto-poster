#!/usr/bin/env python3
"""在庫の投稿本文を「1日4投稿×N日」のランダムスケジュールに割り当てて予約する。

製造業/占いで時間帯プリセットが異なる:
  - seizogyo（製造業）：昼12:00前後に1本＋夜18:00-23:00に3本（最低間隔30分・ランダム）
  - uranai（占い）  ：午前(8:00-11:30)に1本＋夕方-深夜17:00-23:59に3本（同上）

コンテンツ選択：本文があり status が queued / 空 /（任意で draft）の行を、
queued→空→draft の優先順で N*4 本まで採用し、post_datetime を新規ランダム割当＋status=queued。

安全：既定 dry-run（表示のみ）。--apply で書込（GoogleSheetStore.update_post 再利用＝アカウント別タブ
限定・RAW・指定フィールドのみ）。書込前にバックアップ推奨、書込後に読み戻し検証。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from threads_poster.sheets import GoogleSheetStore  # noqa: E402
from threads_poster.schedule import build_schedule  # noqa: E402
from threads_poster.compliance import check_post, extract_ng_words  # noqa: E402

PLACEHOLDER_CHARS = ["◯", "〇", "○"]  # 未確定プレースホルダ（自動公開させない）


def content_issues(text: str, ng: list[str]) -> list[str]:
    """本文の自動公開可否チェック。NGワード/URL/文字数（compliance）＋未確定プレースホルダ。"""
    ok, reasons = check_post(text, ng)
    issues = [] if ok else list(reasons)
    for ch in PLACEHOLDER_CHARS:
        if ch in text:
            issues.append(f"未確定プレースホルダ「{ch}」")
    return issues

PRESETS = {
    "seizogyo": dict(noon_center_min=12 * 60, noon_jitter_min=30,
                     evening_start_min=18 * 60, evening_end_min=23 * 60,
                     evening_count=3, min_gap_min=30),
    "uranai": dict(noon_center_min=9 * 60 + 45, noon_jitter_min=105,        # 午前 8:00-11:30
                   evening_start_min=17 * 60, evening_end_min=23 * 60 + 59,  # 夕方-23:59
                   evening_count=3, min_gap_min=30),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--sheet-id", required=True)
    ap.add_argument("--preset", required=True, choices=list(PRESETS))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sa", default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE",
                    os.path.expanduser("~/.config/threads-poster/service-account.json")))
    ap.add_argument("--status", default="queued", help="割当後の状態（既定 queued）")
    ap.add_argument("--include-drafts", action="store_true", help="draft も在庫として使う")
    ap.add_argument("--tz", default="Asia/Tokyo")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    daily = PRESETS[args.preset]
    per_day = 1 + daily["evening_count"]
    need = args.days * per_day
    sa_info = json.load(open(args.sa, encoding="utf-8"))
    tz = ZoneInfo(args.tz)
    now = datetime.now(tz)
    store = GoogleSheetStore(sa_info, args.sheet_id)

    posts = [p for p in store.get_posts() if str(p.get("account")) == args.account
             and str(p.get("row_id") or "").strip()]
    ng = extract_ng_words(store.get_guideline())
    allowed = {"queued", ""} | ({"draft"} if args.include_drafts else set())
    # 優先順位: queued(承認済) → 空(在庫) → draft。同順位内は現row順。
    rank = {"queued": 0, "": 1, "draft": 2}
    raw_pool = [p for p in posts if str(p.get("text") or "").strip()
                and str(p.get("status") or "").lower() in allowed]
    # コンプラ/プレースホルダで不適格な在庫は除外（自動公開させない＝BAN対策）。
    pool, skipped = [], []
    for p in raw_pool:
        issues = content_issues(str(p.get("text") or ""), ng)
        (skipped if issues else pool).append((p, issues))
    pool = [p for p, _ in sorted(pool, key=lambda x: rank.get(str(x[0].get("status") or "").lower(), 9))]
    if skipped:
        print(f"除外（不適格）{len(skipped)}本:")
        for p, issues in skipped:
            print(f"   {p.get('row_id')}: {', '.join(issues)} | {str(p.get('text') or '')[:16]}")
    selected = pool[:need]

    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    schedule = build_schedule(len(selected), start_date=now, tz=tz, rng=rng, **daily)

    print(f"=== {args.account} / preset={args.preset} / {len(selected)}本を {args.days}日×{per_day} に割当"
          f"（{'APPLY' if args.apply else 'DRY-RUN'}）===")
    if len(selected) < need:
        print(f"⚠️ 在庫不足: 必要{need}本に対し採用{len(selected)}本（{need - len(selected)}本足りません）")
    for p, dt in zip(selected, schedule):
        old = str(p.get("post_datetime") or "(空)")
        print(f"  {str(p['row_id']):>20} [{str(p.get('status') or '空'):<6}] {old:<17} -> {dt}  | {str(p.get('text') or '')[:16]}")

    if not args.apply:
        print("\n(dry-run) 反映するには --apply を付けて再実行。", file=sys.stderr)
        return 0

    for p, dt in zip(selected, schedule):
        store.update_post(str(p["row_id"]), {"post_datetime": dt, "status": args.status},
                          account=args.account)
    print(f"\n✓ {len(selected)}本を予約（status={args.status}）。", file=sys.stderr)
    store.sort_posts_tab(args.account, descending=True)  # 新しい日付が上に来るよう整える
    print("✓ 投稿タブを日付降順に整列。", file=sys.stderr)
    # 読み戻し検証
    after = {str(p["row_id"]): (p.get("post_datetime"), str(p.get("status") or "").lower())
             for p in store.get_posts() if str(p.get("account")) == args.account}
    ok = all(after.get(str(p["row_id"])) == (dt, args.status.lower()) for p, dt in zip(selected, schedule))
    print("✓ 読み戻し検証: 一致" if ok else "✗ 読み戻し: 不一致あり（要確認）", file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
