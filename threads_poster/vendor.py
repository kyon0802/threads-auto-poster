"""外注アカウントの「作業量と質」を測る（純関数・AI不使用・読み取り専用）。

なぜ analyzer.py と分けるか:
  analyzer.py は「自社が書いた投稿の何が当たったか」を測る（勝ちパターンの抽出＝生成の材料）。
  こちらは「外注先がどれだけ手を動かしたか」を測る（外注管理＝発注者としての監督）。
  見たい問いが違うので、指標も出力先も別にする。

指標は 2026-09-08 の実測分析（外注2アカ・33日・151投稿）で実際に判断材料になったものだけを採る:
  - 投稿本数 / 稼働日数 … 契約どおりの本数が出ているか
  - ユニーク本文数 / 使い回し率 … **実際に何本書いたか**（見かけの本数と制作量は違う）
  - 時間帯分布 … 反応の良い時間に置けているか
  - 表示の合計/中央値 … 成果側の最低限
本文の照合は「空白を除いた完全一致」。表記ゆれまで拾う類似判定は誤検知が怖いので採らない。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime

_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_dt(s) -> datetime | None:
    """シートの日時文字列（JST・naive）を datetime に。読めなければ None。"""
    s = str(s or "").strip()
    if not s:
        return None
    for f in _FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _to_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def _norm_text(t) -> str:
    """本文の照合キー。空白（改行含む）を全て除去した文字列。"""
    return re.sub(r"\s+", "", str(t or ""))


def latest_per_post(rows: list[dict]) -> list[dict]:
    """インサイトの日次スナップショットを投稿単位に畳む（最新の取得日を採用）。

    インサイトは posted_id × 取得日 で毎日1行積まれるため、素直に数えると
    1本の投稿が日数ぶんに膨らみ、外注の作業量を過大評価してしまう。
    """
    best: dict[str, dict] = {}
    for r in rows:
        pid = str(r.get("posted_id") or "").strip()
        if not pid:
            continue
        cur = best.get(pid)
        if cur is None or str(r.get("snapshot_date") or "") >= str(cur.get("snapshot_date") or ""):
            best[pid] = r
    return list(best.values())


def summarize_activity(rows: list[dict], *, days: int = 7, today: date | None = None) -> dict:
    """外注アカ1つぶんの活動サマリを返す。

    引数:
      rows  … インサイトタブの行（posted_id / post_datetime / text / views / snapshot_date）
      days  … 集計期間（既定7日＝週次レポートの窓）
      today … 期間の終端（既定は本日）

    返り値の主なキー:
      posts / active_days / unique_texts / reused / reuse_rate(%) / text_missing
      by_hour(24要素) / views_total / views_median / low_views(表示5以下の本数)
    """
    today = today or date.today()
    posts = latest_per_post(rows)

    # 期間窓（today を含む直近 days 日）
    dated = []
    for r in posts:
        dt = _parse_dt(r.get("post_datetime"))
        if dt is None:
            continue
        if 0 <= (today - dt.date()).days < days:
            dated.append((dt, r))

    by_hour = [0] * 24
    day_set, views, texts = set(), [], []
    text_missing = 0
    for dt, r in dated:
        by_hour[dt.hour] += 1
        day_set.add(dt.date())
        views.append(_to_int(r.get("views")))
        key = _norm_text(r.get("text"))
        if key:
            texts.append(key)
        else:
            text_missing += 1

    n = len(dated)
    uniq = len(set(texts))
    reused = len(texts) - uniq
    # 使い回し率の分母は「本文が取れている投稿」だけ。本文が空の行を分母に入れると
    # 収集前の古い行があるだけで率が変わってしまい、週ごとの比較ができなくなる。
    reuse_rate = round(reused / len(texts) * 100) if texts else 0
    sv = sorted(views)

    return {
        "posts": n,
        "active_days": len(day_set),
        "idle_days": max(0, days - len(day_set)),
        "unique_texts": uniq,
        "reused": reused,
        "reuse_rate": reuse_rate,
        "text_missing": text_missing,
        "by_hour": by_hour,
        "views_total": sum(views),
        "views_median": sv[len(sv) // 2] if sv else 0,
        "low_views": sum(1 for v in views if v <= 5),
        "days": days,
    }
