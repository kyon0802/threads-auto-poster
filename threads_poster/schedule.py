"""投稿スケジュール生成：1日 N 本を、指定の時間帯にランダム配置（最低間隔つき）。

製造業アカウント（takumi_kojo_navi）の方針:
  - 1日4本（昼1本＋夜3本）
  - 昼: 12:00 前後（既定 ±30分＝11:30〜12:30）に1本
  - 夜: 18:00〜23:00 にランダムで3本
  - 投稿どうしの**最低間隔は30分**。ただし「30分ごとの等間隔」ではなく、最低間隔を確保した上で
    残りの空き時間を乱数で割り振り**ランダムに配置**する（厳密一様分布ではないが毎回ばらつく）。

設計:
  - すべて「その日の0時からの経過分(int)」で計算してから日時へ変換する（丸め誤差で
    最低間隔が30分を割らないよう、終始 **整数分** で扱う）。
  - 乱数は `rng`（`random.Random`）を**注入可能**にしてテストで再現できるようにする
    （`now_fn` / `generate_fn` の注入と同じ方針）。本番は既定の `random.Random()`。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


def _random_times_with_min_gap(rng: random.Random, start_min: int, end_min: int,
                               count: int, min_gap_min: int) -> list[int]:
    """[start_min, end_min] の分区間に count 個の時刻(分)を、隣接間隔が min_gap_min 以上に
    なるようランダムに配置して**昇順**で返す（等間隔ではない）。

    アルゴリズム: 最低間隔ぶんを先に確保した「空き時間」free = 区間幅 -(count-1)*gap を、
    count 個の乱数オフセット（整数）として引き、昇順に並べて i*gap を足し戻す。
    これで「昇順・各間隔 >= gap・全点が区間内」が**厳密に**保証される（整数演算で丸めなし）。
    ※offsets は順序統計量のため厳密な一様分布ではない（中央が中心寄り）。要件＝最低間隔と
      毎回のばらつきは満たす。完全一様が必要なら将来 rejection sampling に差し替え可。
    """
    if count <= 0:
        return []
    span = end_min - start_min
    free = span - (count - 1) * min_gap_min
    if free < 0:
        raise ValueError(
            f"区間 {span} 分に最低間隔 {min_gap_min} 分で {count} 本は配置できません")
    offsets = sorted(rng.randint(0, free) for _ in range(count))
    return [start_min + off + i * min_gap_min for i, off in enumerate(offsets)]


def daily_slots_minutes(rng: random.Random,
                        noon_center_min: int = 12 * 60, noon_jitter_min: int = 30,
                        evening_start_min: int = 18 * 60, evening_end_min: int = 23 * 60,
                        evening_count: int = 3, min_gap_min: int = 30) -> list[int]:
    """1日分のスロット（その日の0時からの経過分・**昇順**）を返す。

    既定 = 昼1本（12:00±30分）＋ 夜 evening_count 本（18:00〜23:00・最低間隔 min_gap_min）。
    昼(<=12:30)は夜の開始(18:00)より必ず前なので、戻り値は全体として昇順になる。

    防御: 昼の最大時刻(center+jitter)と夜開始の間も最低間隔を満たすことを保証する。
    既定では330分の余裕があり常に成立。パラメータを上書きして窓が近接した場合は
    昇順・最低間隔が壊れるため、ここで早期に検知する（generator/reschedule は既定で使用）。
    """
    if noon_center_min + noon_jitter_min + min_gap_min > evening_start_min:
        raise ValueError(
            f"昼の最大({noon_center_min + noon_jitter_min})＋最低間隔({min_gap_min}) が "
            f"夜開始({evening_start_min}) を超えます。窓設定を見直してください。")
    noon = noon_center_min + rng.randint(-noon_jitter_min, noon_jitter_min)
    evening = _random_times_with_min_gap(
        rng, evening_start_min, evening_end_min, evening_count, min_gap_min)
    return [noon] + evening


# 事業別の1日スロット時間帯プリセット（generator/fill_week が共有）。
# seizogyo（製造業）＝昼12:00前後＋夜18:00-23:00に3本。
# uranai（占い）   ＝午前8:00-11:30＋夕方17:00-23:59に3本。
PRESETS = {
    "seizogyo": dict(noon_center_min=12 * 60, noon_jitter_min=30,
                     evening_start_min=18 * 60, evening_end_min=23 * 60,
                     evening_count=3, min_gap_min=30),
    "uranai": dict(noon_center_min=9 * 60 + 45, noon_jitter_min=105,        # 午前 8:00-11:30
                   evening_start_min=17 * 60, evening_end_min=23 * 60 + 59,  # 夕方-23:59
                   evening_count=3, min_gap_min=30),
}


def build_schedule(n: int | None = None, *, start_date: datetime, tz=None,
                   rng: random.Random | None = None, days: int | None = None,
                   start_offset_days: int = 1, not_before: datetime | None = None,
                   **daily_kwargs) -> list[str]:
    """投稿に post_datetime（JST文字列 'YYYY-MM-DD HH:MM'）を割り当てて返す。

    2つの停止条件:
      - n 本モード（既定）: n 本そろうまで詰める。端数日は早い順に採用。
      - days 日モード（days を渡す）: start から days 日分のスロットを全部出す（n は無視）。

    - 開始日 = start_date + start_offset_days（既定 1＝翌日。0 なら当日から）。
    - not_before を渡すと、それ以前（過去）のスロットは除外（当日は現在時刻以降だけ）。
    - 各日ごとに時刻を新規ランダム生成（毎日同じ時間にならない）。

    tz は未使用（start_date が aware な前提）。互換のため受けるだけ。
    """
    rng = rng or random.Random()
    out: list[str] = []
    day = 0
    while True:
        if days is not None:
            if day >= days:
                break
        elif len(out) >= (n or 0):
            break
        base = (start_date + timedelta(days=day + start_offset_days)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for m in daily_slots_minutes(rng, **daily_kwargs):
            if days is None and len(out) >= (n or 0):
                break
            dt = base + timedelta(minutes=m)
            if not_before is not None and dt <= not_before:
                continue  # 過去スロット（当日の現在時刻以前）はスキップ
            out.append(dt.strftime("%Y-%m-%d %H:%M"))
        day += 1
    return out
