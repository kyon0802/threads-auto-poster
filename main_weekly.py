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
from threads_poster.html_report import build_html
from main import resolve_business_sheets

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
    summaries = []

    for name, sid in sheets:
        log.info("=== 事業 '%s' の週次処理 (sheet=%s…) ===", name, str(sid)[:10])
        try:
            store = GoogleSheetStore(sa_info, sid)
            accounts = [a["account"] for a in store.get_accounts() if a.get("account")]
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.exception("事業 '%s' の初期化に失敗: %s", name, e)
            continue

        theme = THEME.get(name, "seizo")
        for acc in accounts:
            try:
                analysis = Analyzer(store).run(acc)
                totals["analyzed"] += 1
                Reporter(store).run(acc, analysis, gen_date)
                totals["reported"] += 1
                # HTMLダッシュボードを reports/ に出力（weekly.yml がメールで送る）
                with open(os.path.join(reports_dir, f"週次レポート_{acc}_{gen_date}.html"), "w") as f:
                    f.write(build_html(acc, analysis, gen_date, theme=theme, title=acc))
                gen_titles = []
                if generate:
                    res = Generator(store, acc, n_posts=n_posts, model=gen_model,
                                    status=gen_status).run(analysis)
                    totals["generated_drafts"] += len(res["written"])
                    gen_titles = [t.splitlines()[0][:38] for t in res["kept"]]
                    log.info("%s: %s %d本投入 / 破棄 %d本", acc, gen_status, len(res["written"]), len(res["rejected"]))
                summaries.append((acc, analysis["n_posts"], gen_status, gen_titles))
            except GeneratorError as e:  # 必須タブ未整備（§17e）→ そのアカだけ失敗扱い
                failures += 1
                log.error("%s: 生成中止（プロフィール/ガイドライン未整備）: %s", acc, e)
            except Exception as e:  # noqa: BLE001
                failures += 1
                log.exception("%s の週次処理に失敗: %s", acc, e)

    # 公開前通知メールの本文（各アカの実績サマリ＋今週投稿する内容）
    import html as _h
    parts = ["<h2>Threads 週次レポート</h2>",
             f"<p>生成日 {gen_date} ／ モード: {'queued=そのまま自動公開' if gen_status == 'queued' else 'draft=要確認'}</p>"]
    for acc, npost, st, titles in summaries:
        parts.append(f"<h3>{_h.escape(acc)}</h3><p>分析 {npost} 投稿。</p>")
        if titles:
            label = "今週そのまま自動公開する投稿" if st == "queued" else "今週の下書き（要確認）"
            parts.append(f"<p><b>{label}</b>:</p><ul>"
                         + "".join(f"<li>{_h.escape(t)}…</li>" for t in titles) + "</ul>")
    parts.append("<p>※ 視覚的なダッシュボードは添付HTMLを開いてください。"
                 "止めるには投稿キューの該当行を削除、または Variable PAUSED=1。</p>")
    with open(os.path.join(reports_dir, "メール本文.html"), "w") as f:
        f.write("\n".join(parts))

    log.info("完了: %s / 失敗=%d / 生成=%s", totals, failures, "ON" if generate else "OFF")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
