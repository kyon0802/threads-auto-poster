"""登録済みアカウントの棚卸し（純関数・読み取り専用）。

なぜ必要か（2026-09-11）:
「前回発行したトークンがまだ使えるのか」「そもそもどのシートに登録済みなのか」を
確かめる手段が無く、分からないなら認可からやり直すしかない状態だった。
長期トークンは60日有効なので、生きていれば取り直す必要はない。

★トークンの値そのものは絶対に返さない。棚卸しに要るのは「あるか」「いつ更新したか」だけで、
値は要らない（CLAUDE.md §17b：アクセストークンは Secrets と非公開シートのみ）。
"""
from __future__ import annotations

from datetime import datetime

from .sheets import is_outsourced

# 長期トークンの有効期間（Threads API 仕様・CLAUDE.md §3）。
LONG_LIVED_DAYS = 60
# 残りこれ以下で警告。publisher は7日経過で自動リフレッシュするため、
# それが効いていれば警告は出ない＝出たら「自動更新が回っていない」サインでもある。
WARN_DAYS_LEFT = 7

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_dt(s) -> datetime | None:
    s = str(s or "").strip()
    if not s:
        return None
    for f in _FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def summarize_accounts(rows: list[dict], *, now: datetime | None = None) -> list[dict]:
    """accounts タブの行から、トークンの状態だけを要約して返す。

    返り値の各要素:
      account / is_outsourced / has_user_id / has_token
      age_days   … トークン更新日時からの経過日数（不明なら None）
      days_left  … 失効までの残り日数（不明なら None）
      status     … missing（未登録）/ unknown（更新日時なし）/ expired / warn / ok
    ★access_token の値は含めない。
    """
    now = now or datetime.now()
    out = []
    for r in rows:
        account = str(r.get("account") or "").strip()
        if not account:
            continue
        has_token = bool(str(r.get("access_token") or "").strip())
        updated = _parse_dt(r.get("token_updated_at"))
        age = days_left = None
        if has_token and updated is not None:
            age = (now - updated).days
            days_left = LONG_LIVED_DAYS - age

        if not has_token:
            status = "missing"
        elif days_left is None:
            # トークンはあるが更新日時が空。publisher が次回 now で初期化するので実害は小さい。
            status = "unknown"
        elif days_left <= 0:
            status = "expired"
        elif days_left <= WARN_DAYS_LEFT:
            status = "warn"
        else:
            status = "ok"

        out.append({
            "account": account,
            "is_outsourced": is_outsourced(r),
            "has_user_id": bool(str(r.get("user_id") or "").strip()),
            "has_token": has_token,
            "age_days": age,
            "days_left": days_left,
            "status": status,
        })
    return out
