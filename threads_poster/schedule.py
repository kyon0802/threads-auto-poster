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


def build_schedule(n: int, *, start_date: datetime, tz=None,
                   rng: random.Random | None = None, **daily_kwargs) -> list[str]:
    """n 本の投稿に post_datetime（JST文字列 'YYYY-MM-DD HH:MM'）を割り当てて返す。

    - start_date の**翌日**から開始し、1日 `len(daily_slots_minutes())` 本（既定4本）ずつ詰める。
    - 各日ごとに時刻を新規ランダム生成する（毎日同じ時間にならない）。
    - 端数（最終日に4本に満たない分）は、その日の早い順（昼→夜）に必要数だけ採用する。

    tz は使用しない（start_date が既に aware な前提）。互換のため引数だけ受ける。
    """
    rng = rng or random.Random()
    out: list[str] = []
    day = 0
    while len(out) < n:
        base = (start_date + timedelta(days=day + 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for m in daily_slots_minutes(rng, **daily_kwargs):
            if len(out) >= n:
                break
            out.append((base + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M"))
        day += 1
    return out
