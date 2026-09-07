"""殿堂入り（自アカの長期の当たり投稿DB）の構築とプロンプト用抽出（純関数・依存なし）。

★なぜ必要か（2026-09-07 実測）
インサイトは全期間ぶん蓄積されていた（takumi 7,958行・161投稿・6月の投稿も健在）のに、
生成AIに渡る勝ち投稿は短い傾向窓のTOP3だけだった。結果、takumi は自己ベスト(3,759表示・6/18)を、
澪は上位5本すべてを一度も見ないまま次の投稿を作っていた。

そこで「全期間の表示回数 上位30本」を `殿堂入り_<acc>` タブにDBとして持ち、生成のたびに
そこから数本を見せる。人が手で入れた行（保護が非空）は自動更新で消さない。
設計= docs/superpowers/specs/2026-09-07-hall-of-fame-design.md
"""
from __future__ import annotations

from datetime import datetime

LIMIT = 30          # 殿堂入りの保持本数（オーナー決定）
PROMPT_K = 3        # 1回の生成でAIに見せる本数（具体例は3〜7本という設計原則に従う）
# ローテーションは上位 ROTATE_POOL 本の中で回す。30本を平等に回すと「殿堂入り23位」のような
# 弱い実例ばかり見せる回が出てしまい、「当たりに寄せる」という目的から外れる。
ROTATE_POOL = 10


def _i(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _latest_per_post(rows: list[dict]) -> list[dict]:
    """posted_id ごとに snapshot_date が最大の行を残す（analyzer と同じ考え方）。"""
    best: dict[str, tuple[str, dict]] = {}
    for r in rows:
        pid = str(r.get("posted_id") or "")
        if not pid:
            continue
        d = str(r.get("snapshot_date") or "")
        if pid not in best or d >= best[pid][0]:
            best[pid] = (d, r)
    return [v[1] for v in best.values()]


def build_hall_of_fame(insight_rows: list[dict], posts: list[dict],
                       existing: list[dict] | None = None, *,
                       limit: int = LIMIT, now: datetime | None = None) -> list[dict]:
    """全期間の表示回数 上位 `limit` 本を組む。保護行は温存し、残枠を自動収穫で埋める。

    - `insight_rows` … `インサイト_<acc>` の全行（posted_id×snapshot_date）
    - `posts` … `投稿_<acc>` の行（本文・型ラベルを posted_id で結合。追加読み取りはしない）
    - `existing` … 現在の `殿堂入り_<acc>` の行（`protected` が非空なら人の手＝消さない）
    本文が結合できない投稿は入れない（AIに空の実例を見せないため）。
    """
    updated_at = (now or datetime.now()).strftime("%Y-%m-%d")
    by_pid = {}
    for p in posts or []:
        pid = str(p.get("posted_id") or "")
        if pid and str(p.get("text") or "").strip():
            by_pid[pid] = p

    out: list[dict] = []
    seen: set[str] = set()

    # 1) 人が保護した行は順序ごと温存（自動更新で消さない）
    for r in existing or []:
        if not str(r.get("protected") or "").strip():
            continue
        pid = str(r.get("posted_id") or "")
        if pid and pid in seen:
            continue
        seen.add(pid)
        out.append({
            "posted_id": pid, "post_datetime": r.get("post_datetime", ""),
            "text": r.get("text", ""), "views": _i(r.get("views")),
            "engagement_rate": r.get("engagement_rate", ""),
            "hook_type": r.get("hook_type", ""), "content_type": r.get("content_type", ""),
            "protected": str(r.get("protected") or "").strip(),
            "note": r.get("note", ""), "updated_at": r.get("updated_at", updated_at),
        })
        if len(out) >= limit:
            break

    # 2) 残枠を「全期間の表示回数 上位」で埋める
    if len(out) < limit:
        cand = [r for r in _latest_per_post(insight_rows or [])
                if str(r.get("posted_id") or "") in by_pid
                and str(r.get("posted_id")) not in seen]
        cand.sort(key=lambda r: _i(r.get("views")), reverse=True)
        for r in cand[:limit - len(out)]:
            pid = str(r.get("posted_id"))
            p = by_pid[pid]
            out.append({
                "posted_id": pid, "post_datetime": r.get("post_datetime", ""),
                "text": p.get("text", ""), "views": _i(r.get("views")),
                "engagement_rate": r.get("engagement_rate", ""),
                "hook_type": p.get("hook_type", ""), "content_type": p.get("content_type", ""),
                "protected": "", "note": "", "updated_at": updated_at,
            })

    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def select_for_prompt(rows: list[dict], now: datetime, k: int = PROMPT_K,
                      rotate_pool: int = ROTATE_POOL) -> list[dict]:
    """殿堂入りから今回AIに見せる k 本を選ぶ（上位プール内の決定的ローテーション）。

    - 毎回同じ上位3本だと模倣が固定化して投稿が同質化するので、日ごとに開始位置をずらす。
    - ただし回すのは上位 `rotate_pool` 本の中だけ（弱い実例を見せない）。
    同じ日に何度実行しても同じ結果（再現性・テスト可能）。
    """
    pool = [r for r in rows or [] if str(r.get("text") or "").strip()][:max(1, rotate_pool)]
    if not pool or k <= 0:
        return []
    k = min(k, len(pool))
    start = now.toordinal() % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(k)]
