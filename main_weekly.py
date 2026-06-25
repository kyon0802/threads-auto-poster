"""週次の分析→レポート→（任意で）翌週コンテンツ生成のエントリ（weekly.yml から実行）。

各事業シートの各アカウントについて:
  1) Analyzer  … インサイト分析_<acc> を更新
  2) Reporter  … 週次レポート タブへ追記
  3) Generator … GENERATE_POSTS=1 のときのみ。Claude で翌週案を生成→機械コンプラゲート→
                 投稿_<acc> へ status=draft 投入（人が queued に変えるまで自動公開されない）

環境変数:
  GOOGLE_SERVICE_ACCOUNT_JSON / BUSINESSES または SPREADSHEET_ID（投稿系と共通ルーティング）
  GENERATE_POSTS=1            … 生成を有効化（既定オフ＝分析とレポートのみ）
  GEN_POSTS_PER_ACCOUNT       … 1アカの生成本数（既定5）
  GEN_MODEL                   … 生成モデル（既定 claude-opus-4-8）
  ANTHROPIC_API_KEY           … 生成有効時に必須（generator が読む）
  TZ_NAME                     … 既定 Asia/Tokyo
"""
import os
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from threads_poster.sheets import GoogleSheetStore
from threads_poster.analyzer import Analyzer
from threads_poster.reporter import Reporter
from threads_poster.generator import Generator, GeneratorError
from threads_poster.html_report import build_html, build_fragment, wrap_document
from threads_poster.schedule import build_schedule
from threads_poster.strategy import generate_strategy
from main import resolve_business_sheets


def enrich_tops_with_text(posts: list, account: str, analysis: dict) -> None:
    """ランキング(top / top_er)に投稿後ID経由で**実際の本文**を結合する（TOP5を全文表示するため）。
    posts は store.get_posts() の結果（事業ごとに1回読んで使い回す＝Sheets読込を増やさない）。"""
    pid2text = {}
    for p in posts:
        if str(p.get("account")) != account:
            continue
        pid = str(p.get("posted_id") or "")
        if pid:
            pid2text[pid] = p.get("text") or ""
    for key in ("top", "top_er"):
        for r in analysis.get(key, []):
            r["text"] = pid2text.get(str(r.get("posted_id") or ""), "")

# 事業ごとの予約時刻スケジュール戦略。
# seizogyo（製造業 takumi_kojo_navi）＝1日4本（昼1＋夜18-23時に3本・最低間隔30分・ランダム配置）。
# それ以外（占い等）は None＝従来どおり（generator が翌日から1日1本・21時固定で割り当て）。
SCHEDULE_FN_BY_BUSINESS = {"seizogyo": build_schedule}


def n_posts_for(name: str, env, default_n: int) -> int:
    """事業ごとの1アカ生成本数。seizogyo は「1日4本×7日＝28本」を既定（毎日4投稿を1週間フルカバー）。
    Variable GEN_POSTS_SEIZOGYO で上書き可。その他事業は GEN_POSTS_PER_ACCOUNT（既定5）。
    ※本数を事業ごとに分けるのは、占い等は従来どおり1日1本のため28本にすると28日先まで並んでしまうのを防ぐため。"""
    if name == "seizogyo":
        return int(env.get("GEN_POSTS_SEIZOGYO", "28"))
    return default_n

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main_weekly")


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    tz_name = os.environ.get("TZ_NAME", "Asia/Tokyo")
    generate = os.environ.get("GENERATE_POSTS") == "1"
    n_posts = int(os.environ.get("GEN_POSTS_PER_ACCOUNT", "5"))
    gen_model = os.environ.get("GEN_MODEL", "claude-opus-4-8")
    gen_status = os.environ.get("GEN_STATUS", "draft")  # draft=人が確認 / queued=全自動公開
    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    sheets = resolve_business_sheets(os.environ)
    if not sa_json or not sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON と (BUSINESSES または SPREADSHEET_ID) が必要です")
        return 1
    # キルスイッチ: PAUSED=1 なら生成を止める（分析・レポートは無害なので継続）
    if os.environ.get("PAUSED") == "1" and generate:
        log.info("PAUSED=1：一時停止中のため生成は行いません（分析・レポートのみ）")
        generate = False

    sa_info = json.loads(sa_json)
    gen_date = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    os.makedirs(reports_dir, exist_ok=True)
    THEME = {"seizogyo": "seizo", "uranai": "uranai"}
    totals = {"analyzed": 0, "reported": 0, "generated_drafts": 0}
    failures = 0
    fragments = []  # メール本文に入れる各アカのレポート本体

    for name, sid in sheets:
        log.info("=== 事業 '%s' の週次処理 (sheet=%s…) ===", name, str(sid)[:10])
        try:
            store = GoogleSheetStore(sa_info, sid)
            accounts = [a["account"] for a in store.get_accounts() if a.get("account")]
            posts_all = store.get_posts()  # 事業で1回だけ読む（TOP5本文結合に使い回す）
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.exception("事業 '%s' の初期化に失敗: %s", name, e)
            continue

        theme = THEME.get(name, "seizo")
        for acc in accounts:
            try:
                analysis = Analyzer(store).run(acc)
                totals["analyzed"] += 1
                enrich_tops_with_text(posts_all, acc, analysis)  # TOP5に実際の本文を結合
                Reporter(store).run(acc, analysis, gen_date)
                totals["reported"] += 1
                # 来週の方針＋投稿例（AI生成）。generate=False(PAUSED や GENERATE_POSTS=0)なら
                # 課金を避けるため呼ばず None＝方針セクションなしでレポートは出す。
                strategy = generate_strategy(store, acc, analysis, model=gen_model) if generate else None
                # HTMLレポート（reports/に保存＝weekly.yml が添付）＋メール本文用 fragment
                with open(os.path.join(reports_dir, f"週次レポート_{acc}_{gen_date}.html"), "w", encoding="utf-8") as f:
                    f.write(build_html(acc, analysis, gen_date, theme=theme, title=acc, strategy=strategy))
                fragments.append(build_fragment(acc, analysis, gen_date, theme=theme, title=acc, strategy=strategy))
                if generate:
                    schedule_fn = SCHEDULE_FN_BY_BUSINESS.get(name)
                    acc_n_posts = n_posts_for(name, os.environ, n_posts)
                    res = Generator(store, acc, n_posts=acc_n_posts, model=gen_model,
                                    status=gen_status, schedule_fn=schedule_fn).run(analysis)
                    totals["generated_drafts"] += len(res["written"])
                    log.info("%s: %s %d本投入 / 破棄 %d本", acc, gen_status, len(res["written"]), len(res["rejected"]))
            except GeneratorError as e:  # 必須タブ未整備（§17e）→ そのアカだけ失敗扱い
                failures += 1
                log.error("%s: 生成中止（プロフィール/ガイドライン未整備）: %s", acc, e)
            except Exception as e:  # noqa: BLE001
                failures += 1
                log.exception("%s の週次処理に失敗: %s", acc, e)

    # メール本文＝各アカの視覚レポートをそのまま1通に（メールで開いてすぐ読める）。
    email_html = wrap_document("Threads 週次レポート", "".join(fragments) or "<p>対象なし</p>")
    with open(os.path.join(reports_dir, "メール本文.html"), "w", encoding="utf-8") as f:
        f.write(email_html)

    log.info("完了: %s / 失敗=%d / 生成=%s", totals, failures, "ON" if generate else "OFF")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
