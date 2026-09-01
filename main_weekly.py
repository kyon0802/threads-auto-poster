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
from datetime import datetime, date
from functools import partial
from zoneinfo import ZoneInfo

from threads_poster.sheets import GoogleSheetStore
from threads_poster.analyzer import Analyzer, follower_trend
from threads_poster.errors import classify_generation_error
from threads_poster.inventory import compute_runway, runway_message
from threads_poster.reporter import Reporter
from threads_poster.generator import Generator, GeneratorError
from threads_poster.html_report import build_html
from threads_poster.schedule import build_schedule, PRESETS
from threads_poster.strategy import generate_strategy
from threads_poster.mailer import send_html
from main import resolve_business_sheets

# 事業名 → メール件名に出す日本語ラベル。
# 未登録だと件名に内部キー（seizogyo2 等）がそのまま出る＝社外の配信先がいるので必ず入れる。
BIZ_LABEL = {
    "seizogyo": "製造業",
    "uranai": "占い（結）",
    "meguri": "占い（澪）",
    "seizogyo2": "製造業（住田）",
    "seizogyo3": "製造業（ぱし）",
}


def read_account_metrics(store, account: str) -> list[dict]:
    """アカウント指標（フォロワー数の日次スナップショット）。未対応のStoreでも落ちない。"""
    getter = getattr(store, "get_account_metrics", None)
    if getter is None:
        return []
    try:
        return getter(account)
    except Exception:  # noqa: BLE001 指標が取れなくてもレポートは出す
        logging.getLogger("main_weekly").warning("%s: アカウント指標を読めませんでした", account)
        return []

# ── 3日サイクル（PDCA）設定 ──────────────────────────────────────────────
# 「3日分の投稿を作成→3日分を分析してレポート」を3日ごとに繰り返す。
# cron は毎日叩くが、ここで「起点日からの経過日数 % 3 == 0」の日だけ本処理を実行する
# （day-of-month の */3 は月末→月初で間隔が崩れるため、起点日アンカー方式にする）。
# 起点 = 2026-06-28（初回サイクル 06-26夕〜06-28 を手動投入した直後。以降 06-28/07-01/07-04…で稼働）。
# 各サイクルで generator は「翌日から CYCLE_DAYS 日 × 4本」を生成するので、
#   06-28実行→06-29〜07-01、07-01実行→07-02〜07-04… と隙間なく連続する。
CYCLE_ANCHOR = date(2026, 6, 28)
CYCLE_DAYS = 3
POSTS_PER_DAY = 4


def is_cycle_day(today: date) -> bool:
    return (today - CYCLE_ANCHOR).days % CYCLE_DAYS == 0


def send_account_reports(reports: list[dict], *, user: str, password: str, to: str,
                         gen_date: str = "", send_fn=None) -> tuple[int, int]:
    """アカウントごとに1通ずつ個別メールを送る。(送信成功数, 失敗数) を返す。
    reports=[{account,label,html,filename}]。send_fn は注入可（テスト用）。"""
    send_fn = send_fn or send_html
    log = logging.getLogger("main_weekly")
    sender = f"Threads週次レポート <{user}>"
    sent = failed = 0
    for rep in reports:
        # 宛先はレポート個別の to（事業別ルーティング）があればそれ、無ければ共通の to。
        rcpt = rep.get("to") or to
        # 在庫ゼロ・生成失敗のときは件名で分かるようにする（正常なレポートに埋もれさせない）。
        prefix = "【要確認】" if rep.get("alert") else ""
        subject = (f"{prefix}【Threads週次】{rep['label']}｜{rep['account']}"
                   + (f"（{gen_date}）" if gen_date else ""))
        try:
            send_fn(user, password, sender, rcpt, subject, rep["html"], attachment_name=rep.get("filename"))
            sent += 1
            log.info("メール送信OK: %s → %s", rep.get("account"), rcpt)
        except Exception:  # noqa: BLE001 1通の失敗で他アカの送信は止めない
            failed += 1
            log.exception("メール送信失敗: %s", rep.get("account"))
    return sent, failed


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
    for key in ("top", "top_er", "trend_top", "trend_bottom"):
        for r in analysis.get(key, []):
            r["text"] = pid2text.get(str(r.get("posted_id") or ""), "")

# 事業ごとの予約時刻スケジュール戦略（1日4本・ランダム配置・最低間隔30分）。
# seizogyo（製造業）＝昼12時前後1本＋夜18-23時に3本。uranai（占い）＝午前1本＋夕方-深夜3本。
# build_schedule に事業別プリセット（時間帯）を partial で固定。これを持たない事業は None＝
# 従来どおり generator が翌日から1日1本・21時固定で割り当てる。
SCHEDULE_FN_BY_BUSINESS = {
    "seizogyo": partial(build_schedule, **PRESETS["seizogyo"]),
    "uranai": partial(build_schedule, **PRESETS["uranai"]),
    "meguri": partial(build_schedule, **PRESETS["meguri"]),  # 占い「澪」＝朝昼夕夜の4窓
    "seizogyo2": partial(build_schedule, **PRESETS["seizogyo2"]),  # 製造業・住田(共感認知型)＝朝夜寄り4窓
    "seizogyo3": partial(build_schedule, **PRESETS["seizogyo3"]),  # 製造業・ぱし(本音暴露型)＝昼夕寄り4窓（住田と別窓）
}


def n_posts_for(name: str, env, default_n: int) -> int:
    """事業ごとの1アカ生成本数。4本/日スケジュール対象（seizogyo/uranai）は
    「CYCLE_DAYS 日 × 4本」＝1サイクル分（既定 3日×4＝12本）。Variable GEN_POSTS_<NAME> で上書き可
    （例 GEN_POSTS_SEIZOGYO / GEN_POSTS_URANAI）。その他事業は GEN_POSTS_PER_ACCOUNT（既定5）。"""
    if name in SCHEDULE_FN_BY_BUSINESS:
        return int(env.get(f"GEN_POSTS_{name.upper()}", str(CYCLE_DAYS * POSTS_PER_DAY)))
    return default_n


def gen_status_for(name: str, env, default_status: str) -> str:
    """事業ごとの生成後ステータス（draft=人が確認 / queued=自動公開）。

    Variable `GEN_STATUS_<NAME>` があればその事業だけ差し替え、無ければ共通の `GEN_STATUS`。
    製造業は過去2回BAN済みで、占いと同じ「無確認自動公開」しか選べないのがリスクだったため
    事業別に分離した（例：GEN_STATUS=queued かつ GEN_STATUS_SEIZOGYO=draft）。
    """
    return env.get(f"GEN_STATUS_{name.upper()}", "").strip() or default_status


def recipients_for(name: str, env, default_to: str) -> str:
    """事業ごとの週次レポート宛先。Variable `MAIL_TO_<NAME>`（カンマ区切りで複数可）が
    あればそれを使い、無ければ default_to（`MAIL_TO`＝全事業共通の既定宛先）。
    例：MAIL_TO=morll（全事業）＋ MAIL_TO_SEIZOGYO2="morll, toshi" で seizogyo2 だけ toshi にも配る。
    宛先は mailer 側でカンマ分解して全員に配送される。"""
    return env.get(f"MAIL_TO_{name.upper()}", "").strip() or default_to

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
    # 3日サイクルゲート: cron は毎日叩くが、起点日から3日ごとの日だけ本処理（分析→レポート→生成→メール）
    # を実行する。それ以外の日は即終了（次サイクルまで待機）。FORCE_CYCLE=1 で手動実行時はバイパス。
    today = datetime.now(ZoneInfo(tz_name)).date()
    if os.environ.get("FORCE_CYCLE") != "1" and not is_cycle_day(today):
        log.info("3日サイクル外（起点=%s / 本日=%s / 周期=%d日）→ 本日は実行しません（次サイクルまで待機）",
                 CYCLE_ANCHOR, today, CYCLE_DAYS)
        return 0
    # キルスイッチ: PAUSED=1 なら生成を止める（分析・レポートは無害なので継続）
    if os.environ.get("PAUSED") == "1" and generate:
        log.info("PAUSED=1：一時停止中のため生成は行いません（分析・レポートのみ）")
        generate = False

    # メール送信する事業（空＝全事業＝運用中の全アカウントへ個別送信）。
    # 限定したいときだけ Variable EMAIL_BUSINESSES="seizogyo" 等を設定。
    email_businesses = set(b.strip() for b in os.environ.get("EMAIL_BUSINESSES", "").split(",") if b.strip())

    sa_info = json.loads(sa_json)
    # 期間窓・在庫ランウェイの基準時刻。シートの日時はJSTのnaive文字列なので合わせる。
    now_local = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    gen_date = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    os.makedirs(reports_dir, exist_ok=True)
    THEME = {"seizogyo": "seizo", "uranai": "uranai", "meguri": "uranai"}
    totals = {"analyzed": 0, "reported": 0, "generated_drafts": 0}
    failures = 0
    email_reports = []  # アカウントごとに個別送信するレポート [{account,label,html,filename}]

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
        # メール対象の事業か（EMAIL_BUSINESSES 空＝全事業＝運用中の全アカウントに個別送信）。
        in_email = (not email_businesses) or (name in email_businesses)
        for acc in accounts:
            try:
                analysis = Analyzer(store, now_fn=lambda: now_local).run(acc)
                # ランキングに本文を結合（★生成より前・全事業で実施。勝ち/負けの実物を
                # 生成プロンプトへ届かせる＝PDCA閉ループの結線・2026-09-01設計）
                enrich_tops_with_text(posts_all, acc, analysis)
                totals["analyzed"] += 1
                Reporter(store).run(acc, analysis, gen_date)
                totals["reported"] += 1

                # ── 生成（★レポートより先に実行する）─────────────────────────────
                # 以前はレポートHTMLを組んだ後に生成していたため、生成の成否をレポートに
                # 載せられなかった。2026-07の障害（残高不足で4サイクル連続失敗）が
                # レポートから読み取れなかった原因なので、先に走らせて結果を持ち回る。
                # 生成の例外はここで受け止め、レポート・メールは必ず最後まで出す。
                gen_info = None
                if not generate:
                    gen_info = {"ok": None, "reason": "生成オフ（GENERATE_POSTS=0 または PAUSED=1）"}
                else:
                    acc_n_posts = n_posts_for(name, os.environ, n_posts)
                    # GEN_POSTS_<NAME>=0 ＝ その事業だけ生成オフ（立ち上げ期の手動運用と自動生成の
                    # 二重投稿を防ぐ per-business スイッチ。分析・レポートは通常どおり実施）。
                    if acc_n_posts <= 0:
                        gen_info = {"ok": None, "reason": f"GEN_POSTS_{name.upper()}=0（生成オフ）"}
                        log.info("%s: GEN_POSTS_%s=0 → 生成スキップ（分析・レポートは実施）",
                                 acc, name.upper())
                    else:
                        schedule_fn = SCHEDULE_FN_BY_BUSINESS.get(name)
                        acc_status = gen_status_for(name, os.environ, gen_status)
                        try:
                            res = Generator(store, acc, n_posts=acc_n_posts, model=gen_model,
                                            status=acc_status, schedule_fn=schedule_fn).run(analysis)
                            totals["generated_drafts"] += len(res["written"])
                            gen_info = {"ok": True, "written": len(res["written"]),
                                        "status": f"{acc_status}で投入（破棄{len(res['rejected'])}本）"}
                            log.info("%s: %s %d本投入 / 破棄 %d本", acc, acc_status,
                                     len(res["written"]), len(res["rejected"]))
                        except GeneratorError as e:  # 必須タブ未整備（§17e）
                            failures += 1
                            gen_info = {"ok": False, "reason": "必須タブ未整備", "detail": str(e)}
                            log.error("%s: 生成中止（プロフィール/ガイドライン未整備）: %s", acc, e)
                        except Exception as e:  # noqa: BLE001 残高不足/認証/レート等
                            failures += 1
                            reason = classify_generation_error(e)
                            gen_info = {"ok": False, "reason": reason, "detail": str(e)}
                            log.error("%s: 生成失敗（%s）: %s", acc, reason, e)

                # ── レポート成果物（本文結合・方針生成・HTML）はメール対象の事業だけ ──
                # （対象外の事業は分析・レポートタブ更新・投稿生成のみ＝無駄なAI課金/レンダリングを避ける）。
                if in_email:
                    followers = follower_trend(read_account_metrics(store, acc), now=now_local)
                    runway = compute_runway(posts_all, acc, now=now_local,
                                            posts_per_day=POSTS_PER_DAY)
                    log.info("%s: %s", acc, runway_message(runway))
                    # 来週の方針＋投稿例（AI生成）。generate=False(PAUSED や GENERATE_POSTS=0)なら
                    # 課金を避けるため呼ばず None＝方針セクションなしでレポートは出す。
                    strategy, strategy_err = None, None
                    if generate:
                        errs = []
                        strategy = generate_strategy(store, acc, analysis, model=gen_model,
                                                     on_error=errs.append)
                        strategy_err = errs[0] if errs else None
                    fname = f"週次レポート_{acc}_{gen_date}.html"
                    html = build_html(acc, analysis, gen_date, theme=theme, title=acc,
                                      strategy=strategy, followers=followers, runway=runway,
                                      gen_info=gen_info, strategy_error=strategy_err)
                    with open(os.path.join(reports_dir, fname), "w", encoding="utf-8") as f:
                        f.write(html)
                    # アカウントごとに1通ずつ送るため、ここで個別に貯める（business＝宛先ルーティング用）
                    # alert＝件名に【要確認】を付ける条件（在庫ゼロ or 生成失敗）。
                    alert = (gen_info or {}).get("ok") is False or runway["pending"] == 0
                    email_reports.append({"account": acc, "label": BIZ_LABEL.get(name, name),
                                          "business": name, "html": html, "filename": fname,
                                          "alert": alert})
                # 投稿タブを投稿日時の降順に整える（新しい日付が上）。生成で追記した行も上に来る。
                store.sort_posts_tab(acc, descending=True)
            except Exception as e:  # noqa: BLE001 分析/レポート/シート書込の失敗
                failures += 1
                log.exception("%s の週次処理に失敗: %s", acc, e)

    # アカウントごとに1通ずつ個別メール送信（ENABLE_EMAIL=1 ＋ MAIL_USERNAME/MAIL_PASSWORD 必須）。
    enable_email = os.environ.get("ENABLE_EMAIL") == "1"
    mail_user = os.environ.get("MAIL_USERNAME")
    mail_pw = os.environ.get("MAIL_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or mail_user
    # 事業別の宛先ルーティング：既定(mail_to)＝全事業。MAIL_TO_<NAME> があればその事業だけ差し替え。
    for rep in email_reports:
        rep["to"] = recipients_for(rep["business"], os.environ, mail_to)
    if enable_email and email_reports:
        if mail_user and mail_pw:
            sent, mail_failed = send_account_reports(
                email_reports, user=mail_user, password=mail_pw, to=mail_to, gen_date=gen_date)
            failures += mail_failed
            log.info("メール送信: %d通成功 / %d通失敗 (既定宛先 %s・事業別はMAIL_TO_<NAME>)", sent, mail_failed, mail_to)
        else:
            # ENABLE_EMAIL=1 なのに認証情報が無い＝設定ミス。静かに緑にせず失敗扱いで気づけるようにする。
            failures += 1
            log.error("ENABLE_EMAIL=1 だが MAIL_USERNAME/MAIL_PASSWORD 未設定のため送信できません（設定を確認）")
    elif enable_email and not email_reports:
        log.info("ENABLE_EMAIL=1 だが送信対象のレポートが0件でした（EMAIL_BUSINESSES/対象アカウントを確認）")

    log.info("完了: %s / 失敗=%d / 生成=%s / メール対象=%d件",
             totals, failures, "ON" if generate else "OFF", len(email_reports))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
