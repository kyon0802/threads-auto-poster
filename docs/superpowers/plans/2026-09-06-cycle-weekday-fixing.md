# PDCAサイクルの曜日固定 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成サイクルを「起点日から3日ごと」から「毎週 日曜・水曜の週2回」へ、レポートを「週1回・日曜」へ固定し、生成本数を曜日から自動計算する。

**Architecture:** `main_weekly.py` の単一ゲート `is_cycle_day`（起点アンカー方式）を、曜日ベースの2つのゲート（生成／レポート）へ置き換える。生成本数は固定値をやめ「次のサイクル日までの日数 × 4本」で算出する。多本数生成の品質低下を避けるため、`Generator` 内でAI呼び出しだけを最大8本ずつに分割する（予約時刻の割り当てと `row_id` 採番は分割せず最後に1回）。

**Tech Stack:** Python 3 / pytest / 既存の `MemoryStore` によるAPI不要モックテスト

**Spec:** [docs/superpowers/specs/2026-09-06-cross-account-analytics-design.md](../specs/2026-09-06-cross-account-analytics-design.md) の §8 と §10-2 の①

## Global Constraints

- 生成サイクル = **毎週 日曜・水曜**（週2回）／レポート = **毎週 日曜**（週1回）
- Python の `weekday()` は 月=0 火=1 水=2 木=3 金=4 土=5 **日=6**
- 生成本数 = **次のサイクル日までの日数 × `POSTS_PER_DAY`(=4)**（日曜→3日分12本／水曜→4日分16本）
- 1回のAI呼び出しで生成する上限 = **8本**
- Variable `GEN_POSTS_<NAME>` の用途は **「0 = その事業だけ生成オフ」に限定**。1以上の本数上書きは後方互換で残すが運用では設定しない
- `FORCE_CYCLE=1` は生成・レポートの**両方**を強制する（手動実行用）
- このrepoは**公開(public)**。トークン・APIキー・スプレッドシートID・メールアドレス・事業ノウハウを一切書かない（CLAUDE.md §17b）
- **既存108本のテストが全て通ること**（`python3 -m pytest tests/ -q`）
- 本計画の対象外：②トークン共通化、③横断分析基盤（別計画）
- コミットは Conventional Commits（本文は日本語可）。末尾に `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

### Task 1: 曜日ゲート関数への置き換え

**Files:**
- Modify: `main_weekly.py:60-72`（`CYCLE_ANCHOR` / `CYCLE_DAYS` / `is_cycle_day` のブロック）
- Modify: `main_weekly.py:22`（`from datetime import datetime, date` に `timedelta` を追加）
- Modify: `tests/test_phase2.py`（`test_cycle_gate_every_3_days` を削除＋`__main__` ランナーの呼び出し行も削除）
- Test: `tests/test_cycle_schedule.py`（新規）

**Interfaces:**
- Consumes: なし（このタスクが起点）
- Produces:
  - `GEN_WEEKDAYS: frozenset[int]` = `{2, 6}`（水・日）
  - `REPORT_WEEKDAY: int` = `6`（日）
  - `POSTS_PER_DAY: int` = `4`（**既存のまま。`main_monitor.py:31` が import しているので名前を変えない**）
  - `is_cycle_day(today: date) -> bool`
  - `is_report_day(today: date) -> bool`
  - `days_until_next_cycle(today: date) -> int`（1〜7）
  - **削除**：`CYCLE_ANCHOR` / `CYCLE_DAYS`（Task 2・3で参照を解消する）

---

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_cycle_schedule.py` を新規作成:

```python
"""PDCAサイクルの曜日ゲート（生成=日・水／レポート=日）。API不要・純関数のみ。

2026-09-06(日) / 09-07(月) / 09-08(火) / 09-09(水) / 09-10(木) / 09-11(金) / 09-12(土) / 09-13(日)
を基準日に使う（実在の曜日）。
"""
import os
import sys
from datetime import date

# リポジトリルートを import パスに追加（tests/ からの直実行・pytest 両対応）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_is_cycle_day_sunday_and_wednesday():
    """生成サイクル日は日曜と水曜だけ。"""
    from main_weekly import is_cycle_day
    assert is_cycle_day(date(2026, 9, 6))    # 日
    assert is_cycle_day(date(2026, 9, 9))    # 水
    assert is_cycle_day(date(2026, 9, 13))   # 翌週の日
    for d in (7, 8, 10, 11, 12):             # 月・火・木・金・土
        assert not is_cycle_day(date(2026, 9, d)), d
    print("  ✓ is_cycle_day（日・水のみ True）OK")


def test_is_report_day_sunday_only():
    """レポート日は日曜だけ。水曜は生成日だがレポート日ではない。"""
    from main_weekly import is_report_day
    assert is_report_day(date(2026, 9, 6))
    assert not is_report_day(date(2026, 9, 9))
    for d in (7, 8, 10, 11, 12):
        assert not is_report_day(date(2026, 9, d)), d
    print("  ✓ is_report_day（日のみ True）OK")


def test_days_until_next_cycle():
    """次の生成日までの日数＝そのサイクルで埋める日数。"""
    from main_weekly import days_until_next_cycle
    assert days_until_next_cycle(date(2026, 9, 6)) == 3    # 日→水（月火水）
    assert days_until_next_cycle(date(2026, 9, 9)) == 4    # 水→日（木金土日）
    assert days_until_next_cycle(date(2026, 9, 7)) == 2    # 月→水（FORCE_CYCLE時）
    assert days_until_next_cycle(date(2026, 9, 12)) == 1   # 土→日
    print("  ✓ days_until_next_cycle（日→3 / 水→4）OK")


def test_two_cycles_cover_the_week_without_gap_or_overlap():
    """2サイクルの合計が7日＝在庫に穴も重複も出ない（週2回運用の要）。"""
    from main_weekly import days_until_next_cycle
    total = days_until_next_cycle(date(2026, 9, 6)) + days_until_next_cycle(date(2026, 9, 9))
    assert total == 7, total
    print("  ✓ 週の被覆（3日+4日=7日）OK")


if __name__ == "__main__":
    test_is_cycle_day_sunday_and_wednesday()
    test_is_report_day_sunday_only()
    test_days_until_next_cycle()
    test_two_cycles_cover_the_week_without_gap_or_overlap()
    print("========== 全テスト PASS ==========")
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: FAIL — `ImportError: cannot import name 'is_report_day' from 'main_weekly'`

- [ ] **Step 3: `main_weekly.py` のサイクル定数を置き換える**

`main_weekly.py:22` の import を変更:

```python
from datetime import datetime, date, timedelta
```

`main_weekly.py:60-72`（`# ── 3日サイクル（PDCA）設定 ──` から `is_cycle_day` の定義まで）を、次で丸ごと置き換える:

```python
# ── PDCAサイクル設定（曜日固定・2026-09-06 変更）────────────────────────────
# 以前は「起点日(2026-06-28)から3日ごと」だったため、サイクル日が毎週ずれ、
# レポートの届く曜日も定まらなかった。生成は週2回（日・水）、レポートは週1回（日）に固定する。
# 頻度は実質据え置き（3日周期 ≒ 週2.33回 → 週2回）＝AI呼び出し回数・費用はほぼ変わらない。
# Python の weekday(): 月=0 火=1 水=2 木=3 金=4 土=5 日=6
GEN_WEEKDAYS = frozenset({6, 2})   # 日・水
REPORT_WEEKDAY = 6                 # 日
POSTS_PER_DAY = 4                  # ★main_monitor.py が import している。名前を変えない


def is_cycle_day(today: date) -> bool:
    """生成サイクル日か（週2回・日と水）。"""
    return today.weekday() in GEN_WEEKDAYS


def is_report_day(today: date) -> bool:
    """レポート日か（週1回・日）。生成日でもレポート日でない日がある（水曜）。"""
    return today.weekday() == REPORT_WEEKDAY


def days_until_next_cycle(today: date) -> int:
    """次の生成サイクル日までの日数（1〜7）。生成はこの日数ぶんの在庫を作る。

    日曜→水曜=3日（月火水）、水曜→日曜=4日（木金土日）。合計7日で週がちょうど埋まる。
    サイクル日以外（FORCE_CYCLE=1 での手動実行）でも、次のサイクル日までを埋める本数になる。
    """
    for d in range(1, 8):
        if (today + timedelta(days=d)).weekday() in GEN_WEEKDAYS:
            return d
    raise AssertionError("GEN_WEEKDAYS が空です（設定ミス）")
```

- [ ] **Step 4: 旧テストを削除する**

`tests/test_phase2.py` から関数 `test_cycle_gate_every_3_days`（`def test_cycle_gate_every_3_days():` から次の `def` の直前まで）を削除する。
あわせて `__main__` ブロック内の呼び出し行 `    test_cycle_gate_every_3_days()` も削除する。

削除する理由：3日周期は仕様として存在しなくなり、`CYCLE_ANCHOR` も消えるため、このテストは import エラーになる。同等の検証は `tests/test_cycle_schedule.py` が担う。

- [ ] **Step 5: テストを実行して成功を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: PASS（4 passed）

- [ ] **Step 6: コミット**

```bash
git add main_weekly.py tests/test_cycle_schedule.py tests/test_phase2.py
git commit -m "$(cat <<'EOF'
feat: PDCAサイクルを曜日固定にする（生成=日・水／レポート=日）

起点日アンカーの3日周期はサイクル日が毎週ずれ、レポートの曜日も定まらなかった。
weekday ベースの is_cycle_day / is_report_day / days_until_next_cycle に置き換える。
日曜→水曜=3日・水曜→日曜=4日で週がちょうど埋まる（穴も重複も出ない）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 生成本数を曜日から動的に決める

**Files:**
- Modify: `main_weekly.py:127-133`（`n_posts_for`）
- Modify: `tests/test_phase2.py`（`test_n_posts_for_4perday_businesses` を削除＋`__main__` の呼び出し行も削除）
- Test: `tests/test_cycle_schedule.py`（Task 1 で作ったファイルに追記）

**Interfaces:**
- Consumes: Task 1 の `days_until_next_cycle(today)` / `POSTS_PER_DAY` / `SCHEDULE_FN_BY_BUSINESS`（既存）
- Produces: `n_posts_for(name: str, env, default_n: int, today: date) -> int`
  **第4引数 `today` が新規（必須・位置引数）**。Task 3 の `main()` はこの順で呼ぶ。

---

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_cycle_schedule.py` の `if __name__ == "__main__":` ブロックの**手前**に追記:

```python
def test_n_posts_for_is_dynamic_by_weekday():
    """4本/日事業の生成本数は曜日で変わる（日=3日分12本 / 水=4日分16本）。"""
    from main_weekly import n_posts_for
    sunday, wednesday = date(2026, 9, 6), date(2026, 9, 9)
    assert n_posts_for("seizogyo", {}, 5, sunday) == 12
    assert n_posts_for("seizogyo", {}, 5, wednesday) == 16
    assert n_posts_for("seizogyo2", {}, 5, sunday) == 12
    assert n_posts_for("seizogyo3", {}, 5, wednesday) == 16
    assert n_posts_for("meguri", {}, 5, wednesday) == 16
    assert n_posts_for("other", {}, 5, wednesday) == 5   # 4本/日対象外は既定のまま
    print("  ✓ n_posts_for（曜日で12/16本・対象外は既定）OK")


def test_n_posts_for_zero_variable_turns_generation_off():
    """GEN_POSTS_<NAME>=0 は「その事業だけ生成オフ」として残す（用途を0に限定）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "0"}, 5, date(2026, 9, 9)) == 0
    print("  ✓ GEN_POSTS_<NAME>=0（生成オフ）OK")


def test_n_posts_for_explicit_override_is_backward_compatible():
    """1以上の明示指定は後方互換で残す（運用では設定しない）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "9"}, 5, date(2026, 9, 9)) == 9
    print("  ✓ GEN_POSTS_<NAME>=9（明示上書き）OK")


def test_n_posts_for_empty_variable_falls_back_to_dynamic():
    """Variable を削除/空にしたら動的計算に戻る（空文字を int() して落ちないこと）。"""
    from main_weekly import n_posts_for
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": ""}, 5, date(2026, 9, 6)) == 12
    assert n_posts_for("seizogyo2", {"GEN_POSTS_SEIZOGYO2": "  "}, 5, date(2026, 9, 9)) == 16
    print("  ✓ 空のGEN_POSTS_<NAME>は動的計算にフォールバック OK")
```

`if __name__ == "__main__":` ブロックにも呼び出しを4行追加:

```python
    test_n_posts_for_is_dynamic_by_weekday()
    test_n_posts_for_zero_variable_turns_generation_off()
    test_n_posts_for_explicit_override_is_backward_compatible()
    test_n_posts_for_empty_variable_falls_back_to_dynamic()
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: FAIL — `TypeError: n_posts_for() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: `n_posts_for` を書き換える**

`main_weekly.py` の `n_posts_for` を次で置き換える:

```python
def n_posts_for(name: str, env, default_n: int, today: date) -> int:
    """事業ごとの1アカ生成本数。

    4本/日スケジュール対象の事業は「次のサイクル日までの日数 × POSTS_PER_DAY」で
    **自動計算**する（日曜=3日分12本 / 水曜=4日分16本）。曜日で必要日数が変わるため
    固定値にできない（固定12本のままだと4日区間で毎週1日分＝4本の穴が空く）。

    Variable GEN_POSTS_<NAME> は **0 = その事業だけ生成オフ** に用途を限定する。
    1以上での本数上書きは後方互換で受け付けるが、運用では設定しない（設定すると穴が空く）。
    空文字/未設定なら動的計算にフォールバックする。
    4本/日スケジュール対象外の事業は GEN_POSTS_PER_ACCOUNT（既定5）のまま。
    """
    if name not in SCHEDULE_FN_BY_BUSINESS:
        return default_n
    raw = str(env.get(f"GEN_POSTS_{name.upper()}", "")).strip()
    if raw:
        return int(raw)
    return days_until_next_cycle(today) * POSTS_PER_DAY
```

- [ ] **Step 4: 旧テストを削除する**

`tests/test_phase2.py` から関数 `test_n_posts_for_4perday_businesses`（`def test_n_posts_for_4perday_businesses():` から次の `def` の直前まで）を削除し、`__main__` ブロックの `    test_n_posts_for_4perday_businesses()` の行も削除する。

削除する理由：引数が3つ前提で、かつ「常に12本」を固定で検証しており、動的計算と両立しない。

- [ ] **Step 5: テストを実行して成功を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: PASS（8 passed）

- [ ] **Step 6: コミット**

```bash
git add main_weekly.py tests/test_cycle_schedule.py tests/test_phase2.py
git commit -m "$(cat <<'EOF'
feat: 生成本数を「次サイクルまでの日数×4本」で動的に決める

週2回サイクルでは間隔が3日と4日に分かれるため、固定12本だと4日区間で
毎週4本の在庫穴が空く。GEN_POSTS_<NAME> の用途は 0=生成オフ に限定し、
空/未設定なら動的計算へフォールバックする（空文字で int() が落ちないようにする）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 生成ゲートとレポートゲートを分離する

**Files:**
- Create: `main_weekly.py` に `decide_gates()` を追加（`days_until_next_cycle` の直後）
- Modify: `main_weekly.py:170-175`（サイクルゲートの早期return）
- Modify: `main_weekly.py:225-235`（生成の分岐に生成日ゲートを追加・`n_posts_for` の呼び出し）
- Modify: `main_weekly.py`（`Reporter(store).run(...)` をレポート日だけに・`if in_email:` をレポート日だけに・`sort_posts_tab` を生成日だけに）
- Test: `tests/test_cycle_schedule.py`（追記）

**Interfaces:**
- Consumes: Task 1 の `is_cycle_day` / `is_report_day`、Task 2 の `n_posts_for(name, env, default_n, today)`
- Produces: `decide_gates(today: date, env) -> tuple[bool, bool]`（`(生成するか, レポートするか)`）

---

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_cycle_schedule.py` の `if __name__ == "__main__":` の手前に追記:

```python
def test_decide_gates_by_weekday():
    """日=生成+レポート / 水=生成のみ / それ以外=何もしない。"""
    from main_weekly import decide_gates
    assert decide_gates(date(2026, 9, 6), {}) == (True, True)     # 日
    assert decide_gates(date(2026, 9, 9), {}) == (True, False)    # 水
    assert decide_gates(date(2026, 9, 7), {}) == (False, False)   # 月
    assert decide_gates(date(2026, 9, 12), {}) == (False, False)  # 土
    print("  ✓ decide_gates（日=生成+報告 / 水=生成のみ / 他=なし）OK")


def test_decide_gates_force_cycle_forces_both():
    """FORCE_CYCLE=1 は手動実行用。生成もレポートも両方走らせる。"""
    from main_weekly import decide_gates
    assert decide_gates(date(2026, 9, 7), {"FORCE_CYCLE": "1"}) == (True, True)
    assert decide_gates(date(2026, 9, 9), {"FORCE_CYCLE": "1"}) == (True, True)
    print("  ✓ decide_gates（FORCE_CYCLE=1 で両方強制）OK")
```

`__main__` ブロックに2行追加:

```python
    test_decide_gates_by_weekday()
    test_decide_gates_force_cycle_forces_both()
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: FAIL — `ImportError: cannot import name 'decide_gates' from 'main_weekly'`

- [ ] **Step 3: `decide_gates` を追加する**

`main_weekly.py` の `days_until_next_cycle` の定義直後に追加:

```python
def decide_gates(today: date, env) -> tuple[bool, bool]:
    """(生成するか, レポートするか) を返す。

    生成は週2回（日・水）、レポートは週1回（日）。水曜は生成だけ走りレポートは出ない。
    FORCE_CYCLE=1（手動実行）は両方を強制する。
    """
    if env.get("FORCE_CYCLE") == "1":
        return True, True
    return is_cycle_day(today), is_report_day(today)
```

- [ ] **Step 4: テストを実行して成功を確認**

Run: `python3 -m pytest tests/test_cycle_schedule.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: `main()` の早期returnを2ゲートに置き換える**

`main_weekly.py` の以下（既存 170-175 行目付近）:

```python
    # 3日サイクルゲート: cron は毎日叩くが、起点日から3日ごとの日だけ本処理（分析→レポート→生成→メール）
    # を実行する。それ以外の日は即終了（次サイクルまで待機）。FORCE_CYCLE=1 で手動実行時はバイパス。
    today = datetime.now(ZoneInfo(tz_name)).date()
    if os.environ.get("FORCE_CYCLE") != "1" and not is_cycle_day(today):
        log.info("3日サイクル外（起点=%s / 本日=%s / 周期=%d日）→ 本日は実行しません（次サイクルまで待機）",
                 CYCLE_ANCHOR, today, CYCLE_DAYS)
        return 0
```

を、次で置き換える:

```python
    # サイクルゲート（曜日固定）: cron は毎日叩くが、生成は日・水、レポートは日だけ実行する。
    # どちらでもない日は即終了。FORCE_CYCLE=1（手動実行）は両方を強制する。
    today = datetime.now(ZoneInfo(tz_name)).date()
    do_generate_cycle, do_report = decide_gates(today, os.environ)
    if not (do_generate_cycle or do_report):
        log.info("本日 %s(%s) は生成日でもレポート日でもないため実行しません（生成=日・水 / レポート=日）",
                 today, "月火水木金土日"[today.weekday()])
        return 0
    log.info("本日 %s(%s) の実行内容: 生成=%s / レポート=%s（生成対象日数=%d日）",
             today, "月火水木金土日"[today.weekday()],
             "する" if do_generate_cycle else "しない",
             "する" if do_report else "しない", days_until_next_cycle(today))
```

- [ ] **Step 6: 生成の分岐に生成日ゲートを追加する**

`main_weekly.py` の以下（既存 226-233 行目付近）:

```python
                gen_info = None
                if not generate:
                    gen_info = {"ok": None, "reason": "生成オフ（GENERATE_POSTS=0 または PAUSED=1）"}
                else:
                    acc_n_posts = n_posts_for(name, os.environ, n_posts)
```

を、次で置き換える:

```python
                gen_info = None
                if not do_generate_cycle:
                    gen_info = {"ok": None, "reason": "本日は生成日ではありません（生成=日・水）"}
                elif not generate:
                    gen_info = {"ok": None, "reason": "生成オフ（GENERATE_POSTS=0 または PAUSED=1）"}
                else:
                    acc_n_posts = n_posts_for(name, os.environ, n_posts, today)
```

- [ ] **Step 7: レポート・メール・並べ替えをそれぞれのゲートに紐づける**

同じアカウントループ内の3箇所を変更する。

(a) Reporter のタブ追記をレポート日だけにする。以下:

```python
                Reporter(store).run(acc, analysis, gen_date)
                totals["reported"] += 1
```

を:

```python
                # レポートタブへの追記は週1回（日曜）だけ。生成日（水曜）は分析と生成のみ。
                if do_report:
                    Reporter(store).run(acc, analysis, gen_date)
                    totals["reported"] += 1
```

(b) HTML生成・方針生成・メール蓄積をレポート日だけにする。以下:

```python
                if in_email:
```

を:

```python
                if in_email and do_report:
```

(c) 投稿タブの並べ替えを生成日だけにする。以下:

```python
                # 投稿タブを投稿日時の降順に整える（新しい日付が上）。生成で追記した行も上に来る。
                store.sort_posts_tab(acc, descending=True)
```

を:

```python
                # 投稿タブを投稿日時の降順に整える（新しい日付が上）。生成で追記した行も上に来る。
                # 行を追加していないレポート専用日は並べ替え不要（無駄なSheets APIを撃たない）。
                if do_generate_cycle:
                    store.sort_posts_tab(acc, descending=True)
```

- [ ] **Step 8: 全テストを実行して回帰が無いことを確認**

Run: `python3 -m pytest tests/ -q`
Expected: PASS（**116 passed**。内訳：ベースライン108 − 旧テスト2本削除（Task 1・2）+ `test_cycle_schedule.py` の10本）

`CYCLE_ANCHOR` / `CYCLE_DAYS` への参照が残っていないことも確認する:

Run: `grep -rn "CYCLE_ANCHOR\|CYCLE_DAYS" --include=*.py .`
Expected: 出力なし（1件も残っていない）

- [ ] **Step 9: コミット**

```bash
git add main_weekly.py tests/test_cycle_schedule.py
git commit -m "$(cat <<'EOF'
feat: 生成ゲートとレポートゲートを分離する

これまで単一の is_cycle_day で生成もレポートも同じ日に走っていた。
生成=日・水（週2回）、レポート=日（週1回）に分け、水曜は生成のみとする。
レポート専用日は投稿行を追加しないため sort_posts_tab も撃たない。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: AI呼び出しを最大8本ずつに分割する

**Files:**
- Modify: `threads_poster/generator.py`（`MAX_POSTS_PER_CALL` 定数の追加・`build_prompt` に `avoid_texts` 引数・`Generator.__init__` に `batch_size`・`run()` の生成部）
- Test: `tests/test_generator_batching.py`（新規）

**Interfaces:**
- Consumes: 既存の `Generator` / `build_prompt` / `_call_generate_fn` / `MemoryStore`
- Produces:
  - `threads_poster.generator.MAX_POSTS_PER_CALL: int` = `8`
  - `build_prompt(..., avoid_texts: list[str] | None = None)`（末尾にキーワード引数を追加。既存呼び出しは無変更で動く）
  - `Generator(..., batch_size: int = MAX_POSTS_PER_CALL)`

**設計上の要点（守ること）:** 分割するのは **AI呼び出しだけ**。コンプラゲート・予約時刻の割り当て・`row_id` 採番・シート書込は分割せず最後に1回だけ行う。`run()` を複数回呼ぶ形にすると (1) `row_id` が `<prefix>-gYYYYMMDD-01` から採番し直されて**全タブ一意の不変条件を破り二重投稿の原因になる**、(2) 2回目の `_stock_anchor` が1回目で書いた行を見られず**同じ日付に二重予約する**。この2つを避けるための構造である。

---

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_generator_batching.py` を新規作成:

```python
"""生成の分割（1回のAI呼び出しは最大8本）。API不要・generate_fn をモックする。"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from threads_poster.sheets import MemoryStore
from threads_poster.generator import Generator, MAX_POSTS_PER_CALL

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=JST)   # 水曜＝4日分16本のサイクル


def _store():
    store = MemoryStore([{"account": "a1"}], [])
    store.profiles = {"a1": {"声": "テスト"}}
    # 分類を "NGワード" 以外にして禁止語ゼロにする（extract_ng_words はこの分類の行だけ拾う）。
    # "NGワード" にすると ルール の文字列がそのまま禁止語になり、モック本文が偶然引っかかりうる。
    store.guideline = [{"分類": "その他", "ルール": "本文に外部URLを書かない", "重大度": "中"}]
    return store


def _recording_gen(prompts: list):
    """呼ばれた prompt を記録し、毎回8本返すモック生成関数。"""
    def fn(prompt, hook_types=None):
        prompts.append(prompt)
        i = len(prompts)
        return [{"text": f"バッチ{i}の投稿{j}です。", "hook_type": "数字提示",
                 "content_type": "情報提供", "exemplar_id": ""} for j in range(8)]
    return fn


def test_max_posts_per_call_is_eight():
    assert MAX_POSTS_PER_CALL == 8
    print("  ✓ MAX_POSTS_PER_CALL=8 OK")


def test_16_posts_split_into_two_calls():
    """16本要求 → 8本×2回のAI呼び出しに分割される。"""
    prompts = []
    store = _store()
    res = Generator(store, "a1", generate_fn=_recording_gen(prompts),
                    now_fn=lambda: NOW, n_posts=16, status="draft").run({})
    assert len(prompts) == 2, len(prompts)
    assert len(res["written"]) == 16, len(res["written"])
    print("  ✓ 16本→2回のAI呼び出しに分割 OK")


def test_12_posts_split_into_eight_and_four():
    """12本要求 → 8本 + 4本。端数の回も1回として数える。"""
    prompts = []
    store = _store()
    Generator(store, "a1", generate_fn=_recording_gen(prompts),
              now_fn=lambda: NOW, n_posts=12, status="draft").run({})
    assert len(prompts) == 2, len(prompts)
    # 2回目のプロンプトは残り4本を要求している
    assert "4本作成" in prompts[1], prompts[1][:200]
    print("  ✓ 12本→8本+4本に分割 OK")


def test_8_posts_is_a_single_call():
    """上限ちょうどは分割しない（無駄なAI呼び出しを増やさない）。"""
    prompts = []
    store = _store()
    Generator(store, "a1", generate_fn=_recording_gen(prompts),
              now_fn=lambda: NOW, n_posts=8, status="draft").run({})
    assert len(prompts) == 1, len(prompts)
    print("  ✓ 8本ちょうどは1回 OK")


def test_row_ids_are_unique_across_batches():
    """★分割しても row_id は通し番号（全タブ一意＝二重投稿防止の不変条件）。"""
    prompts = []
    store = _store()
    res = Generator(store, "a1", generate_fn=_recording_gen(prompts),
                    now_fn=lambda: NOW, n_posts=16, status="draft").run({})
    assert len(set(res["written"])) == 16, res["written"]
    assert res["written"][0].endswith("-01")
    assert res["written"][-1].endswith("-16")
    print("  ✓ row_id が分割をまたいで一意・通し番号 OK")


def test_post_datetimes_are_unique_across_batches():
    """★分割しても予約時刻が重複しない（2回目が1回目と同じ日に載らない）。"""
    prompts = []
    store = _store()
    Generator(store, "a1", generate_fn=_recording_gen(prompts),
              now_fn=lambda: NOW, n_posts=16, status="draft").run({})
    dts = [p["post_datetime"] for p in store.posts]
    assert len(set(dts)) == 16, sorted(dts)
    print("  ✓ 予約時刻が分割をまたいで重複なし OK")


def test_second_prompt_lists_first_batch_texts_to_avoid_duplicates():
    """2回目のプロンプトに1回目の本文が入る（同じ切り口の重複生成を防ぐ）。"""
    prompts = []
    store = _store()
    Generator(store, "a1", generate_fn=_recording_gen(prompts),
              now_fn=lambda: NOW, n_posts=16, status="draft").run({})
    assert "既に作成済みの投稿" not in prompts[0]
    assert "既に作成済みの投稿" in prompts[1]
    assert "バッチ1の投稿0です。" in prompts[1]
    print("  ✓ 2回目のプロンプトに作成済み本文が入る OK")


def test_explicit_candidates_bypass_batching():
    """candidates を直接渡す経路（テスト/手動）は分割せず従来どおり。"""
    prompts = []
    store = _store()
    cands = [{"text": f"手動候補{j}です。", "hook_type": "数字提示",
              "content_type": "情報提供", "exemplar_id": ""} for j in range(3)]
    Generator(store, "a1", generate_fn=_recording_gen(prompts),
              now_fn=lambda: NOW, n_posts=16, status="draft").run({}, candidates=cands)
    assert prompts == []          # AIを一度も呼ばない
    assert len(store.posts) == 3
    print("  ✓ candidates 直接指定は分割経路を通らない OK")


if __name__ == "__main__":
    test_max_posts_per_call_is_eight()
    test_16_posts_split_into_two_calls()
    test_12_posts_split_into_eight_and_four()
    test_8_posts_is_a_single_call()
    test_row_ids_are_unique_across_batches()
    test_post_datetimes_are_unique_across_batches()
    test_second_prompt_lists_first_batch_texts_to_avoid_duplicates()
    test_explicit_candidates_bypass_batching()
    print("========== 全テスト PASS ==========")
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python3 -m pytest tests/test_generator_batching.py -q`
Expected: FAIL — `ImportError: cannot import name 'MAX_POSTS_PER_CALL' from 'threads_poster.generator'`

- [ ] **Step 3: `MAX_POSTS_PER_CALL` と `build_prompt` の `avoid_texts` を追加する**

`threads_poster/generator.py` の `HOOK_TYPES = [...]` の定義の直前に定数を追加:

```python
# 1回のAI呼び出しで作る投稿数の上限。週2回サイクルでは1回あたり最大16本になるが、
# 1回のリクエストでまとめて作らせると型のバリエーションが痩せ、max_tokens 切れの危険も上がる。
# 分割するのはAI呼び出しだけで、予約時刻の割り当てと row_id 採番は最後に1回だけ行う。
MAX_POSTS_PER_CALL = 8
```

`build_prompt` のシグネチャを変更:

```python
def build_prompt(account: str, profile: dict, guideline: list[dict], analysis: dict, n: int,
                 knowledge: str = "", exemplars: list[dict] | None = None,
                 hook_types: list[str] | None = None,
                 avoid_texts: list[str] | None = None) -> str:
```

`build_prompt` の `return (` の直前に、重複回避セクションの組み立てを追加:

```python
    # 分割生成の2回目以降に渡す「同じサイクルで既に作った本文」。同じ切り口の重複を防ぐ。
    avoid_section = ""
    if avoid_texts:
        joined = "\n".join(f"- {str(t)[:60]}" for t in avoid_texts if str(t).strip())
        if joined:
            avoid_section = (
                "\n## 既に作成済みの投稿（同じサイクル内・重複禁止）\n"
                "以下は今回のサイクルで既に作成済みの投稿の冒頭です。\n"
                "**同じ切り口・同じ数字・同じ言い回しを繰り返さないでください。**\n"
                f"{joined}\n"
            )
```

`build_prompt` の `return (...)` の中、`f"{type_section}\n"` の**次の行**に `f"{avoid_section}"` を挿入する:

```python
        f"{type_section}\n"
        f"{avoid_section}"
        f"{THREADS_HOOK_RULES}\n\n"
```

- [ ] **Step 4: `Generator` に `batch_size` を足し、`run()` の生成部を分割ループにする**

`Generator.__init__` のシグネチャに `batch_size` を追加（`rng=None` の後ろ）:

```python
                 schedule_fn=None, rng=None, batch_size: int = MAX_POSTS_PER_CALL):
```

`__init__` 本体の `self.rng = rng` の後に追加:

```python
        self.batch_size = max(1, int(batch_size))
```

`run()` の中の以下のブロック:

```python
        known_exemplar_ids = None
        if candidates is None:
            exemplars = self.store.get_exemplars(self.account)
            known_exemplar_ids = {str(ex.get("exemplar_id")) for ex in exemplars}
            hook_types = hook_types_for(profile)
            prompt = build_prompt(self.account, profile, guideline, analysis, self.n_posts,
                                  knowledge=knowledge, exemplars=exemplars, hook_types=hook_types)
            gen = self.generate_fn or make_anthropic_generate_fn(self.model, self.n_posts,
                                                                 hook_types=hook_types)
            candidates = _call_generate_fn(gen, prompt, hook_types)
```

を、次で置き換える:

```python
        known_exemplar_ids = None
        if candidates is None:
            exemplars = self.store.get_exemplars(self.account)
            known_exemplar_ids = {str(ex.get("exemplar_id")) for ex in exemplars}
            hook_types = hook_types_for(profile)
            # ★分割生成（2026-09-06）：AI呼び出しだけを batch_size ごとに分ける。
            # 2回目以降は作成済み本文を avoid_texts で渡し、同じ切り口の重複を防ぐ。
            # 予約時刻の割り当てと row_id 採番はこの後で1回だけ行う（分割して run() を
            # 複数回呼ぶと row_id が振り直されて全タブ一意が壊れ、在庫アンカーも効かない）。
            candidates = []
            remaining = self.n_posts
            while remaining > 0:
                chunk = min(remaining, self.batch_size)
                avoid = [c.get("text", "") if isinstance(c, dict) else str(c) for c in candidates]
                prompt = build_prompt(self.account, profile, guideline, analysis, chunk,
                                      knowledge=knowledge, exemplars=exemplars,
                                      hook_types=hook_types, avoid_texts=avoid)
                gen = self.generate_fn or make_anthropic_generate_fn(self.model, chunk,
                                                                     hook_types=hook_types)
                got = _call_generate_fn(gen, prompt, hook_types) or []
                candidates.extend(got)
                remaining -= chunk
                if not got:
                    # 空返しが続くと無限に近い呼び出しになるため打ち切る（残高不足等）
                    logger.warning("%s: 生成が空を返したため分割ループを打ち切ります（取得%d本）",
                                   self.account, len(candidates))
                    break
```

- [ ] **Step 5: テストを実行して成功を確認**

Run: `python3 -m pytest tests/test_generator_batching.py -q`
Expected: PASS（8 passed）

- [ ] **Step 6: 既存テストの回帰が無いことを確認**

Run: `python3 -m pytest tests/ -q`
Expected: PASS（124 passed）

- [ ] **Step 7: コミット**

```bash
git add threads_poster/generator.py tests/test_generator_batching.py
git commit -m "$(cat <<'EOF'
feat: AI呼び出しを最大8本ずつに分割する

週2回サイクルでは1回あたり最大16本になるが、まとめて作らせると型のバリエーションが
痩せ max_tokens 切れの危険も上がる。分割するのはAI呼び出しだけで、コンプラゲート・
予約時刻の割り当て・row_id 採番・書込は従来どおり最後に1回だけ行う
（run() を複数回呼ぶ形だと row_id が振り直されて全タブ一意が壊れ、在庫アンカーも効かない）。
2回目以降のプロンプトには作成済み本文を渡して重複生成を防ぐ。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: ドキュメントを実態に合わせる

**Files:**
- Modify: `CLAUDE.md`（§1 のワークフロー説明・§2 のサイクル説明・§6 の Variable 表）
- Modify: `docs/CHANGELOG.md`（末尾に §30 を追記）

**Interfaces:**
- Consumes: Task 1〜4 の実装結果
- Produces: なし（ドキュメントのみ）

**注意:** `CLAUDE.md` は本計画の作業開始時点で未コミットの変更（§8 の Supabase 判断）を含んでいる可能性がある。**その差分は消さずに残す**こと。`git diff CLAUDE.md` で確認してから編集する。

---

- [ ] **Step 1: `CLAUDE.md` §1 のワークフロー説明を直す**

`weekly.yml  日次06:00 → main_weekly.py  3日サイクルゲート→分析→レポート→生成→メール`

を:

```
  weekly.yml  日次06:00 → main_weekly.py  曜日ゲート→分析→生成(日・水)→レポート/メール(日)
```

- [ ] **Step 2: `CLAUDE.md` §2 のサイクル説明を直す**

`- **3日PDCAサイクル**: weekly.yml は毎日叩くが `is_cycle_day`（起点 2026-06-28・3日周期）の日だけ本処理。各サイクルで「翌日から3日×4本/日」を生成→隙間なく連続。手動実行は `FORCE_CYCLE=1`。`

を:

```
- **PDCAサイクル（曜日固定・2026-09-06〜）**: weekly.yml は毎日叩くが、**生成は日曜・水曜（週2回）**、
  **レポート＋メールは日曜（週1回）**だけ実行する。生成本数は「次のサイクル日までの日数 × 4本」で
  自動計算（日曜=3日分12本／水曜=4日分16本、合計7日で週がちょうど埋まる）。
  AI呼び出しは1回あたり最大8本に分割する（`generator.MAX_POSTS_PER_CALL`）。
  手動実行は `FORCE_CYCLE=1`（生成・レポートの両方を強制）。
```

- [ ] **Step 3: `CLAUDE.md` §6 の Variable 表を直す**

`| `GEN_POSTS_<NAME>` | Variable | 事業別の生成本数（既定12=3日×4本）。**0でその事業だけ生成オフ**。現在 SEIZOGYO2/SEIZOGYO3/MEGURI=0 |`

を:

```
| `GEN_POSTS_<NAME>` | Variable | **0でその事業だけ生成オフ**。用途はこれだけ。1以上の本数指定は後方互換で残るが**設定してはいけない**（曜日で必要本数が3日分/4日分と変わるため、固定値だと在庫に穴が空く）。**2026-09-06に SEIZOGYO2/SEIZOGYO3/MEGURI の `12` を削除済み**＝全事業が動的計算 |
```

- [ ] **Step 4: `docs/CHANGELOG.md` に §30 を追記する**

ファイル末尾に追記:

```markdown
## §30 PDCAサイクルの曜日固定（2026-09-06）

### 背景
サイクル判定が「起点日(2026-06-28)からの経過日数 % 3」だったため、サイクル日が毎週ずれ、
週次レポートの届く曜日も定まらなかった。また3日間隔は「投稿後48時間未満は当たり判定から除外」
というPDCAの前提と噛み合わず、前サイクルの結果が固まる前に次を生成していた。

### 変更
- 生成ゲートとレポートゲートを分離。`is_cycle_day`（日・水の週2回）と
  `is_report_day`（日の週1回）＋ `decide_gates()` を新設。`CYCLE_ANCHOR` / `CYCLE_DAYS` は削除
- 生成本数を `days_until_next_cycle(today) * POSTS_PER_DAY` で動的計算（日=12本／水=16本）。
  合計7日で週がちょうど埋まり、在庫に穴も重複も出ない
- Variable `GEN_POSTS_<NAME>` の用途を「0=生成オフ」に限定。空/未設定は動的計算へフォールバック
  （空文字を `int()` して落ちないようにした）。**運用側で SEIZOGYO2/SEIZOGYO3/MEGURI の `12` を削除済み**
- AI呼び出しを最大8本ずつに分割（`generator.MAX_POSTS_PER_CALL`）。2回目以降のプロンプトには
  作成済み本文を渡して重複生成を防ぐ
- レポート専用日は `sort_posts_tab` を撃たない（行を追加していないため）

### 分割生成で守った不変条件
分割するのは**AI呼び出しだけ**。コンプラゲート・予約時刻の割り当て・`row_id` 採番・書込は
最後に1回だけ行う。`run()` を複数回呼ぶ形にすると、(1) `row_id` が `-01` から振り直されて
「全タブ一意」（§16）が壊れ二重投稿の原因になり、(2) 2回目の在庫アンカー（§29追補2）が
1回目で書いた行を見られず同じ日付に二重予約する。

### 関連
設計 = docs/superpowers/specs/2026-09-06-cross-account-analytics-design.md §8
計画 = docs/superpowers/plans/2026-09-06-cycle-weekday-fixing.md
```

- [ ] **Step 5: 秘密情報が混入していないことを確認**

Run:
```bash
git diff --cached CLAUDE.md docs/CHANGELOG.md | grep -nEi 'token|apikey|api_key|spreadsheets/d/|@gmail|[A-Za-z0-9_-]{40,}' || echo "OK: 秘密情報なし"
```
Expected: `OK: 秘密情報なし`

- [ ] **Step 6: 全テストを最終確認**

Run: `python3 -m pytest tests/ -q`
Expected: PASS（124 passed）

- [ ] **Step 7: コミット**

```bash
git add CLAUDE.md docs/CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs: サイクル曜日固定を CLAUDE.md と CHANGELOG に反映

§1/§2 のサイクル説明を曜日ベースに更新し、§6 の GEN_POSTS_<NAME> を
「0=生成オフ専用」に書き換える（Variable の 12 は削除済み）。
CHANGELOG に §30 として背景・変更・分割生成で守った不変条件を記録する。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 完了条件

- [ ] `python3 -m pytest tests/ -q` が 124 passed
- [ ] `grep -rn "CYCLE_ANCHOR\|CYCLE_DAYS" --include=*.py .` が空
- [ ] `python3 -c "from main_weekly import decide_gates; from datetime import date; print(decide_gates(date(2026,9,6), {}), decide_gates(date(2026,9,9), {}))"` が `(True, True) (True, False)`
- [ ] `CLAUDE.md` の §8（Supabase判断）の既存差分が消えていない

## 実装後の運用確認（人が行う・コード変更なし）

1. `weekly.yml` を **`FORCE_CYCLE=1` で手動実行**し、ログに「生成=する / レポート=する」と
   生成対象日数が出ることを確認する
2. 生成された行の `投稿日時` が既存在庫の最終日の翌日から**連続して**並び、
   日付の重複が無いことをシートで確認する
3. 次の水曜（レポートなし・生成のみ）の run で、レポートメールが**届かない**ことを確認する
4. 次の日曜の run で、レポートメールが**届く**ことを確認する
5. `monitor.yml` の在庫警告が出ていないことを確認する（穴が空いていない証拠）
