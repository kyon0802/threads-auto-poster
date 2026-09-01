# CLAUDE.md — Threads自動投稿システム 引き継ぎ

このファイルはClaude Codeが起動時に自動参照する。**作業を再開する前に必ず全文を読むこと。**

> ⚠️ **最重要・全状況で遵守**：このrepoは**公開（public）**。何を公開してよく・絶対ダメかは **§17** を参照。ターミナル新規作業・自動化・ユーザーからの分析/作業依頼など**あらゆる場面で§17を識別・認識すること**。

> 📜 **時系列の改修履歴（§11〜§16, §18〜§25）は [docs/CHANGELOG.md](docs/CHANGELOG.md) に移設**（2026-07-03整理）。本ファイルは「現在の仕様・運用」だけを持つ。章番号は移設前と同じ（CHANGELOG内の §参照を壊さないため欠番あり）。

---

## 0. このプロジェクトの目的

スプレッドシートに投稿を並べておくと、**複数のThreadsアカウントへ時間指定で自動投稿**するシステム。
ツリー（リプライ連結）対応。サーバ管理ゼロ（GitHub Actions の無料cron で稼働）。
投稿だけでなく、**インサイト自動収集→分析→週次レポート（メール配信）→AIによる次サイクル投稿の自動生成**までを3日PDCAサイクルで回す。
最終的にはオーナー（Master）のSNS運用代行業の「再利用可能な納品資産」にする。

> ✅ **2026-08-31 復旧済み**。Anthropic のクレジット購入により生成が再開し、
> 4アカウント全てに在庫12本（09-01〜09-03）を投入・`monitor.yml` も1ヶ月ぶりに緑。
> 停止期間＝takumi 43日／住田 53日／ぱし 49日／澪 21日。経緯は docs/CHANGELOG.md §27・§28。
> あわせて **seizogyo2/seizogyo3/meguri の自動生成をON**（`GEN_POSTS_*=12`）にし、4アカ全て自動運用に復帰。
>
> ✅ **09-03の日程重複は解消済み（2026-09-01）**：08-31の `FORCE_CYCLE=1` 復旧実行が生んだ
> 09-03 の二重予約候補（各アカ4本＝計16行 `*-g20260831-09〜12`）を row_id 指名で `retired` 化した。
> 09-02 サイクルは 09-03〜09-05 をクリーンに生成できる。教訓：generator は既存在庫を見ず常に
> 「翌日から3日分」を生成する（`build_schedule` の `not_before` は過去スロット除外専用）ため、
> **`FORCE_CYCLE=1` を使ったら必ず次サイクルとの日程重複を確認すること**。
>
> ✅ **PDCA第1段は稼働中（2026-09-01 マージ＋シート移行済み）**：4シート全てに型ラベル3列＋
> `お手本DB_<acc>`＋`仮説ログ` を追加済み。09-02 サイクルから勝ち/負け本文注入と型ラベル記録が始まる。
> 設計＝docs/superpowers/specs/2026-09-01-pdca-closed-loop-design.md・実装＝CHANGELOG §29。
>
> 状態確認は必ず実測で：生成が動くか → **`preflight.yml` を手動実行**（副作用ゼロ）／
> 投稿在庫があるか → `python3 main_monitor.py`（読取専用）。

これは3層構想の **第1層**。第2層（Threads→LINE導線）、第3層（LINE上でClaude自動鑑定）は後続フェーズ（§9）。

---

## 1. アーキテクチャ

```
スプレッドシート（事業ごとに1枚・投稿キュー＋アカウント/トークン＋インサイト＋ナレッジ）
        ↑ 読む / 結果(status, posted_id, インサイト, 分析, 生成投稿)を書き戻す
GitHub Actions（すべて別systemの6本）
  post.yml    10分おき  → main.py         投稿の公開（Threads API）
  collect.yml 日次04:00 → main_collect.py インサイト収集（読み取り専用）
  weekly.yml  日次06:00 → main_weekly.py  3日サイクルゲート→分析→レポート→生成→メール
  monitor.yml 日次08:00 → main_monitor.py 投稿在庫の監視（読取専用・異常時のみ通知）
  preflight.yml 手動のみ → main_preflight.py 生成AIの疎通/残高チェック（副作用ゼロ）
  tests.yml   push/PR   → pytest tests/   検証専用（秘密不使用）
```

設計判断と理由（変更時はここを尊重すること）:
- **状態は全てスプレッドシートに集約** → GitHub Actions側を完全ステートレスにできる（Actionsのファイルシステムは毎回破棄されるため）。
- **GitHub Actions cron** → VPS不要・無料。`concurrency` で直列化し多重起動による二重投稿を防止。post/collect/weekly/monitor は**別 concurrency グループ**＝互いに邪魔しない。
- **cronはUTC、投稿時刻判定はシート側のJST** で行う（混同しないこと）。
- **多事業ルーティング**：Secret `BUSINESSES`（JSON配列 `[{"name","spreadsheet_id"},…]`）で事業ごとに独立した非公開シートをループ。無ければ `SPREADSHEET_ID` 単体にフォールバック（後方互換）。1事業の失敗で他事業は止めない（run全体は exit 2＝失敗通知）。
- **安全装置を既定内蔵** → 1日上限（公式250より低い既定50）、冪等性（postedは再投稿しない）、write-ahead（公開前に `publishing`）、親未公開なら子は保留、生成は機械コンプラゲート通過分のみ、キルスイッチ `PAUSED=1`。

---

## 2. 現在の運用状態（2026-08-31時点）

- **repo**: `kyon0802/threads-auto-poster`（**公開**・§17厳守）。gh CLI は `/opt/homebrew/bin/gh`（アカウント kyon0802）。
- **稼働事業（Secret `BUSINESSES`）**: 4事業4アカウント（全て自動生成ON・2026-08-31〜）。
  - `seizogyo`（製造業・`takumi_kojo_navi`）
  - `seizogyo2`（製造業・共感認知型 `tenshokuman`＝住田）
  - `seizogyo3`（製造業・本音暴露型 `pashi`＝ぱし。2026-07-08にseizogyo2から分離＝**アカウント別シート**）
- **廃止**: `uranai`（占い「結」・`miko_yui_musubi`）は 2026-07-28 に**廃止**。156投稿で累計227表示（平均1.5）とアカウント側の配信抑制が疑われたため、人格・アカウント名ごと終了。ナレッジのみローカルへ保全（占いThreads-note事業/結_アーカイブ_20260728）。**新しい占いアカウントは未定**。
  - `meguri`（占い「澪」・アカウントキー `mio__meguri`＝**アンダースコア2つ**）。2026-07-28に BUSINESSES へ追加して稼働開始し、ローンチ26本（07-29〜08-10）を公開済み。2026-08-31に自動生成もON。
- **3日PDCAサイクル**: weekly.yml は毎日叩くが `is_cycle_day`（起点 2026-06-28・3日周期）の日だけ本処理。各サイクルで「翌日から3日×4本/日」を生成→隙間なく連続。手動実行は `FORCE_CYCLE=1`。
- **投稿スケジュール**: 事業別プリセット（`schedule.PRESETS`）で1日4本・ランダム配置・最低間隔30分。seizogyo=昼1＋夜3／meguri=朝昼夕夜の4窓／seizogyo2=生活リズム4窓（朝通勤・昼休憩・夕帰宅・夜寝る前）／seizogyo3=昼夕寄り4窓（seizogyo2と窓が重ならないことをテストで機械保証＝CIB配慮）。
- **生成**: `GENERATE_POSTS=1`＋`ANTHROPIC_API_KEY`。`GEN_STATUS`=draft(人が確認)/queued(全自動公開)。**事業別に `GEN_STATUS_<NAME>` で上書き可**（製造業だけdraft等）。生成前に必須タブゲート（§17e）、生成後に機械コンプラゲート。**2026-08-31に seizogyo2/seizogyo3/meguri の `GEN_POSTS_*` を 12 にして全4アカ自動生成ON**（それまでは立ち上げ期の手動運用のため 0＝オフだった）。
- **メール**: `ENABLE_EMAIL=1` でアカウントごとに週次レポートを個別送信（宛先は Variable `MAIL_TO` / `MAIL_TO_<事業名>`・認証は Gmail アプリパスワード。実アドレスは公開repoに書かない＝§17b）。run失敗時はGitHub純正の失敗通知メールも飛ぶ。
- **在庫監視**: `monitor.yml`（日次 08:00 JST・読取専用）が各アカの未来在庫と残り日数を算出し、在庫ゼロ/残りわずかのときだけ【要確認】メールを送る。在庫ゼロの間は run を exit 2 で赤くする。**投稿ジョブは在庫ゼロでも成功で終わるため、停止を検知できる唯一の仕組み**（§10・docs/CHANGELOG.md §27）。
- **テスト**: `python3 -m pytest tests/ -q`（97本・API不要のモック）。push/PR ごとに tests.yml でも自動実行。
- **過去インシデントの教訓は §10 と docs/CHANGELOG.md（§13/§14/§16/§27）**。特に「row_id 必須・全タブ一意」は絶対。

---

## 3. ★Threads API 正確仕様（2026-06時点で公式・検証済み。ここを推測で書き換えないこと）

- ベースURL: `https://graph.threads.net` / バージョン `v1.0`
- **投稿は2ステップ**:
  1. コンテナ作成: `POST /v1.0/{user-id}/threads`
     params: `media_type`(TEXT/IMAGE/VIDEO/CAROUSEL), `text`, `image_url`, `video_url`, `reply_to_id`, `reply_control`(everyone/accounts_you_follow/mentioned_only), `access_token`
  2. 公開: `POST /v1.0/{user-id}/threads_publish?creation_id={container-id}&access_token=...`
- **メディアは公開前に処理完了を待つ**: `GET /v1.0/{container-id}?fields=status` → `FINISHED`/`IN_PROGRESS`/`ERROR`/`EXPIRED`。FINISHEDになってから publish。
- **ツリー**: ネイティブの「スレッド」オブジェクトは無い。前の投稿の公開後IDを次の `reply_to_id` に渡して**逐次**作る。1リクエストでまとめて作るバッチは無い。
- **トークン**:
  - 短期(1h): `POST /oauth/access_token`(grant_type=authorization_code)
  - 長期(60日)へ交換: `GET /access_token?grant_type=th_exchange_token&client_secret=...&access_token=...`
  - リフレッシュ: `GET /refresh_access_token?grant_type=th_refresh_token&access_token=...`（**24h以上経過かつ未失効**が条件）
- **読み取り（インサイト）**: `GET /{user-id}/threads`（投稿一覧・カーソルページング）／`GET /{media-id}/insights`（views,likes,replies,reposts,quotes,shares）／`GET /{user-id}/threads_insights`（要 `threads_manage_insights` スコープ）。`data[]` の並び順は非保証＝name キーで参照。
- **制限**: 公開上限 250/24h/ユーザー（アカウント単位、アプリ単位ではない）。
- **その他**: ネイティブ予約機能なし（自前cron必須）／投稿の編集不可／**投稿の削除もこのアプリ権限では不可**（code 10）／メディアは公開到達可能なURL必須（直アップ不可）／自分所有アカウントは「開発モード」（Threadsテスター追加）で App Review なしに実投稿可。第三者運用/販売（Phase E）のみ App Review 必須。

---

## 4. ファイル構成

```
main.py                       投稿エントリ（post.yml から10分おき）
main_collect.py               インサイト収集エントリ（collect.yml から日次・読み取り専用）
main_weekly.py                週次エントリ（weekly.yml から日次→3日サイクルゲート）
main_monitor.py               在庫監視エントリ（monitor.yml から日次・読取専用）
main_preflight.py             生成AIの事前疎通チェック（preflight.yml から手動・副作用ゼロ）
                              max_tokens=1 を1回投げ「残高不足/認証エラー/…」に分類する。
                              本番の週次を撃たずに「今動くか」を確かめる唯一の手段
bootstrap_token.py            初回の短期→長期トークン交換ヘルパー
threads_poster/
  threads_api.py              ThreadsClient（container/publish/insights/トークン。状態を持たない）
                              ★HTTPは http_request() が唯一の入口。例外のトークンをマスクする（§17b）
  sheets.py                   Store抽象 + GoogleSheetStore(本番・batch書込・with_retry) + MemoryStore(テスト)
  publisher.py                公開ロジック（時刻判定/ツリー/レート制限/write-ahead/冪等性）
  collector.py                インサイト日次収集（Publisher対称・読み取り専用）
  analyzer.py                 実績集計（純関数・AI不使用）。analyze_windowed=直近7日/前7日/累計、傾向は28日窓
  inventory.py                投稿在庫（ランウェイ）の算出（純関数・週次レポートと在庫監視で共用）
  errors.py                   失敗理由の分類（残高不足/認証/レート/一時障害・純関数）
  reporter.py                 週次レポートのタブ追記＋Markdownミラー（AI不使用）
  generator.py                AI投稿生成（Claude・必須タブゲート＋コンプラゲート・schedule_fn注入）
  strategy.py                 来週方針＋投稿例の生成（レポート用・読み取り専用）
  schedule.py                 1日N本ランダム配置（PRESETS: seizogyo/seizogyo2/seizogyo3/meguri・rng注入）
  compliance.py               機械コンプラゲート（NGワード/URL/文字数・決定的）
  html_report.py              週次レポートHTML（メール対応・インラインCSS）
  mailer.py                   SMTP(SSL)送信（certifi・smtp_factory注入）
scripts/                      ローカルで人が実行するセットアップ/移行/運用ツール
  setup_sheet.py              Phase A: タブ/ヘッダ自動生成＋接続診断
  get_auth_url.py             Phase B: OAuth認可URL生成
  exchange_token.py           Phase B/C: 認可code→長期トークン取得
  setup_account.py            トークン→user_id取得→accounts へ登録
  setup_post_tab.py           アカウント別タブ「投稿_<account>」作成＋記入例タブ
  add_validation_ja.py        投稿タブにドロップダウン/日時形式チェック付与
  migrate_headers_ja.py       既存シート見出しの日本語化（データ保持）
  migrate_to_business_sheet.py 事業分離の移管（旧→新シート verbatimミラー・冪等）
  setup_post_tab.py / check_sheet.py / reschedule_posts.py / fill_week_schedule.py
  batch_to_csv.py             content→sheetブリッジ（立ち上げバッチMd→posts CSV）
  sync_knowledge.py           ローカルナレッジ→ナレッジ_タブ同期
  add_pdca_columns.py         PDCA移行: 投稿タブ3列追加＋お手本DB/仮説ログ作成（冪等・DRY-RUN既定）
  local_run.sh                .env読込→DRY_RUN既定でローカル実行
tests/                        テスト（API不要・モック・97本）。pytest でも直実行でも可
  test_logic.py / test_collect.py / test_phase2.py / test_schedule.py
  test_report_window.py（期間窓・在庫・エラー分類） / test_monitor.py / test_threads_api_masking.py
sheet_templates/              accounts.csv / posts.csv / posts_example.csv（記入例）
.claude/agents/               このrepo専用のサブエージェント定義10体（orchestrator が回し役。
                              api-specialist / system-architect / devops / insights-engineer /
                              data-analyst / content-strategist / compliance-reviewer /
                              code-reviewer / qa-engineer）。公開repoなので事業ノウハウは書かない（§17b）
docs/CHANGELOG.md             時系列の改修履歴（旧CLAUDE.md §11〜§25 ＋ §26以降）
.github/workflows/            post.yml / collect.yml / weekly.yml / monitor.yml /
                              preflight.yml / tests.yml
requirements.txt / .env.example / README.md / SETUP.md
```

各層の責務:
- `threads_api.py` … HTTPとAPI仕様の知識のみ。状態を持たない。
- `sheets.py` … データの読み書き。`Store` インターフェースを実装すれば保管先を差し替え可能（DB化はここ）。全gspread呼び出しは `with_retry`（一過性5xx/429を指数バックオフで吸収）。
- `publisher.py`/`collector.py`/`generator.py` … ビジネスロジック。`client_factory`/`now_fn`/`generate_fn`/`rng` を注入できるのでテスト容易。`dry_run=True` で無書込実行。

---

## 5. スプレッドシート スキーマ（日本語見出し・アカウント別タブ）

> 真実は `threads_poster/sheets.py`（`*_FIELD_ALIASES` / `per_account_post_headers` / タブ定数）。見出しは**日本語(正規)でも英語(旧名)でも読める**（エイリアス層）。タブ名は変えない。見出し行も消さない。

**`accounts` タブ**:
`アカウント`(account) / `ユーザーID`(user_id) / `アクセストークン`(access_token) / `トークン更新日時`(token_updated_at) / `本日投稿数`(daily_count) / `カウント日付`(daily_count_date)

**投稿タブ = アカウントごとに分割**。タブ名 **`投稿_<アカウント名>`**（`<アカウント名>`は accounts の `アカウント` と一致。**アカウントはタブ名から自動判定するので「アカウント」列は持たない**）。見出し:
`投稿ID`(row_id) / `投稿日時`(post_datetime, JST `YYYY-MM-DD HH:MM`・文字列書式) / `本文`(text) / `メディア種類`(media_type, TEXT/IMAGE/VIDEO/CAROUSEL) / `メディアURL`(media_url) / `返信先ID`(reply_to=親の投稿ID) / `返信できる人`(reply_control) / `状態`(status) / `投稿後ID`(posted_id) / `投稿実施日時`(posted_at) / `エラー`(error) / `フック型`(hook_type) / `内容型`(content_type) / `参照お手本ID`(exemplar_ref)

**システムが自動生成/管理するタブ**:
- `インサイト_<acc>` … 投稿別インサイトの日次スナップショット（posted_id×取得日で冪等）
- `アカウント指標_<acc>` … フォロワー数等の日次スナップショット
- `インサイト分析_<acc>` … analyzer の週次集計（毎回全置換）
- `週次レポート` … reporter の追記

**人が curation するタブ（生成の知識源・§17d）**:
- `プロフィール_<acc>`（項目/内容） … 声・トーン・テーマ・お手本・アカ固有NG
- `ガイドライン`（分類/ルール/重大度・事業共通） … 規約/法令/NGワード/過去BAN教訓。「NGワード」分類の行が機械ゲートの禁止語源
- `ナレッジ_<acc>`（A列チャンク） … 事業ナレッジ全文（あれば最優先の知識源）
- `お手本DB_<acc>`（お手本ID/出典/本文/フック型/内容型/ポジション近接度/実測数/状態/退役理由/メモ/収集日） … 勝ちパターンDB。自アカ当たり/滑り＋競合の当たり投稿。生成は `状態=active` のみ参照。設計は docs/superpowers/specs/2026-09-01-pdca-closed-loop-design.md
- `仮説ログ`（日付/仮説/検証方法/結果/次アクション） … サイクルごとのPDCA記録（第1段は人が記入・第2段で自動追記）

運用ルール:
- `状態` は空 or `queued` で投入 → システムが `posted`/`error` を書き戻す。公開直前に `publishing`（write-ahead）。`publishing` のまま残った行は中断痕＝**Threads側を確認してから**空に戻す。`draft`=生成直後（人が queued に変えるまで公開されない）。`retired`=退避（公開対象外）。
- **★投稿ID（row_id）は必ず埋める＋全タブで一意**（冪等性のキー。空・重複は二重投稿の原因＝docs/CHANGELOG.md §16）。アカウント別の接頭辞推奨（例 `miko-001`、生成は `<prefix>-gYYYYMMDD-NN` 自動付与）。
- メディアは `メディアURL` に**公開到達可能な直リンク必須**。CAROUSEL は URL を**カンマ区切りで2件以上**。
- **2アカ目以降の追加手順**は docs/CHANGELOG.md §15（テスター追加→トークン発行→タブ生成→accounts 登録。トークン生成ツールは「今ブラウザでログイン中のアカウント」に発行される点に注意）。

---

## 6. 環境変数 / Secrets / Variables

| 変数 | 置き場所 | 用途 |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Secret | サービスアカウントJSON全文 |
| `BUSINESSES` | Secret | 事業→シートID の JSON 配列（多事業ルーティング） |
| `SPREADSHEET_ID` | Secret | 単一シートのフォールバック（後方互換・即ロールバック用） |
| `ANTHROPIC_API_KEY` | Secret | AI生成（generator/strategy）有効時に必須 |
| `MAIL_USERNAME` / `MAIL_PASSWORD` | Secret | Gmail送信（アプリパスワード） |
| `MAX_POSTS_PER_DAY` | Variable | 1アカウント1日上限（コード既定50・**現在の実設定は5**） |
| `TREE_REPLY_DELAY_SEC` | Variable | ツリー返信の間隔秒（既定30） |
| `LOOKBACK_DAYS` | Variable | 収集対象の過去日数（既定60） |
| `PAUSED` | Variable | **キルスイッチ**。1で投稿・生成を即停止 |
| `GENERATE_POSTS` | Variable | 1で生成有効（既定0＝分析とレポートのみ） |
| `GEN_STATUS` | Variable | draft=人が確認（既定）/ queued=全自動公開 |
| `GEN_STATUS_<NAME>` | Variable | **事業別の上書き**（例 `GEN_STATUS_SEIZOGYO=draft`）。空なら `GEN_STATUS` |
| `GEN_POSTS_PER_ACCOUNT` | Variable | 4本/日対象外の事業の生成本数（既定5） |
| `GEN_POSTS_<NAME>` | Variable | 事業別の生成本数（既定12=3日×4本）。**0でその事業だけ生成オフ**。現在 SEIZOGYO2/SEIZOGYO3/MEGURI=0 |
| `GEN_MODEL` | Variable | 生成モデル（既定 claude-opus-4-8） |
| `FORCE_CYCLE` | Variable | 1で3日サイクルゲートをバイパス（手動実行用） |
| `ENABLE_EMAIL` / `EMAIL_BUSINESSES` / `MAIL_TO` | Variable | 週次メール配信（EMAIL_BUSINESSES空=全事業） |
| `MAIL_TO_<NAME>` | Variable | **事業別の宛先**（カンマ区切りで複数可）。空なら `MAIL_TO` |
| `RUNWAY_WARN_DAYS` | Variable | 在庫の残り日数がこれ以下で警告（既定2・monitor.yml） |
| `TZ_NAME` | env | 既定 Asia/Tokyo |
| `DRY_RUN` | env(ローカル) | "1" で無書込実行（検証用） |
| `THREADS_CLIENT_SECRET` | env(ローカル) | bootstrap_token.py 実行時のみ |

ローカル検証: `DRY_RUN=1 python3 main.py`（または `./scripts/local_run.sh`）／ テスト: `python3 -m pytest tests/ -q`

---

## 7. 次にやること（ロードマップ / 優先順）

0. **★障害復旧（最優先・2026-07-28発生）**: Anthropic APIの残高切れで生成が4サイクル連続失敗し、
   全アカウントの在庫がゼロ。クレジット購入 → `threads-weekly-report` を手動実行で復旧する。
   Auto-reload を有効にすれば恒久解決（詳細と経緯は docs/CHANGELOG.md §27）。
1. **★澪（meguri）の起動（HITL・コードとシートは完成、トークン待ちで inert）**:
   ①テスター追加 → 認可 → 長期トークン取得（`scripts/get_auth_url.py` → `scripts/exchange_token.py`）
   ②accounts にトークン登録 ③Secret `BUSINESSES` に meguri 追加 ④`fill_week_schedule.py --preset meguri`
   **起動時は `GEN_POSTS_MEGURI=0`（設定済み）のまま draft26本を消化する**のが安全。
   手順の正本 = 非公開ローカル `占いThreads-note事業/澪_新アカ立ち上げ/_トークン取得と起動手順_20260728.md`
   （シートID・アプリIDを含むため公開repoには置かない＝§17b）。暦の正確性（縁起日/節気）は宿題。
2. **占いの新アカウント設計**: 「結」廃止に伴い占い枠が空いている。ナレッジは保全済み
   （`占いThreads-note事業/結_アーカイブ_20260728`）。人格・アカウント名は再利用しない方針。
3. seizogyo2/seizogyo3（住田・ぱし）の自動生成をONにするか判断（現在 `GEN_POSTS_*=0` で停止中）。
   ぱしは平均797表示/本と数字が出ているため再開の価値が高い。
4. 安定運用の観察: 製造業は過去2回BAN。`GEN_STATUS_SEIZOGYO=draft` で人の確認を挟む運用も選べる。
5. 安定後、テンプレ化し他クライアントへ複製。**第三者運用／販売の段階で初めて App Review を申請**（Phase E）。

## 8. 未決定事項（Masterに確認すべき）

- トークンのシート保管はセキュリティ上の妥協。アカウント数が増えるなら `Store` をDB（Supabase/SQLite等）実装に差し替える判断。
- `MAX_POSTS_PER_DAY` の最終値（凍結回避と物量のバランス）。
- 長時間ブロック障害時に失敗メールが10分毎に来る問題（シートでのalert抑制案あり・docs/CHANGELOG.md §13）。

## 9. 後続フェーズ（第2/第3層・未着手・文脈共有のため記載）

- 第2層: Threads→LINE導線。無料鑑定をリードマグネットにLINE友だち獲得 → ナーチャ → 月額コンシェルジュ（LTV本命）→ 購入後はMasterが手動対応。
- 第3層: LINE Messaging API + Webhook → Claude API で自動鑑定。受信即「鑑定中」返信 → 2〜6時間後にプッシュ配信（努力ヒューリスティック/返報性）。会話履歴はDB保持しステートレス前提でClaudeへ全文渡す。

### ★コンプラ要件（第2/3層で必須・飛ばさない）
- **消費者契約法の取消権**: 霊感等の特別な能力による知見で不安を煽り契約させると取消対象。恐怖訴求の物販は不可、鑑定はポジティブ/エンパワー型に。
- **LINE公式アカウント規約**: 事前審査はないがモニタリングで停止あり。占い詐欺誘導が社会問題化し監視強化方向。
- **特商法/景表法**: 「無料」表記の正確性、有料商品の特商法表記義務。
- **要配慮情報**: 悩みはセンシティブ情報。プラポリ＋取得同意を友だち追加時に明示。
- **AI開示**: 人間占い師が視ていると誤認させない。
- **★クライシス・ルーティング（最優先実装）**: 希死念慮/自傷/DV/深刻なメンタル等を検知したら自動鑑定を出さず人・専門窓口へ。倫理＋炎上＋賠償リスク回避。

---

## 10. 注意（落とし穴）

- GitHub Actionsのcronは数分遅延することがある（厳密な秒単位公開は不可）。
- トークンをシートに置くため、シートは**サービスアカウントと本人のみ共有**。公開リンク禁止。
- `status=error` 行は、原因解消後に `status` を空に戻せば次回再試行される。
- 親子（ツリー）は親を必ず先の時刻に。親が同回で公開→子は同回 or 次回に自動連結。
- `status=publishing` のまま残った行は公開処理の中断痕。実際に投稿されたかThreadsで確認してから空に戻す（無確認で戻すと二重投稿の恐れ）。
- `DRY_RUN=1` はシートを**一切書き換えない**（公開対象の検出と疎通確認のみ）。
- **「今すぐ公開」になる行があるときに手作業でシートを編集しない**（10分cronとレースして二重投稿の原因＝docs/CHANGELOG.md §16）。整備は**未来時刻の行**に対して行う。
- **障害の切り分け（docs/CHANGELOG.md §13/§14）**: Meta側の `OAuthException code 200 "API access blocked"` ＝アプリ/アカウント主体のブロック→**Threadsアプリで人が承認解除**が必須。Google Sheets の 5xx/429 ＝一過性→`with_retry` が自動回復。トークン失効は code 190（別物）。切り分けは「シートからトークンを読み `GET /v1.0/me` を `requests` で叩く」（urllibはmacOSのSSLで不可）。
- Sheets への書込は batch 化済み（行単位 update_cells / 一括 append_rows）。**投稿ごとに get_all_records を呼ぶ実装は Read/min 429 に当たる**ので書かない（docs/CHANGELOG.md §19）。
- **投稿ジョブは在庫ゼロでも「成功」で終わる。** Actions が緑でも投稿が出ているとは限らない。
  在庫の有無は `monitor.yml`（日次）か `python3 main_monitor.py`（読取専用）で確認する。
  2026-07にこれで4アカウントの停止を9〜19日見逃した（docs/CHANGELOG.md §27）。
- **失った日数を取り戻そうと、過去日時の行をまとめて queued にしない。** 単発投稿の間にウェイトが
  無いため1回の実行で最大 `MAX_POSTS_PER_DAY` 本が数秒間隔で連射され、BAN歴のあるアカで
  スパム判定を誘発する。そもそもThreadsは過去日時に投稿できず穴は埋まらない。

---

## 17. ★公開/非公開データの分類＋知識のシート反映（最重要・全セッション/自動化/分析で必ず識別）

> このリポジトリ `kyon0802/threads-auto-poster` は**公開(public)**。**ターミナル新規作業・自動化・ユーザーからの分析/作業依頼など、あらゆる状況でこのルールを認識・遵守すること。** 分析自動化PRDは Issue #2、設計メモは [[threads-analytics-project]]。

### 17a. 公開repoに上げてOK（コードと仕組みだけ）
- エンジンのコード（publisher/collector/analyzer/reporter/generator）、テスト、`.github/workflows/*.yml`（秘密は Secret 参照）、CLAUDE.md等のドキュメント（秘密を含まない）、スキーマ定義・**ダミー**サンプル。

### 17b. 絶対に公開repoに上げない（漏洩=事業/凍結リスク）
- **秘密**：アクセストークン／APIキー（ANTHROPIC_API_KEY等）／サービスアカウントJSON → **GitHub Secrets と 非公開シートのみ**。
- **事業ノウハウ**：プロフィール・声/戦略・**ガイドライン/規約の分析結果**・コンプラルール・BAN原因分析 → **非公開シート ＋ ローカル(`製造業Threads`/`占いThreads-note事業`)/非公開repo**。
- **実データ**：投稿本文資産・インサイト・分析結果・フォロワー/個人情報 → **非公開シート**。
- スプレッドシートID → public コードにハードコードしない（Secret/Variable）。

### 17c. 置き場所の原則
秘密＝Secrets＋非公開シート ／ 事業データ・知識＝**非公開Googleシート**（サービスアカウント＋本人のみ共有・公開リンク禁止）＋ローカル/非公開repo ／ 公開コード＝この public repo。

### 17d. ローカル知識→シートへの反映（生成精度の源・curation step）
- **generator はシートしか読まない**（GitHub Actions はローカルフォルダを読めない）。だから精度に必要な知識は **人が対話で蒸留してシートに入れる**（自動では入らない）。**生データのローカルミラーは精度に無関係**（人間の制作チーム向け便宜）。
- **シートに入れる＝精度に効くのは「投稿の中身と表現を直接決める知識」だけ**：
  1. **①実績**（インサイト＋分析）… collector が自動収集。
  2. **②`プロフィール_<acc>` タブ（必須）**… 声・トーン・テーマ・お手本・アカ固有NG。
  3. **③`ガイドライン` タブ（事業共通・必須）**… 規約/法令/合法ライン/NGワード/**過去BAN原因の教訓**。
- **これ以外（生の議事録・戦略ブレスト・無関係資料）はシートに入れなくても精度は落ちない。**
- 反映元の例（製造業）：`プロフィール/アカA_たくみ_プロフィール.md`→`プロフィール_takumi_kojo_navi` ／ `compliance/コンプライアンス_マスター.md`＋`アカウント運用ルール_個人2アカ版.md`＋`勝ちパターンとBAN原因_分析.md`→`ガイドライン`。占いは `占いThreads-note事業/` 配下から同様に。
- 反映は**初回セットアップ＋ルール/プロフィール更新時**に行う curation 作業（私が対話で実施 or ユーザー）。自動 generator はその結果のタブを読むだけ。
- **澪（meguri）は甘めガイドライン**（docs/CHANGELOG.md §25）：NGワードは最小ハード法務のみ（`治る/完治/効く/稼げる/儲かる/不労所得`）。`必ず/絶対/100%/当たる` は**入れない**（断定トーンを殺すため）。霊感商法/誇大の抑止はガイドライン本文＋人の二段構え。

### 17e. ★情報抜け防止メカニズム（curationは手作業＝抜けが起きうるので機械ゲートで担保）
curation（ローカル知識→シート）は手作業なので、放置すると「重要な規約が抜けたまま生成」が起こりうる。これを**4重で防ぐ**：
1. **必須タブ存在ゲート（generator 起動時に強制）**：各アカウントで `プロフィール_<acc>`（または `ナレッジ_<acc>`）と 事業 `ガイドライン` が**存在＋非空**でなければ generator は**生成を中止**（loud fail＋通知）。＝「抜け→静かに低品質投稿」を「抜け→生成停止＋通知」に変える。
2. **反映元レジストリ（明文化）**：「どのローカルファイル→どのタブ」を §17d / PRD #2 に固定。curation時はこの一覧を**必ず網羅**（チェックリスト化）。
3. **ユーザー確認（意味的完全性）**：初回curation後、`ガイドライン`/`プロフィール_<acc>` をユーザーが一度レビューし「抜けなし」を確認。
4. **機械コンプラゲート（二重の安全網）**：生成後も `ガイドライン` を参照して違反を遮断（合格分のみ投入）。
- （任意）ドリフト検知：ローカル元ファイルが更新されたのにタブが未更新なら警告。
- ゲートは「タブの存在/非空」を保証、レジストリ＋ユーザーレビューが「中身の網羅」を保証する役割分担。

---

## 改修ログ

時系列の改修履歴（§11〜§16, §18〜§25）は **[docs/CHANGELOG.md](docs/CHANGELOG.md)** を参照。
今後の改修も CHANGELOG に追記し、本ファイルは「現在の仕様・運用」だけを更新すること。
