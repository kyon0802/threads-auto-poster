# 改修ログ（CHANGELOG）— Threads自動投稿システム

> CLAUDE.md から移設した時系列の改修履歴（§11〜§25・内容は原文のまま）。
> 現在の仕様・運用ルールは リポジトリ直下の CLAUDE.md を正とする。

## 11. 改修ログ（2026-06-12）

機械（このフォルダ）側の堅牢化。ロジックは `test_logic.py` 全6ケースPASS（API不要のモック検証）。
- **二重投稿防止(write-ahead)**: 公開API実行前に `status=publishing` を書き、成功後に `posted`。中断時は `publishing` で残り再投稿されない（§5/§10）。
- **DRY_RUN がシートを汚さない**: 旧実装は dry-run でも `posted` を書き戻していた不具合を修正。検証実行が安全に。
- **シート書き戻しを batch 化**: 1セルずつ → 1行まとめて1回（Sheets APIクォータ対策）。
- **CAROUSEL対応**: `threads_api` に複数画像／動画の逐次コンテナ化→親カルーセル公開を実装。`media_url` カンマ区切り。
- **トークン更新のエッジケース修正**: 手動投入直後（`token_updated_at`空）の長期トークンに不要な refresh を叩かないよう初期化。
- **MAX_POSTS_PER_DAY 一元化**: `post.yml` のハードコードを廃し、リポジトリ Variable へ。
- **content→sheet ブリッジ追加**: `scripts/batch_to_csv.py`。コンテンツ側の立ち上げバッチ(R1形式 Markdown)を posts CSV へ変換＋2アカ3hずらしスケジュール生成＋NGワード最終防壁。
  ※ コンテンツ資産は `製造業Threads/01_現運用_個人エージェント2アカ/`（旧 `個人エージェント×Threads`。2026-06-12にリネーム）配下。

---

## 12. 改修ログ（2026-06-14〜15）本番稼働＋シートUX

- **本番稼働（Phase B/C/D 完了）**: account=`takumi_kojo_navi`（旧handle `rk_riko2`）で実投稿成功（2026-06-14）。private repo `kyon0802/threads-auto-poster`、10分cron稼働。Secrets=`GOOGLE_SERVICE_ACCOUNT_JSON`/`SPREADSHEET_ID`、Variable=`MAX_POSTS_PER_DAY`。gh CLI は `/opt/homebrew/bin/gh`（PATH未通）でアカウント kyon0802・scope に workflow 追加済み。
- **シート見出しの日本語化（後方互換つき）**: `sheets.py` にエイリアス層（内部キー(英語)⇔日/英見出し）。`migrate_headers_ja.py` で既存シートをデータ保持のまま日本語化。
- **アカウント別タブ化**: 投稿タブを `投稿_<account>` で複数持てるように（タブ名からアカウント自動判定＝行に書かない）。`setup_post_tab.py` で生成。複数アカは**1シート・1リポジトリ**で運用（分けるのは Phase E のみ）。
- **入力支援**: `add_validation_ja.py` で `メディア種類`/`状態`=ドロップダウン、`投稿日時`=形式チェック＋文字列書式。`記入例` タブにツリー例。
- **local_run.sh 修正**: コマンドラインの `DRY_RUN` を `.env` より優先（`DRY_RUN=0 ./scripts/local_run.sh` が実投稿になるよう）。
- 既知メンテ: `post.yml` の `actions/checkout@v4`・`setup-python@v5` が Node20非推奨警告（2026-09-16撤去予定。動作は継続）。

---

## 13. 改修ログ（2026-06-16〜18）障害対応・失敗通知・アカウント改名

- **API access blocked 障害（2026-06-16）**: 投稿が全停止。原因は Meta側がトークンを全API一律 `OAuthException code 200 "API access blocked."` でブロック（=アプリ/アカウント主体への制限。トークン失効 code 190 とは別物で `refresh_access_token` すら弾かれる）。コード/cron/シートは正常。**Threadsアプリの「不正アクセス検知」をユーザーが承認・解除して復旧**。`error` で止まった行は `状態` を空に戻し再投稿。切り分けは「シートからトークンを読み `GET /v1.0/me` を `requests` で叩く」（urllibはmacOSのSSL CERT_VERIFY_FAILEDで不可）。
- **失敗メール通知を有効化（2026-06-18）**: GitHub純正のActions失敗通知を利用。投稿エラー時は `main.py` が exit code 2 → run failure 扱い → メール送信。受信先はオーナーのGitHub通知メール（Settings→Notifications→Default notifications email＋System→Actions=Email/"failed workflows only"）。実テスト済み。※長時間ブロック時は10分毎にメールが来るため、将来シートでalert抑制する案あり。
- **アカウント改名（2026-06-18）**: Threads側で handle を `rk_riko2` → **`takumi_kojo_navi`** に変更。**user_id(36368336406145487)・アクセストークンは不変**（handle変更はトークンを無効化しない＝`GET /me` でid同一・username更新を確認済み）。システム側の表記を全て更新: シート（`accounts` の `アカウント` 値＋投稿タブ名 `投稿_rk_riko2`→`投稿_takumi_kojo_navi`）、`setup_account.py`/`setup_post_tab.py`/`migrate_headers_ja.py` の既定値・例、本ファイルの記述。**今後アカウント名を指すときは `takumi_kojo_navi`**。

---

## 14. 改修ログ（2026-06-18）Google Sheets 一過性エラーの自動リトライ

- **誤報メール障害（2026-06-18 16:00 JST / 07:00 UTC）**: run が1回だけ失敗し失敗メールが届いた。原因は **Google スプレッドシート側の一時的 HTTP 502**（`GoogleSheetStore.__init__` の `worksheet("accounts")` ＝ gspread 呼び出しで `APIError(502)` が未捕捉 → 投稿ロジック到達前に exit 1 → run失敗 → メール）。**Threads API でもトークンでもない**＝§13 の「API access blocked(code 200, Meta主体ブロック)」とは**全くの別物**（あちらは人の承認解除が必須、こちらは自己回復する一過性）。実害ゼロ（落ちたのが書き込み前なので `publishing`/`error` の中断痕なし・データ無傷）、次の cron(07:10 UTC)以降は自動回復済みだった。
- **根本原因**: `GoogleSheetStore` の gspread 呼び出しに**リトライが皆無**だった。Google 側は 5xx/429 や接続瞬断を日常的に返すため、いつでも再発し得た。
- **修正**: `threads_poster/sheets.py` に `with_retry(fn, attempts=5, base_delay=2.0)` ＋ `_is_transient(exc)` を追加し、**全 gspread ネットワーク呼び出し**（open_by_key / worksheet / worksheets / get_all_records / row_values / col_values / update_cells）をラップ。指数バックオフ（2→4→8→16秒）で自動再試行する。
  - **再試行する**: HTTP **429/500/502/503/504**（=APIErrorの`.response.status_code`）＋ **応答到達前の network 障害**（`requests` の `ConnectionError`/`Timeout`＝`.response is None` のもの）。
  - **再試行しない（即送出）**: 404/403 等の恒久エラーや通常のバグ例外。
  - **安全性**: 書き込み(`update_cells`)は特定セルへの RAW **上書き**（追記ではない）なので再試行は**冪等**＝二重投稿リスクなし。Threads publish 層（write-ahead `publishing`→`posted`）は `with_retry` の外で不介入。
- **検証**: `test_logic.py` に TEST 7〜11 追加（502再試行で成功 / 404即送出 / 試行使い切りで送出 / network例外も再試行 / ValueError等は再試行しない）。全11テスト PASS ＋ 実シート DRY_RUN 成功。`threads-code-reviewer` レビュー通過（major=network例外取りこぼしを修正済み）。
- **既知の据え置き（minor, 現運用規模で実害小）**: ①広域障害時は各呼び出しが最大30秒粘り run 全体が伸び得る（ただし最初の呼び出しで早期 fail するため限定的）。②429 は `Retry-After` を尊重せず固定バックオフ（将来アカウント/投稿数が増えたら検討）。

---

## 15. 改修ログ（2026-06-18）2アカウント目 `miko_yui_musubi` 追加

- **2アカウント目を本番投入（2026-06-18）**: `miko_yui_musubi`（**user_id `36383330141313377`**）。takumi_kojo_navi と**同一 Meta アプリ・同一シート・同一リポジトリ**に相乗り（テスター方式。CLAUDE.md §12 の運用方針どおり）。**現在のアカウントは takumi_kojo_navi / miko_yui_musubi の2つ**。投稿タブはそれぞれ `投稿_takumi_kojo_navi` / `投稿_miko_yui_musubi`。
- **追加手順（次アカ追加時のテンプレ）**: ①Threadsテスターに追加（Meta開発者ダッシュボード）＋**本人アカウントで招待を承認（Threadsアプリ側：設定→アカウント→ウェブサイトのアクセス許可）** → ②ダッシュボードの**ユーザートークン生成ツール**で長期トークン発行 → ③`setup_post_tab.py --account <名>` ＋ `add_validation_ja.py --tab 投稿_<名>` でタブ生成 → ④`setup_account.py --token-file <file> --account <名>` で accounts 登録（`GET /me` で user_id 自動取得）。
- **★ハマりどころ（今回実際に詰まった）**: トークン生成ツールは「**今ブラウザで Threads にログイン中のアカウント**」に発行する（アカウント選択UIではない）。takumi でログインしたままだと "takumi_kojo_naviとして続行" しか出ず miko を選べない。**シークレット/別ウィンドウで miko にログインし直してやり直す**のが正解。同意画面は「<対象アカウント名>として続行」と出るので、対象名を必ず確認してから続行する。
- **トークンの種別判定**: ダッシュボード生成トークンは**長期**。短期→長期交換API `th_exchange_token` に長期トークンを渡すと `code 452 "Session key invalid"` が返る（＝既に長期である証左）。`GET /v1.0/me` が 200 なら有効。手動投入直後は `token_updated_at` を現在時刻で記録するため不要な refresh は走らない（§11）。**新アカのトークンを発行・確認したら、念のためローテーション（再発行）推奨**（取得経路にトークンが残るため）。
- **`setup_account.py` バグ修正**: `ws_p = sh.worksheet("posts")` を**無条件で**開いていたため、`posts` をリネーム済みのアカウント別運用では新アカ登録時に `WorksheetNotFound` でクラッシュしていた。投稿先を `投稿_<account>`（無ければ `posts` にフォールバック）へ変更し、`--add-test-post` 指定時のみ開くようにした。

---

## 16. 改修ログ（2026-06-18〜19）二重投稿インシデント＋冪等性の堅牢化

- **二重投稿インシデント（2026-06-18, miko_yui_musubi 接続テスト時）**: miko の初投稿が2回公開された。原因は**投稿IDが空のまま「公開対象」になったこと**。空 row_id だと書き戻し（`update_post`）が「最初の空ID行」を探すため正しい行に着地せず、シート上は未投稿のまま残る → 外部cron(10分毎/cron-job.org)が公開済みなのに続く手動 dispatch が同じ投稿を再公開した（**cronと手作業のレース**）。実害＝同一投稿2件（**Threads API はこのアプリ権限では投稿削除不可** ＝ `code 10 "Application does not have permission"`。アプリで手動削除）。row_id を全行へ一意付与（`miko-001`…）して以降は再発なし（自動化で各1回ずつ正常公開を確認）。
- **★row_id（投稿ID）の必須要件**: **投稿IDは必ず埋める＋全タブで一意**にすること。`update_post` はタブを順に走査し最初に一致した row_id 行を更新するため、**アカウント跨ぎで同じIDがあると別アカウントの行を誤更新する**。アカウント別の接頭辞推奨（例 `miko-001…`、takumi は素の数字 `1,2,…`）。空欄・重複は厳禁。
- **堅牢化（コード修正・`test_logic.py` TEST 12〜14 で検証, 全14 PASS）**:
  - `publisher`: **投稿IDが空の行は公開しない**（警告ログ＋スキップ）。row_id は冪等性のキーなので、無いまま公開させない。
  - `update_post`（GoogleSheetStore/MemoryStore）: **空キーは書き戻さない**（誤って先頭行を書き換えない）＋ **`account` 引数でアカウント別タブに限定**（タブ跨ぎの同一row_id誤更新を防止）。`publisher` は書き戻し時に account を渡す。
- **教訓（運用）**: 「今すぐ公開」になる行があるときに手作業でシートを編集すると外部cronとレースする。コンテンツ投入後の整備（row_id付与等）は**未来時刻の行**に対して行うか、実投稿は**cronと直列化される GitHub Actions 経由**でのみ行うこと。

---

## 18. 改修ログ（2026-06-24）事業分離（Phase 0）＝事業ごとに非公開シートを分割

PRD #2 の Phase 0 を実施。**「全事業が1シートに同居」→「事業ごとに独立した非公開シート」**へ移管し、エンジンを多事業対応にした（投稿は止めずに切替）。

- **事業別シート**：製造業（`takumi_kojo_navi`）／占い（`miko_yui_musubi`）をそれぞれ独立した**非公開**スプレッドシートに分離。各シートは `accounts` ＋ `投稿_<acc>` を持つ（将来 `インサイト_<acc>` 等の収集タブもこの事業シートに足す）。**シートIDは公開repoに書かない**＝GitHub Secret `BUSINESSES` で管理（§17b 準拠）。
- **多事業ルーティング（コードは1つのまま）**：`main.py` に `resolve_business_sheets(env)` を追加。環境変数 `BUSINESSES`（JSON配列 `[{"name","spreadsheet_id"}, …]`）があれば**事業ごとに `GoogleSheetStore`＋`Publisher` をループ**。無ければ従来の `SPREADSHEET_ID` 単体に**フォールバック**（後方互換・即ロールバック）。1事業が失敗しても他事業は止めず、run 全体は失敗扱い（exit 2 ＝失敗通知は維持）。`Publisher` 本体は無変更＝既存テストの保証をそのまま維持。
- **移管ツール**：`scripts/migrate_to_business_sheet.py --src <旧ID> --dst <新ID> --account <acc>`。`accounts` 行（トークン・日次カウント込み）＋ `投稿_<acc>` 全行を **verbatim ミラー**（`posted`/`投稿後ID`/`状態` を含む＝**二重投稿防止の生命線**）。**全セル RAW(文字列)書込**で17桁の user_id / 投稿後ID やトークンの**桁落ちを防止**。**冪等**（再実行で旧シートの最新状態を全置換ミラー）。
- **安全な切替手順（実施済み）**：①cron停止（`gh workflow disable post.yml`）②in-flight 無しを確認→移管スクリプト**再実行**で旧シートの最終状態を再同期（切替直前に公開された投稿との race を消す）③Secret `BUSINESSES` 登録＋`post.yml` の env に `BUSINESSES` 追加→push ④`workflow_dispatch` で1回実行し**両事業 error0** を確認 ⑤cron再開（`gh workflow enable`）。**旧シート＋旧Secret `SPREADSHEET_ID` は温存**（post.yml を戻すだけで即ロールバック）。
- **次段（Phase 1）への接続**：トークンを各事業シートの `accounts` に持つ構成は不変。収集（collector）は事業シートに `インサイト_<acc>` 等を足し、新設 `collect.yml` が**同じ `BUSINESSES` ルーティング**で事業をループする（投稿cronとは別系統・読取専用）。

---

## 19. 改修ログ（2026-06-24）Phase 1 ＝ インサイト自動収集 本番稼働

PRD #1（#2 の Phase 1）を実装・稼働。公式 Threads Insights API で各投稿/アカウントの実績を**毎日自動収集**し、事業シートに**日次スナップショット**で蓄積する。**読み取り専用＝投稿系には一切触れない。**

- **読み取りAPI**（`threads_api.py` に追加・状態を持たない）：`list_media`（投稿一覧・カーソルページング）／`get_media_insights`（投稿別 views,likes,replies,reposts,quotes,shares）／`get_user_insights`（アカウント全体 views,likes,replies,reposts,quotes,followers_count）。返却 `data[]` は並び順非保証のため **name キーで参照**、欠落 metric は 0/空 に耐性。既存 `post()` 等は無変更。
- **保存（`sheets.py`）**：`Store` に `upsert_insights_bulk`／`upsert_account_metric` を追加（ABC/Google/Memory）。タブ `インサイト_<acc>`（投稿後ID×取得日で冪等・本文長/ツリー有無/エンゲージ率込み）と `アカウント指標_<acc>`（フォロワー数等・取得日で冪等）を**無ければ自動生成**。
- **collector（`collector.py`・Publisher 対称・読取専用）**：各アカで lookback(既定60日)内の投稿を日次スナップショット。エンゲージ率＝(likes+replies+reposts+quotes)/views（views 欠落時は空）。投稿タブと posted_id で結合し row_id / ツリー有無 を付与。1件のメディア失敗で全体は止めない。`main_collect.py` が `main.py` と**同じ `BUSINESSES` ルーティング**で事業ループ。
- **cron**：`.github/workflows/collect.yml`（日次 UTC19:00=JST04:00・`concurrency: threads-insights-collect` で投稿と別系統・`workflow_dispatch` 可）。
- **★Sheets レート対策（重要）**：投稿ごとに `get_all_records` していた初版は、2事業連続収集で **Read requests/min 上限(429)** に当たり本番collectが失敗。**`upsert_insights_bulk`＝タブを1回だけ読み、update_cells と append_rows を各1回にまとめる**方式へ改修して解消（collector はアカウント単位で行を貯めて一括書込）。
- **実機で確定した仕様**（再認可後に実トークンで確認）：media の `views`/`shares` は実際に返る／account `views` は since/until 不要で返る／`follower_demographics` は**100フォロワー超**が条件（takumi155=可・miko49=不可）で当面未使用。
- **前提（実施済み）**：`threads_manage_insights` を追加して両アカ再認可→各事業シート `accounts` の `アクセストークン` を差し替え（self-owned＝開発モードで App Review 不要）。検証＝両事業 error0 で本番run成功。
- **テスト**：`test_collect.py`（冪等/lookback/dry_run/1件エラー耐性/結合/views欠落＝7ケース）。`MemoryStore`＋`FakeClient`＋`now_fn` 注入で外部挙動のみ検証（prior art=test_logic.py）。
- **次（Phase 2）**：週次集計（時間帯/曜日/本文長/ツリー有無 別の平均エンゲージ率）＋`週次レポート`＋AI翌週コンテンツ生成→機械コンプラゲート→`投稿_<acc>` へ queued 投入。前提＝`プロフィール_<acc>`＋事業共通 `ガイドライン` の curation（§17d/§17e）＋`ANTHROPIC_API_KEY`。

---

## 20. 改修ログ（2026-06-24）Phase 2 ＝ 分析・週次レポート・AI翌週コンテンツ生成

PRD #2 の Phase 2 を実装。実績→分析→レポート→AI生成→**機械コンプラゲート**→`投稿_<acc>` への **draft** 投入まで。生成投稿は **status=draft**（人が `queued` に変えるまで自動公開されない）＝AIが勝手に本番投稿しない設計。

- **analyzer（`analyzer.py`・AI不使用）**：`インサイト_<acc>` の**最新スナップショット**（posted_id ごと snapshot_date 最大）から、時間帯/曜日/本文長/ツリー有無 別の平均エンゲージ率・平均表示を集計→`インサイト分析_<acc>` へ。集計核は純関数 `analyze_insights()`。
- **reporter（`reporter.py`・AI不使用）**：勝ちパターン仮説＋上位投稿を `週次レポート` タブへ追記＋（任意で）ローカル事業フォルダへ Markdown ミラー。
- **compliance（`compliance.py`・決定的ゲート・§17e）**：`ガイドライン` の「NGワード」行から禁止語を抽出し、本文の NGワード/外部URL/文字数超過を機械的に遮断。LLM 判断に頼らない最終防壁。
- **generator（`generator.py`）**：`プロフィール_<acc>`＋`ガイドライン`＋分析を Claude（既定 `claude-opus-4-8`・構造化出力）に渡し翌週案を生成→ compliance ゲート通過分のみ `投稿_<acc>` へ `status=draft`＋一意 row_id（`<prefix>-gYYYYMMDD-NN`）で投入。**★必須タブ存在ゲート**：`プロフィール`/`ガイドライン` が空/欠落なら `GeneratorError` で**生成中止**（盲目生成の禁止）。生成LLMは `generate_fn` 注入（テスト=フェイク／本番=Anthropic SDK `make_anthropic_generate_fn`）。
- **Store 追加（`sheets.py`）**：`get_insights`/`get_profile`/`get_guideline`/`write_analysis`/`append_report`/`add_post`（Google/Memory 両方）。タブ定数 `インサイト分析_` `プロフィール_` `ガイドライン` `週次レポート`。
- **cron**：`main_weekly.py`＋`.github/workflows/weekly.yml`（月曜06:00JST＝日21:00UTC・別concurrency）。**分析+レポートは既定実行**、**生成は `GENERATE_POSTS=1`＋`ANTHROPIC_API_KEY`（Secret）で有効化**（既定オフの段階リリース）。`weekly.yml` は当面 `gh workflow disable`（id 301384598）。
- **curation（事業ノウハウ・非公開）**：`プロフィール_<acc>`（声/テーマ/お手本/NG）と事業共通 `ガイドライン`（規約/法令/NGワード/過去BAN教訓）を**非公開シートにのみ**保持（公開repo禁止・§17）。元ネタはローカル（製造業Threads/占いThreads-note）から対話で蒸留。書込スクリプトも repo 外。
- **テスト**：`test_phase2.py`（集計/最新スナップ/コンプラ遮断/必須タブゲート/生成パイプライン/プロンプト内包＝6 PASS）。実機デモで製造業5/5・占い4/5合格（占い1本は NGワード「結ばれる」をゲートが遮断）→ draft 投入を確認。
- **前提（HITL・残）**：自動生成を回すには `ANTHROPIC_API_KEY` を GitHub Secret 登録＋Variable `GENERATE_POSTS=1`＋`weekly.yml` を enable。生成 draft は人がレビューして `queued` へ。

---

## 21. 改修ログ（2026-06-25）製造業の投稿スケジュールを「1日4本・ランダム配置」に変更

**背景**：generator が翌週案を「**翌日から1日1本・21時固定**」（`generator.py` の `(now+timedelta(days=j)).replace(hour=suggest_hour)`）で並べていたため、製造業 `takumi_kojo_navi` が**毎日1投稿・毎日同じ21時**になっていた（しかも複数回 run すると同21時に重複）。ユーザー要望＝**1日4投稿・時刻はランダム**（昼12時前後に1本＋夜18:00〜23:00にランダム3本／投稿間の**最低間隔30分**・ただし30分等間隔ではなくランダム配置）。**製造業(seizogyo)のみ**。占い(uranai)は従来どおり1日1本21時で不変。

- **新規 `threads_poster/schedule.py`**（純関数・乱数注入可能 `rng`）：
  - `_random_times_with_min_gap(rng,start,end,count,gap)`＝区間内に count 個を**最低間隔gap以上でランダム配置**（昇順）。空き時間 `free=幅-(count-1)*gap` を一様乱数オフセットに分配→ソート→`i*gap` 足し戻し。**終始 整数分**で扱い丸め誤差で30分を割らない。`free<0` は `ValueError`。
  - `daily_slots_minutes(rng,...)`＝1日分（既定 昼12:00±30分 ＋ 夜18:00-23:00に3本・最低間隔30分）→4スロット昇順。
  - `build_schedule(n,*,start_date,tz,rng,**daily)`＝n本を**翌日から1日4本ずつ**詰めて `["YYYY-MM-DD HH:MM",…]` を返す。各日ごとに時刻を新規ランダム生成（毎日同時刻にならない）。端数日は早い順に採用。
- **`generator.py`**：`Generator(... schedule_fn=None, rng=None)` を追加。`schedule_fn` があれば予約時刻をそれで割当（製造業＝`build_schedule`）、無ければ**従来挙動を厳密に維持**（占い等は不変）。`make_anthropic_generate_fn` の `max_tokens` を本数比例（`min(32000,max(8000,400*n))`）に＝28本でも切れない。
- **`main_weekly.py`**：`SCHEDULE_FN_BY_BUSINESS={"seizogyo":build_schedule}` と `n_posts_for(name,env,default)` を追加。**事業ごとに本数を分離**（seizogyo＝28本＝4×7日／Variable `GEN_POSTS_SEIZOGYO` で上書き可・他事業は `GEN_POSTS_PER_ACCOUNT` 既定5）。※`GEN_POSTS_PER_ACCOUNT` は全事業共通なので、これを28にすると占いが28日先まで1本/日で並ぶ→事業別本数で回避。事業名はライブ `BUSINESSES` secret が `seizogyo`/`uranai`（weekly run ログで確認済み）。
- **`weekly.yml`**：`GEN_POSTS_SEIZOGYO: ${{ vars.GEN_POSTS_SEIZOGYO || '28' }}` を追加。
- **既存 queued 投稿の再配置**：旧ロジックで「毎日21時・一部重複」に並んでいた generator 投稿9件（`takumi-g…`）を、新スケジュールへ**実シートで再配置**（明日06-26から 4＋4＋1）。専用 `scripts/reschedule_posts.py`（**既定 dry-run**・`--apply` で書込・`update_post` 再利用でアカウント別タブ限定RAW・**post_datetime のみ**更新／status/posted_id/row_id/本文は不変・読み戻し検証つき）。MAX_POSTS_PER_DAY=5 のままで 4/日 ≤ 5 で安全。
- **テスト**：新規 `test_schedule.py`（最低間隔ヘルパ/配置不能/1日スロット/**500 seed の最低間隔30分＋窓内**/固定グリッドでない/4本詰め/28本=7×4/再現性＝8 PASS）。`test_phase2.py` に generator＋schedule_fn（1日4本検証）と従来挙動維持の2件を追加（計8 PASS）。`test_logic`/`test_collect` も全PASS。
- **過渡期メモ**：再配置9件は 06-26〜06-28(部分) をカバー。次の週次 run（月曜06-29 06:00）が翌日06-30から28本を再生成し以降は毎日4本で自走。06-29 は薄い（再配置の端数）。**製造業は過去2回BAN・現状 `GEN_STATUS=queued`（無確認自動公開）**＝物量増（28/週）に伴い凍結リスクが上がる点は要観察（必要なら `GEN_STATUS=draft` で確認運用へ）。

---

## 22. 改修ログ（2026-06-25）週次レポートの強化（TOP5全文＋詳細KPI＋デザイン刷新＋来週方針＋メール配信）

ユーザー要望で週次レポートを刷新。①TOP5を**実際の投稿本文で全文表示**②各項目に説明をつけ曖昧さ排除③**明るい白ベースのシンプルなデザイン**④**来週の方針＋具体的な投稿例3本**（AI生成）⑤オーナー宛に**毎週メール配信**（宛先は Variable `MAIL_TO`）。

- **`html_report.py` 全面刷新**：ダーク→**明るい白ベース**。**メール(Gmail)でもそのまま読める**よう table＋全要素インラインCSS（flex/grid/`<style>`/外部CSSなし）。セクション＝KPIサマリ(各指標に1行説明)／投稿ランキングTOP5(表示数順)／同(エンゲージ率順)／傾向分析(棒グラフ＋読み解き)／来週の方針／投稿例3本。`build_fragment`(本体)＋`wrap_document`(全文書)＋`build_html`(1アカ完結)に分割し、複数アカを1通のメールに連結可能。NaN/inf は『—』表示。本文は改行保持＋HTMLエスケープ(XSS安全)。
- **`analyzer.py`**：`total_views`/`total_reactions`/`avg_er` のKPI合計と、**エンゲージ率順TOP5(`top_er`)** を追加（従来の表示数順 `top` と併存）。`_entry` に likes/replies/reposts/quotes/reactions を含め全文結合(`text`)は下流で付与。★**ER=0.0 を欠落扱いしないよう修正**（`x or ""` は数値0.0が falsy で平均/ランキングから脱落し avg_er が上振れしていた→`_has_er` で None/空文字のみ欠落判定）。
- **`strategy.py`（新規）**：来週方針(direction)＋やること(focus)＋投稿例(examples 3本)を Claude で生成（`make_anthropic_strategy_fn`・`generate_fn` 注入可）。例文は**機械コンプラゲート**(NGワード/URL/字数)を通し違反は除外。キー無し/失敗時は **None**（方針セクションなしでレポートは出る）。**読取専用＝投稿キューに触れない**。
- **`main_weekly.py`**：分析→`enrich_tops_with_text`(投稿後ID経由でTOP5に本文結合・事業ごと posts を1回読みで使い回し)→reporter→**方針生成(`generate` が True のときのみ＝PAUSED/GENERATE_POSTS を尊重・課金抑制)**→HTMLレポート(reports/保存)＋メール本文 fragment。メール本文＝各アカの視覚レポートを連結した1通。全 `open` に `encoding="utf-8"`。
- **メール配信**：`weekly.yml` の `dawidd6/action-send-mail`(smtp.gmail.com:465・宛先は Variable `MAIL_TO`・本文 `reports/メール本文.html`・添付 `reports/*.html`)は既存。**有効化に `ENABLE_EMAIL=1`(Variable・設定済み)＋`MAIL_USERNAME`/`MAIL_PASSWORD`(Secret＝Gmailアプリパスワード・ユーザー作業)** が必要。次の月曜 cron から配信、または `workflow_dispatch` で即時テスト可。
- **テスト**：`test_phase2.py` に analyze合計KPI＋ER順TOP5／**ER=0.0回帰**／strategyコンプラゲート／html(本文全文＋数値整形＋方針)の計4ケースを追加。全スイートPASS。実データ(製造業21投稿・総表示4,978・平均ER0.69%)でテストレポートを生成しデザイン確認済み（Desktop `週次レポート_製造業_テスト_20260625.html`）。
- **adversarialレビュー(workflow)で確定7件中、major(ER=0.0脱落)＋minor(方針のPAUSED素通り)＋nit(NaN/inf・json.loads・encoding・posts再読込)を反映済み。**

---

## 23. 改修ログ（2026-06-25）週次メールを「アカウントごとに個別送信」へ

ユーザー要望：運用中の**アカウントごとに別々のレポート＋別々のメール**（製造業と占いがそれぞれ1通ずつ届く）。

- **送信をPython側へ移管**：GitHub Action の単一 `dawidd6/action-send-mail` ステップ（1通固定）を廃止し、`main_weekly.py` が**アカウントごとに1通ずつ**送る方式に変更。
- **新規 `threads_poster/mailer.py`**：`build_message`(HTML本文＋同内容HTML添付の MIME)／`send_message`(SMTP_SSL・CAは `certifi.where()` 優先＝macOSの CERT_VERIFY_FAILED 回避・ubuntu可・`smtp_factory` 注入でテスト可)／`send_html`(便利関数)。
- **`main_weekly.py`**：`send_account_reports(reports, user, password, to, gen_date, send_fn=None)`＝アカウント別に件名 `【Threads週次】<事業ラベル>｜<account>（日付）` で送信、1通失敗が他を止めず `(sent, failed)` を返す。ループでは各アカの `build_html` を `email_reports` に貯めて最後に送信。`EMAIL_BUSINESSES` は **空＝全事業（運用中の全アカに個別送信）**（旧 既定"seizogyo"から変更）。メール失敗は run の失敗数に計上。
- **`weekly.yml`**：python ステップ env に `ENABLE_EMAIL`/`EMAIL_BUSINESSES`/`MAIL_USERNAME`/`MAIL_PASSWORD`/`MAIL_TO`(既定= MAIL_USERNAME) を追加。旧メールステップ削除（`reports/メール本文.html` 依存も解消）。
- **`strategy.py` 修正**：`build_strategy_prompt` に `profile` を渡し、**ナレッジが空ならプロフィールを知識源に使う**（占いはナレッジ未同期・プロフィールのみのため、声が反映されない不具合を解消）。
- **テスト**：`test_phase2.py` に 個別送信(件名・通数)／1通失敗の隔離／`build_message` の3ケース追加。全PASS。**実機テスト**＝製造業(views4978)＋占い(views53)の2通をローカルSMTPで実送信成功（宛先は Variable `MAIL_TO`・各 Desktop にHTMLも保存）。
- **設定状況**：`ENABLE_EMAIL=1`＋`MAIL_USERNAME`/`MAIL_PASSWORD` 設定済。`EMAIL_BUSINESSES` 未設定＝全事業。次の月曜cronから両アカが**別々のメール**で届く。

---

## 24. 改修ログ（2026-06-26）3日PDCAサイクル化＋占いも1日4本＋1文目フック/短文の徹底

ユーザー要望：①PDCAを**3日に1回**（3日分生成→3日分を分析してレポート→繰り返す）②**占い(uranai)も1日4投稿**③占いの投稿は**長すぎる→短文化**、**1文目フックが最重要**（弱い1文目＝全く見られない／長文ほど表示回数が落ちる）。④初回は「本日(金)06-26の夕方から」投稿開始。

- **3日サイクル（`main_weekly.py`）**：`weekly.yml` の cron を**毎日**(`0 21 * * *`＝06:00 JST)に変更し、`main_weekly.py` 冒頭の**サイクルゲート** `is_cycle_day(today)`＝`(today - CYCLE_ANCHOR) % 3 == 0` で**3日ごとの日だけ本処理**（分析→レポート→生成→メール）を実行、他日は即 `return 0`。`CYCLE_ANCHOR=2026-06-28`（初回手動サイクル06-26夕〜28の直後）→ 06-28／07-01／07-04… で稼働。`*/3` の day-of-month は月末で崩れるため**起点日アンカー方式**。手動実行は Variable/ env `FORCE_CYCLE=1` でゲートをバイパス。
- **生成本数＝1サイクル分**：`n_posts_for` を「4本/日対象事業（`SCHEDULE_FN_BY_BUSINESS` に居る seizogyo/uranai）は `CYCLE_DAYS*POSTS_PER_DAY=3×4=12本`」に変更（旧 seizogyo=28本/週から）。`weekly.yml` の既定 `GEN_POSTS_SEIZOGYO=12`／新 `GEN_POSTS_URANAI=12`。各サイクルで generator は**翌日から3日×4本**を生成→06-28実行で06-29〜07-01、07-01実行で07-02〜07-04…と隙間なく連続。
- **占いも1日4本（時間帯プリセット）**：`schedule.py` に `PRESETS`（`seizogyo`＝昼12時前後＋夜18-23時に3本／`uranai`＝午前8:00-11:30に1本＋夕方-深夜17:00-23:59に3本・どちらも最低間隔30分）。`SCHEDULE_FN_BY_BUSINESS={seizogyo, uranai}` に `partial(build_schedule, **PRESETS[...])` を登録（占いに専用時間帯を注入）。
- **`build_schedule` 拡張**：`days`（日数モード）／`start_offset_days`（既定1＝翌日・0で当日開始）／`not_before`（過去スロット除外）を追加。初回「本日夕方スタート」は days=3・start_offset_days=0・not_before=now で**当日は現在時刻以前を除外**（午前枠が過ぎていれば夕方3本のみ）。
- **1文目フック・短文ルール（全事業共通）**：`generator.THREADS_HOOK_RULES` を新設し `build_prompt`／`strategy.build_strategy_prompt` の両方に注入。要点＝(1)1文目が全て・挨拶/自己紹介/呼びかけ/定型句で始めない（占いの旧定型の挨拶導入＝弱い1文目を禁止例として明示）(2)短いほど伸びる・基本150字前後/最大250字 (3)1投稿1メッセージ (4)短文フック型を多めに。生成の字数上限を旧500字→短文方針に変更。コード側の例文は事業中立に（公開repo §17b 遵守）。
- **`fill_week_schedule.py`**：`PRESETS` を `schedule.py` から import（重複定義を解消）。`--start-today`（当日開始＋現在時刻以前除外。days日モードで day0 端数＋以降満日）を追加。
- **初回サイクルの実シート投入（06-26〜28・スクリプトは repo 外 scratchpad）**：
  - **占い**：旧 queued/draft 32本（弱い1文目の挨拶定型で始まる長文）を **status=retired** へ退避（publisher は status∈{空,queued} のみ公開＝retiredは非公開）。Workflowで**強フック・短文（88-122字）**の新18本を生成→**機械コンプラゲート18/18合格**→先頭11本を **queued**（06-26 17時台〜・午前/夕方/夜の4本/日）＋残り7本を **draft 在庫**。
  - **製造業**：内容は良好なので維持。06-26夕/27/28の queued 11本はそのまま、**06-29以降(B04〜B20＋空のB11)17本を draft 退避**して自動化(06-28実行)の生成と衝突回避。
  - 投入は**タブ1回読み→batch_update＋append_rows の quota効率版・冪等**（per-row update_post は Read/min 429 に当たるため。§19と同方針）。
- **テスト**：`test_schedule.py` に days日モード/当日開始/過去除外・占いプリセット（午前1＋夕方-深夜3・最低間隔30分・200seed）の2件、`test_phase2.py` に 3日サイクルゲート（月跨ぎ）/`n_posts_for`（4本/日=12・上書き）/uranai schedule_fn（午前+夕方夜）の3件を追加。全スイートPASS。
- **要・運用反映（HITL）**：自動の3日サイクルを回すには `weekly.yml` を **enable**（現状 disable 想定）＋`GENERATE_POSTS=1`＋`ANTHROPIC_API_KEY`＋`GEN_STATUS`（占い/製造業を自動公開にするなら queued）。占いは過去BANリスク（霊感商法/景表法）に留意し、初回は queued で本日夕方公開。

---

## 25. 改修ログ（2026-06-27）3事業目＝占い新アカ「澪（みお）」のスカフォールディング追加

実勝ち投稿42枚の徹底分析（占いThreads-note事業/手動 投稿リサーチ/分析_20260627）を経て、既存「結（縁結びの巫女・恋愛特化）」とカニバらない**3事業目**を立ち上げ中。コンセプト＝**「巡りを読む人 澪」＝暦・月・星の"巡り"で決断のタイミングを告げる、性別年齢を明かさない中性的な語り部**（マネタイズ＝note月額マガジン中心）。

- **事業キー `meguri`**：`BIZ_LABEL`（占い（澪））／`SCHEDULE_FN_BY_BUSINESS`（`partial(build_schedule, **PRESETS["meguri"])`）／`THEME`（uranai色流用）に追加。`n_posts_for` は `SCHEDULE_FN_BY_BUSINESS` 在籍で自動的に 3日×4＝12本（`GEN_POSTS_MEGURI` で上書き可）。
- **朝型スケジュール（新スロット系統）**：`schedule.py` に **`daily_slots_windows(rng, windows, min_gap)`**＝複数の時間帯ウィンドウに各1本ずつ配置（昼1＋夜N の `daily_slots_minutes` とは別系統）。`build_schedule` は `_slots_for` で「windows があればウィンドウ型／無ければ昼夜型」に分岐。`PRESETS["meguri"]`＝朝6:30-8:30／昼11:30-13:00／夕17:00-19:00／夜21:00-23:00 に各1本（暦は朝の縁起日告知が映える）。
- **甘めガイドライン（重要・ユーザー指示）**：勝ち投稿は強い**断定・言い切り・常識否定**で当たる→厳しくすると当たらない。「ブランド純度の禁止」と「法律の硬い線」を分離し**後者だけ**守る。生成ゲートのNGワードは**最小ハード法務のみ**＝`治る/完治/効く/稼げる/儲かる/不労所得`（医療効果・金銭保証）。`必ず/絶対/100%/当たる` は**入れない**（断定トーンを殺すため）。霊感商法/有料商品の効果保証/誇大はガイドライン本文（LLMが読む）＋人で抑止する二段構え。
- **コンテンツ生成済み（ローカル＝立ち上げフォルダ）**：`占いThreads-note事業/澪_新アカ立ち上げ/`＝コンセプト設計／ナレッジ／プロフィール／甘めガイドライン／**ローンチ投稿26本**（甘め検証＋ハードNGゲート全合格・129-168字）／bio2案＋固定投稿。生成は Workflow（5本柱×勝ちフック型→敵対的甘め検証）。
- **専用スプレッドシート新規作成**（morll所有・IDは公開repoに書かない＝memory/scratchpadで管理＝§17b/§18）。タブ構築＋curation＋26投稿(draft在庫)投入は **scratchpad の `build_mio_sheet.py`**（SA共有後に `--apply`・冪等）。**SAへの共有がブロッカー**（claude.ai Drive MCP に権限付与APIが無く、ユーザー手動共有が必要）。
- **コードは inert**：`meguri` を `BUSINESSES` secret に入れるまで何も動かない（=スカフォールディングのみ安全に commit）。**起動の残（HITL）**：①ユーザーが新Threadsアカ作成＋トークン取得（§15手順）②シートをSAに共有→`build_mio_sheet.py --apply`③accounts にトークン＋`BUSINESSES` に3事業目追加④初回サイクルを fill（`--preset meguri --start-today`）。暦の正確性（縁起日/節気の実データ参照）は今後の要対応事項。
- **テスト**：`test_schedule.py` に `daily_slots_windows`（澪4窓・200seed）と build_schedule×meguri preset の2件追加。全スイートPASS。

---

## 26. 改修ログ（2026-07-03）4事業目＝製造業・共感認知型「住田」（seizogyo2）追加

現行 `seizogyo`（たくみ/ナビ＝当事者キャラ×情報メディア）とは**別戦略**の製造業アカウント第2弾。コンセプト＝**「共感を軸にバズらせて認知拡大→後段で送客」**（keita共有の高消費回数スレッド群がモデル）。ポジション＝「住田｜製造業専門家」（ブルーカラー専門転職エージェントの専門家キャラ・@tenshokuman.15）。送客モデルは seizogyo と同一（提携紹介会社へ成果報酬）。

- **事業キー `seizogyo2`**：`SCHEDULE_FN_BY_BUSINESS` に追加。`n_posts_for` は在籍で自動的に 3日×4＝12本（`GEN_POSTS_SEIZOGYO2` で上書き可）。
- **スケジュール**：`PRESETS["seizogyo2"]`＝windows型（澪と同系統）。工場勤務者の生活リズム4窓＝朝6:30-8:30（通勤）／昼11:30-13:00（休憩）／夕17:30-20:00（帰宅後）／夜21:00-23:30（寝る前）に各1本・最低間隔30分。
- **専用スプレッドシート「Threads運用｜製造業2」**（morll所有・IDは公開repoに書かない＝§17b）。SAはDriveクォータ0でファイル作成不可（Google仕様変更）→ Drive MCP でユーザー所有として作成し、**SAへの共有はユーザー手動**（§25の澪と同じブロッカー/解決）。
- **ガイドライン タブは現行 seizogyo シートから複製**＝同じ製造業求人なので違反ライン（NGワード・法令・過去2回BANの教訓）を単一基準で共有。プロフィール/ナレッジは新アカ専用（共感認知型・専門家ポジション）。
- **戦略・投稿のローカル正本**：`製造業Threads/02_新運用_共感認知2アカ/`（設計＝`strategy/00_戦略設計_共感認知型.md`・R1立ち上げ12本は Workflow「型抽出→起草→3視点敵対的検証→修正」で制作）。コンプラ正本は 01 を参照（複製しない）。同時に旧 `02_過去案件_リライズupS_アーカイブ` フォルダを `09_アーカイブ_過去案件_リライズupS` へ改名（新運用に02番を割当）。
- **テスト**：`test_schedule.py` に seizogyo2 窓検証（200seed）＋ build_schedule×preset の2件追加（40→42本・全PASS）。
- **アカウントキー `tenshokuman`**（タブ=`投稿_tenshokuman` 等・手動row_id接頭辞 `ten-`）。user_id等の実データはシートとローカル `02_.../data/システム接続情報.md` のみ（公開repoに書かない）。

### 26追補（2026-07-03）住田R1公開＋メール複数宛先＋二重投稿防止
- **メール複数宛先対応**：`mailer.send_message` が To をカンマ分解して SMTP エンベロープに全員渡すよう修正（従来は `[to]` 1件＝複数指定時に不達）。`_envelope_recipients` 追加＋テスト（test_phase2 43本）。Variable `MAIL_TO` にカンマ区切りで複数宛先を設定可能に（**宛先の実アドレスは Variable 側にのみ置き、公開repoには書かない**＝§17b）。
- **二重投稿防止の穴を修正**：`weekly.yml` に `GEN_POSTS_SEIZOGYO2`（既定 `0`＝生成オフ）を配線。未配線だと n_posts_for が既定12に落ちて立ち上げ期に自動生成が走り手動R1と衝突していた。`main_weekly` は `GEN_POSTS_<NAME>=0` で当該事業の生成をスキップ（分析・レポートは継続）。
- **住田R1公開GO**：立ち上げ12本を **queued** 化（07-04〜07-09・1日2本）。ハッシュタグは全削除（見本準拠・広告色低減）。自動生成を始めるときは Variable `GEN_POSTS_SEIZOGYO2` を 12 にする。

### 26追補2（2026-07-03）週次レポートの事業別宛先ルーティング
- **要件**：既定宛先＝全事業のレポート／別の宛先＝seizogyo2（住田＋今後の2アカ目）のみに追加配布。
- **実装**：`main_weekly.recipients_for(name, env, default_to)`＝`MAIL_TO_<NAME>` があればその事業だけ宛先差し替え、無ければ既定 `MAIL_TO`。email_reports に `business` を付与し送信直前に `rep["to"]` を解決。`send_account_reports` は `rep["to"]` 優先＋per-report宛先ログ。weekly.yml に `MAIL_TO_SEIZOGYO/URANAI/MEGURI/SEIZOGYO2` を配線。
- **設定**：Variable `MAIL_TO`（全事業既定）＋`MAIL_TO_SEIZOGYO2`（seizogyo2のみ別宛先を追加）。他事業（seizogyo/uranai）に別宛先は漏れない（テストで担保）。**宛先の実アドレスは Variable 側にのみ置き、公開repoには書かない**（§17b）。
- テスト2件追加（recipients_for のルーティング＋漏れなし／send_account_reports の per-report宛先）＝45本全PASS。

### 26追補3（2026-07-07）seizogyo2 に2アカ目 `pashi` 追加（本音暴露型・建設施工管理特化）
- **アカ2＝ぱし|転職先生のホンネ（pashi_tenshokusensei）**を seizogyo2 事業に追加（同一シート・同一フォルダ `02_新運用_2アカ`）。アカ1住田(共感認知型)とは別軸＝**本音暴露/給料公開/逆張り議論**型・建設/施工管理派遣特化・既存266フォロワー＆公式LINEで相談送客。
- セットアップ＝`setup_post_tab`/`add_validation_ja`/`setup_account`（accountsにトークン登録）＋`プロフィール_pashi`/`ナレッジ_pashi`（型ライブラリを sync_knowledge）。ガイドラインは共有（同シートの既存タブ）。
- **R1 12本を Workflow（型抽出→起草→3視点敵対検証〈職安法/信用毀損差別/品質〉→修正・指摘9件反映critical0）で制作→機械ゲート12/12合格→queued**（07-08〜07-13・昼12:xx/夕18-19:xx）。**CIB分離**：住田=朝07:xx/夜23:xx、ぱし=昼/夕で3h以上ずらす。ハッシュタグなし。
- pashi特有コンプラ＝実在派遣会社の名指し貶し・属性差別・具体求人化を検証で排除（既存の「洗脳しやすい」等の芸風は再生産せず、給料公開/逆張り/相談回答の合法な芸風に寄せた）。
- 生成は引き続き `GEN_POSTS_SEIZOGYO2=0`（手動R1・二重投稿防止）。メール宛先は seizogyo2 の設定（既定＋別宛先）のまま＝実アドレスは Variable のみ（§17b）。エンジンのコード変更なし。
- フォルダ名変更：`02_新運用_共感認知2アカ`→`02_新運用_2アカ`（2アカ体制の中立名）。

### 26追補4（2026-07-08）ぱしを seizogyo3（独立シート）へ分離＝アカウント別シート＋別スケジュール
- **方針転換**：今後 seizogyo2 系は「自動生成＋分析」を回し、かつ**アカウントごとに投稿時間を変えたい**という要件。1シートに2アカだと schedule PRESET が事業単位で共有されるため、**アカウント＝1事業＝1シート**に分離してブラスト半径と per-account スケジュールを独立させた。
- **seizogyo2＝住田のみ／seizogyo3＝ぱし**（新シート「Threads運用｜製造業3」・morll所有・SA共有）。移行は posted 履歴ゼロのタイミング（初回公開前）に実施＝完全クリーン。順序＝seizogyo3構築(inert)→seizogyo2からぱし削除→BUSINESSESにseizogyo3追加、で**二重投稿ゼロ**を担保。
- **per-account スケジュール**：`PRESETS["seizogyo2"]`（住田＝朝夜寄り4窓）と `PRESETS["seizogyo3"]`（ぱし＝昼夕寄り4窓）を**窓が一切重ならない**よう設計（同一運用者2アカのCIBニアミス防止）。`test_seizogyo2_3_windows_do_not_overlap` で非重複を機械保証。`SCHEDULE_FN_BY_BUSINESS` に seizogyo3 追加。
- weekly.yml に `GEN_POSTS_SEIZOGYO3`（既定0＝生成オフ）＋`MAIL_TO_SEIZOGYO3` を配線。Variable も設定（生成オフ・レポート宛先は seizogyo2 と同じ）。テスト45→47本全PASS。
- ぱしR1 12本は seizogyo3 に queued 再投入（07-08〜13・昼/夕）。seizogyo2 側のぱし関連タブ・accounts行は削除済み。実アドレス/トークン/シートIDは公開repoに無し（§17b）。

---

## §27 全アカウント投稿停止（2026-07-28 発覚）— 原因・対応・再発防止

### 何が起きたか

**稼働していた4アカウント全てが、投稿在庫ゼロで停止していた。**

| アカウント | 事業 | 最終投稿 | 停止日数 |
|---|---|---|---|
| takumi_kojo_navi | seizogyo | 2026-07-19 21:53 | 9日 |
| miko_yui_musubi | uranai | 2026-07-19 22:45 | 9日 |
| tenshokuman（住田） | seizogyo2 | 2026-07-09 23:24 | 19日 |
| pashi（ぱし） | seizogyo3 | 2026-07-13 18:39 | 15日 |

### 原因

1. **Anthropic APIのクレジット残高切れ。** 3日サイクルの生成が JST 07-19 / 07-22 / 07-25 / 07-28 と
   **4サイクル連続で失敗**（`credit balance is too low` / HTTP 400）。最後に生成できたのは 07-16 で、
   その run の途中（takumi・miko の生成後、pashi の方針生成の時点）で残高が尽きている。
   3日サイクルで3日分ちょうど作る設計のため、1回の失敗が即在庫切れになった。
2. **seizogyo2 / seizogyo3 は `GEN_POSTS_*=0`（生成オフ）のまま。** 立ち上げ期の手動R1バッチと
   自動生成の二重投稿を防ぐための**意図した設定**だが、手動12本を撃ち尽くしたあと
   人が明示的に変えるまで二度と再開しない構造だった。

### なぜ9〜19日も気づけなかったか（本質）

**通知が無かったのではなく、鳴っていた警報が埋もれた。**

- GitHub純正の失敗メールは在庫が尽きた当日（07-19）から3日おきに4回鳴っていた。
  しかしそれは**正常な週次レポートメールと同じ受信箱**に届くため埋もれた。
- 週次ワークフローは毎日走るが3日に2日はサイクル対象外で即 return 0＝緑。
  9日間で緑8・赤4となり、Actions一覧が赤一色にならなかった。
- **決定打**：投稿ジョブは**在庫0件でも「成功」で終わる**。停止後も約1,290回「成功」し続けた。
- 週次レポートは全期間累計だったため、投稿が止まっても数字がほとんど変わらず異常が見えなかった。

### 対応（このコミット群）

**1. 週次レポートを「週次」に修正**
- `analyzer.analyze_windowed()`＝直近7日（KPI・TOP5）／前7日（前期比）／28日（傾向分析）／累計（参考値）。
  期間フィルタが無く常に全期間累計だったため、TOP1投稿が6回連続で同じもの、
  「朝の時間帯に寄せる」という同じ助言が13サイクル連続で出ていた。
  傾向分析だけ28日窓なのは、7日だと曜日あたり1本になりサンプル不足でノイズに振り回されるため。
- KPI説明文と実装の不一致（「今週の…」と書きながら全期間累計）を解消。レポートに集計期間を明示。
- `Analyzer.now_fn`（受け取るだけの死にパラメータだった）を期間窓の基準日として実際に使用。

**2. 運用状態を必ず目に入る位置に出す**
- レポート冒頭に「運用状態」バンド＝**在庫ランウェイ／フォロワー増減／生成の成否**。
- 在庫ゼロまたは生成失敗のときメール件名に【要確認】を付与。

**3. 在庫ランウェイの日次監視を新設（再発防止の本丸）**
- `threads_poster/inventory.py`（純関数）＋ `main_monitor.py` ＋ `monitor.yml`（日次 08:00 JST・読取専用）。
  監視対象を「ジョブが落ちたか」から**「投稿が出せる在庫があるか」**へ移す。
- 異常時のみメール（正常なら送らない＝毎日の無害メールで通知が麻痺するのを防ぐ）。
  宛先ごとに1通へまとめ、件名は【要確認】。
- 在庫ゼロの間は exit 2 で run を赤くし、メールが埋もれても Actions 一覧で気づけるよう二重化。

**4. 失敗理由を見えるようにする**
- `threads_poster/errors.py`＝残高不足 / 認証エラー / レート制限 / 一時障害 / 入力エラー に分類。
  4回連続の失敗が全部同じ exit 2 にしか見えず、原因の切り分けができなかったため。
- 生成をレポート生成より**前**に実行し、例外をアカウント単位で受け止める。
  生成が失敗してもレポートとメールは最後まで出て、失敗理由がレポート本文に載る。
- 方針(strategy)生成の失敗も理由を表示（従来は黙って None＝方針セクションが理由なく消えていた）。

**5. あわせて直したもの**
- **公開repoのActionsログにトークンが出うる穴**：トークンはURLのクエリで送っているのに通信呼び出し
  8箇所すべてに例外処理が無く、瞬断時の例外に `...access_token=<本物>` が入る構造だった。
  `threads_api.http_request()` を唯一の入口にし、access_token / client_secret をマスク。
  例外チェーンは繋がない（元例外のargsにURLが残り再露出するため）。素のrequests呼び出しが
  1箇所だけであることをテストで機械保証。
- **キルスイッチ `PAUSED` が投稿側で効いていなかった**（post.yml の env に無かった）。配線。
- **`GEN_STATUS` の事業別化**（`GEN_STATUS_<NAME>`）。製造業は過去2回BAN済みなのに
  占いと同じ「無確認自動公開」しか選べなかった。
- **依存の未固定**：requirements.txt にメジャー上限を付け、`anthropic` を明示追加
  （記載すら無く weekly.yml が毎回ノーピンで最新版を取得していた）。
- **Node20非推奨**：checkout@v4→v5 / setup-python@v5→v6。放置すると 2026-09 に全自動化が停止した。
  全ワークフローに `permissions: contents: read` を明示。
- **§17b違反**：公開repoに残っていた実メールアドレス5箇所を削除（Gmail送信アカウントのユーザー名でもあった）。
- **認可スコープ**：`get_auth_url.py` に `threads_manage_insights` を追加（§19の取り直しの再発防止）。
- `.claude/settings.json`（第三者スクリプトを `curl | sh` する定義）を公開repoから削除。

### 占い「結」（uranai）の廃止

実測で **156投稿の累計表示227回（平均1.5・44本が表示ゼロ・最高13）**、フォロワー48人にすら
届いていなかった（同じ仕組みの pashi は平均797、takumi は200）。コンテンツ品質ではなく
アカウント側の配信抑制が疑われるため、人格・アカウント名ごと廃止し新アカウントへ切り替える方針。
- BUSINESSES から `uranai` を除外、未公開の draft 7本を `retired` 化。
- ナレッジ48,825字・プロフィール・ガイドライン・投稿ログ・インサイトはローカルへ保全
  （`占いThreads-note事業/結_アーカイブ_20260728`）。ガイドラインは占い事業共通なので新アカでも流用可。

### 残作業（人の判断が要るもの）

- Anthropic クレジット購入＋Auto-reload 有効化 → `threads-weekly-report` 手動実行で takumi 復旧。
- seizogyo2 / seizogyo3 の自動生成をONにするか（`GEN_POSTS_SEIZOGYO2/3` を 12 に）。
- 新しい占いアカウントのコンセプト・名前の決定。
- 澪（meguri）のトークン取得（起動手順は非公開ローカルの手順書）。

---

## §28 改修ログ（2026-08-31）生成AIの事前疎通チェック（preflight）追加 — §27の障害は未解決のまま1ヶ月経過

### 実測でわかったこと

§27（2026-07-28発覚）の全アカウント停止は、**1ヶ月以上たった 2026-08-31 時点でも解決していない**。
Actions の実ログで確認した停止日数は以下（uranai=結 は §27 で廃止済みのため対象外）:

| アカウント | 事業 | 最終投稿からの日数（08-30時点） |
|---|---|---|
| takumi_kojo_navi | seizogyo | 42日 |
| tenshokuman（住田） | seizogyo2 | 52日 |
| pashi（ぱし） | seizogyo3 | 48日 |
| mio__meguri（澪） | meguri | 20日 |

- **澪は予定通り起動していた**：07-28に `BUSINESSES` へ追加され、ローンチ26本（07-29〜08-10）を
  公開し切った。ただし `GEN_POSTS_MEGURI=0`（生成オフ）のままなので在庫が補充されず、以後停止。
- **監視は正しく鳴り続けていた**：`monitor.yml` は毎日 exit 2 で赤くなり【要確認】メールを送っていた。
  つまり §27 で入れた再発防止策は機能しており、**検知できない問題ではなく、鳴っている警報に
  対応されていない**状態だった。

### なぜ確認が後回しになったか（今回の学び）

**「請求の未納がない」と「APIクレジット残高がある」は別物**だが、これが混同されやすい。
Anthropic API は前払いのクレジット残高方式で、Claude Code のサブスクとは別会計。
支払いが滞っていなくても、チャージ分を使い切れば `credit balance is too low` で止まる。

さらに悪いことに、**「今動くか」を確かめる手段が実質存在しなかった**:
- 週次は3日サイクルゲートがあり、サイクル日以外は即 return 0（緑）。次の判明まで最大3日待ち。
- 本番の週次を手動で撃つと `GEN_STATUS=queued` のため生成分がそのまま公開される。
  BAN歴2回の製造業アカウントを抱える運用で、確認のためだけに撃つのは危険。

### 追加したもの

**`main_preflight.py` ＋ `.github/workflows/preflight.yml`（手動実行専用）**
- `max_tokens=1` の最小リクエストを **1回だけ** 投げ、生成AIが叩けるかだけを見る。
- **副作用ゼロ**：シートを読まない/書かない・メールを送らない・投稿を1本も作らない。
- 失敗理由を `errors.py` で「残高不足／認証エラー／レート制限／一時障害／入力エラー」に分類し、
  残高不足なら購入手順まで案内する。終了コード 0=正常 / 1=設定不足 / 2=APIが使えない。
- **本番と同じ `GEN_MODEL` を使う**ので、モデルIDが古く/無効になった場合もここで判明する。
- 公開repoのActionsログ対策として `sk-ant-` 形式の鍵をマスク（§17b）。
- テスト5本追加（82→87本）。

**運用への組み込み**：「生成が動くか」の確認は今後 `preflight.yml` の手動実行が正本。
「投稿在庫があるか」は従来どおり `monitor.yml` / `main_monitor.py`。役割を分けた。

### preflight の初回実行結果（2026-08-31 04:29 JST）

```
❌ 生成AIが使えません（原因=残高不足・model=claude-opus-4-8）
詳細: Error code: 400 - 'Your credit balance is too low to access the Anthropic API.
      Please go to Plans & Billing to upgrade or purchase credits.'
```

→ **残高は未回復。クレジット購入が済むまで生成・在庫補充は一切再開しない。**

### 残作業（人の判断が要るもの・§27から未消化）

- **Anthropic クレジット購入＋Auto-reload 有効化**（最優先。これ以外は全部この後）。
  購入後は `preflight.yml` を手動実行して緑を確認 → `FORCE_CYCLE=1` で週次を回して在庫復旧。
- 次の3日サイクル日は **2026-09-02**（起点 2026-06-28 から3日周期）。放置した場合の自動復旧はこの日。
- 澪の `GEN_POSTS_MEGURI` を 0 のままにするか自動生成に切り替えるか判断（現在0＝在庫が増えない）。
- seizogyo2 / seizogyo3 の自動生成をONにするか（`GEN_POSTS_SEIZOGYO2/3` を 12 に）。
- **新規所見**：`weekly.yml` が Google Sheets の `429 Read requests per minute` で
  事業単位の処理を落とすことがある（08-29実行では pashi が該当し exit 2 の一因）。
  事業が4つに増えて読み取り量が上限に近づいている可能性があり、残高復旧後に顕在化しうる。
