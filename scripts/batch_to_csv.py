#!/usr/bin/env python3
"""
コンテンツ工場 → 配送機構 のブリッジ。

「個人エージェント×Threads」の立ち上げバッチ Markdown（R1形式）を読み、
posts タブにそのまま貼れる CSV（row_id/account/post_datetime/text/...）へ変換する。
2アカウントを最低3時間ずらし・1日あたり指定本数で並べる投稿スケジュールも自動生成する。

R1形式（パース対象）:
    ## アカA：... / ## アカB：...        ← アカウントのグループ見出し（A/Bを検出）
    ### A1｜カテゴリ｜CTA:共感           ← 投稿の見出し（row_idの素 A1 を抽出）
    ```
    本文（複数行・ハッシュタグ込み）
    ```

使い方:
    python3 scripts/batch_to_csv.py \
        --in "../製造業Threads/01_現運用_個人エージェント2アカ/posts/R1_立ち上げバッチ_20260612.md" \
        --account-a accA --account-b accB \
        --start 2026-06-16 --slots-a 21:00 --slots-b 18:00 \
        --out r1_posts.csv

注意:
  - これは「機械（配送）」側の入口。**コンプラ判定はしない**。
    投稿可否の正典は `threads-compliance` スキル。本スクリプトの NGワード検査は
    未チェック本文の流入を止める最終防壁（last-line tripwire）に過ぎない。
  - --start 未指定時は post_datetime を空にして出力（手で日時を入れる運用）。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import datetime, timedelta

POSTS_HEADERS = [
    "row_id", "account", "post_datetime", "text", "media_type", "media_url",
    "reply_to", "reply_control", "status", "posted_id", "posted_at", "error",
]

# 最終防壁: コンプライアンス_マスター 第3章の絶対NGワード15語（の代表表記）。
# ★正典は threads-compliance スキル。ここはブリッジに未チェック文が流れ込むのを止めるだけ。
NG_WORDS = [
    "即入寮OK", "カバン一つで", "身一つで", "手ぶらで", "所持金ゼロでも",
    "借金OK", "過去問わず", "経歴不問", "夜逃げ", "事情を聞きます",
    "ワケあり", "絶対稼げる", "今すぐDM", "今日中に",
]
PLACEHOLDER_CHARS = ["◯", "〇", "○"]  # 未確定プレースホルダ

GROUP_RE = re.compile(r"^##\s*アカ([A-Z])")
POST_RE = re.compile(r"^###\s*([A-Za-z]+\d+)")
FENCE_RE = re.compile(r"^```")


def parse_batch(text: str) -> list[dict]:
    """Markdown を [{id, group, body}] に分解する。"""
    posts: list[dict] = []
    group = None
    cur_id = None
    in_fence = False
    body_lines: list[str] = []

    def flush():
        nonlocal cur_id, body_lines
        if cur_id is not None and body_lines:
            posts.append({"id": cur_id, "group": group, "body": "\n".join(body_lines).strip()})
        cur_id = None
        body_lines = []

    for line in text.splitlines():
        if in_fence:
            if FENCE_RE.match(line):
                in_fence = False
                flush()
            else:
                body_lines.append(line)
            continue
        g = GROUP_RE.match(line)
        if g:
            group = g.group(1)
            continue
        m = POST_RE.match(line)
        if m:
            cur_id = m.group(1)
            body_lines = []
            continue
        if FENCE_RE.match(line) and cur_id is not None:
            in_fence = True
            body_lines = []
    return posts


def check_compliance(posts: list[dict]) -> list[str]:
    """NGワード/プレースホルダの最終防壁。違反メッセージのリストを返す（空なら合格）。"""
    issues: list[str] = []
    for p in posts:
        body = p["body"]
        for w in NG_WORDS:
            if w in body:
                issues.append(f"{p['id']}: NGワード「{w}」を含む")
        for ch in PLACEHOLDER_CHARS:
            if ch in body:
                issues.append(f"{p['id']}: 未確定プレースホルダ「{ch}」が残っている")
    return issues


def build_schedule(n: int, start: datetime | None, slots: list[str]) -> list[str]:
    """n本の投稿に post_datetime（JST文字列）を割り当てる。start無しなら空文字。"""
    if start is None:
        return [""] * n
    per_day = max(1, len(slots))
    out = []
    for i in range(n):
        day = i // per_day
        hh, mm = map(int, slots[i % per_day].split(":"))
        dt = (start + timedelta(days=day)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        out.append(dt.strftime("%Y-%m-%d %H:%M"))
    return out


def warn_account_gap(slots_a: list[str], slots_b: list[str], min_gap_h: float = 3.0) -> None:
    """2アカの同日スロットが min_gap_h 未満なら警告（CIB対策: 最低3hずらす）。"""
    def to_min(s):
        h, m = map(int, s.split(":"))
        return h * 60 + m
    for sa in slots_a:
        for sb in slots_b:
            if abs(to_min(sa) - to_min(sb)) < min_gap_h * 60:
                print(f"  ⚠️ 警告: アカA {sa} と アカB {sb} の間隔が{min_gap_h}h未満。"
                      f"CIB対策のため最低3hずらしてください。", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    default_in = "../製造業Threads/01_現運用_個人エージェント2アカ/posts/R1_立ち上げバッチ_20260612.md"
    ap.add_argument("--in", dest="infile", default=default_in, help="バッチMarkdownのパス")
    ap.add_argument("--account-a", required=True, help="アカAに対応する accounts タブの account 名")
    ap.add_argument("--account-b", help="アカBに対応する account 名（無ければA分のみ出力）")
    ap.add_argument("--start", help="投稿開始日 YYYY-MM-DD（未指定なら日時は空欄で出力）")
    ap.add_argument("--slots-a", default="21:00", help="アカAの投稿時刻（カンマ区切り。本数=1日の本数）")
    ap.add_argument("--slots-b", default="18:00", help="アカBの投稿時刻（カンマ区切り）")
    ap.add_argument("--reply-control", default="everyone", help="reply_control 既定値")
    ap.add_argument("--prefix", default="R1", help="row_id の接頭辞")
    ap.add_argument("--out", help="出力CSVパス（未指定なら標準出力）")
    ap.add_argument("--force", action="store_true", help="NGワード検査で違反があっても出力する")
    args = ap.parse_args()

    try:
        with open(args.infile, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"✗ 入力ファイルが見つかりません: {args.infile}", file=sys.stderr)
        return 1

    posts = parse_batch(text)
    if not posts:
        print("✗ 投稿を1件も抽出できませんでした（R1形式の見出し/フェンスを確認）。", file=sys.stderr)
        return 1

    # 最終防壁
    issues = check_compliance(posts)
    if issues:
        print("✗ NGワード/プレースホルダ検査で違反を検出（threads-complianceスキルで要修正）:", file=sys.stderr)
        for m in issues:
            print(f"   - {m}", file=sys.stderr)
        if not args.force:
            print("   → 修正後に再実行してください（強制出力は --force）。", file=sys.stderr)
            return 2

    slots_a = [s.strip() for s in args.slots_a.split(",") if s.strip()]
    slots_b = [s.strip() for s in args.slots_b.split(",") if s.strip()]
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    if args.account_b:
        warn_account_gap(slots_a, slots_b)

    # グループごとに分け、アカウント/スケジュールを割り当て
    group_to_account = {"A": args.account_a}
    if args.account_b:
        group_to_account["B"] = args.account_b

    rows = []
    by_group: dict[str, list[dict]] = {}
    for p in posts:
        by_group.setdefault(p["group"], []).append(p)

    for grp, plist in by_group.items():
        account = group_to_account.get(grp)
        if not account:
            print(f"  ⚠️ アカ{grp} に対応する account 名が未指定。スキップ。", file=sys.stderr)
            continue
        slots = slots_a if grp == "A" else slots_b
        schedule = build_schedule(len(plist), start, slots)
        for p, dt in zip(plist, schedule):
            rows.append({
                "row_id": f"{args.prefix}_{p['id']}",
                "account": account,
                "post_datetime": dt,
                "text": p["body"],
                "media_type": "TEXT",
                "media_url": "",
                "reply_to": "",
                "reply_control": args.reply_control,
                "status": "queued",
                "posted_id": "",
                "posted_at": "",
                "error": "",
            })

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=POSTS_HEADERS)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    out_text = buf.getvalue()

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            f.write(out_text)
        print(f"✓ {len(rows)} 件を書き出しました: {args.out}", file=sys.stderr)
        if issues:
            print("  ⚠️ NGワード違反を含んだまま --force 出力しました。投稿前に必ず修正を。", file=sys.stderr)
        print("  次: postsタブへ貼り付け → DRY_RUN=1 で疎通 → 実投稿。", file=sys.stderr)
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
