# CLAUDE.md — Threads自動投稿システム 引き継ぎ

このファイルはClaude Codeが起動時に自動参照する。**作業を再開する前に必ず全文を読むこと。**

> ⚠️ **最重要・全状況で遵守**：このrepoは**公開**。何を公開してよく・絶対ダメかは **§17** を参照。ターミナル新規作業・自動化・ユーザーからの分析/作業依頼など**あらゆる場面で§17を識別・認識すること**。

---

## 0. このプロジェクトの目的

スプレッドシートに投稿を並べておくと、**複数のThreadsアカウントへ時間指定で自動投稿**するシステム。
ツリー（リプライ連結）対応。サーバ管理ゼロ（GitHub Actions の無料cron で稼働）。
最終的にはオーナー（Master）のSNS運用代行業の「再利用可能な納品資産」にする。

これは3層構想の **第1層**。第2層（Threads→LINE導線）、第3層（LINE上でClaude自動鑑定→数時間後配信→購入後は人が対応）は後続フェーズ。§9を参照。

---

## 1. アーキテクチャ

```
スプレッドシート（投稿キュー＋アカウント/トークン保管庫）
        ↑ 読む / 結果(status, posted_id, トークン更新)を書き戻す
GitHub Actions（10分おき cron / 手動実行可）→ main.py → Threads API（公開）
```

設計判断と理由（変更時はここを尊重すること）:
- **状態は全てスプレッドシートに集約** → GitHub Actions側を完全ステートレスにできる（Actionsのファイルシステムは毎回破棄されるため）。
- **GitHub Actions cron** → VPS不要・無料。`concurrency` で直列化し多重起動による二重投稿を防止。
- **cronはUTC、投稿時刻判定はシート側のJST** で行う（混同しないこと）。
- **安全装置を既定内蔵** → 1日上限は公式250より低い50（バースト投稿の凍結リスク回避）、冪等性（postedは再投稿しない）、親未公開なら子は次回に保留。

---

## 2. 現在の状態（DONE / 検証済み）

実装・テスト完了済み。`python test_logic.py` で以下が全PASS（API不要のモックで検証済み）:
- ツリーの親子連結が正しい（子の `reply_to_id` = 親の公開後ID、孫 = 子のID）
- 公開時刻の判定（未来の行は公開しない）
- アカウント別の1日カウント
- **親が未公開なら子は保留（deferred）し誤投稿しない**
- レート制限（上限超過しない）
- **冪等性**（再実行で二重投稿しない）

`import` も全モジュール通過確認済み。

## 2b. 実機セットアップ状況（2026-06-14 本番稼働開始）

> ★重要: **自分所有のアカウントは「開発モード」で App Review なしに実投稿できる**（アプリの "Threadsテスター" に投稿用アカウントを追加・承認するだけ）。App Review が要るのは第三者運用／販売（Phase E）だけ。

- [x] Google サービスアカウント作成 ＋ 空シート共有（Phase A）
- [x] Meta アプリ作成（`threads_basic`/`threads_content_publish`）＋ 投稿アカウントを **Threadsテスター** に追加・承認
- [x] 長期トークン取得 → accounts タブへ。※実機では **アプリの「ユースケース→カスタマイズ→設定→ユーザートークン生成ツール」が最短**（OAuth code 方式 `get_auth_url.py`→`exchange_token.py` も可）。**テスター招待の承認は Threadsアプリ側「設定→アカウント→ウェブサイトのアクセス許可」**で行う（Meta開発者画面ではない＝最大のハマりどころ）。リダイレクトURIは「カスタマイズ→設定」内で、入力後にドロップダウン候補をクリックして確定が必要。
- [x] GitHub へ push ＋ Secrets 登録 ＋ Actions 有効化 → **private repo `kyon0802/threads-auto-poster`**、10分cron稼働中
- [x] 本番の実投稿テスト成功（2026-06-14、account=`takumi_kojo_navi`〔旧handle `rk_riko2`、2026-06-18にThreads側で改名。user_id/トークンは不変〕）
- [ ]（将来）第三者運用／販売フェーズで `threads_content_publish` の **App Review** を申請（Phase E）

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
- **制限**: 公開上限 250/24h/ユーザー（アカウント単位、アプリ単位ではない）。
- **その他**: ネイティブ予約機能なし（自前cron必須）／投稿の編集不可／メディアは公開到達可能なURL必須（直アップ不可）／本番publishはApp Review必須。

---

## 4. ファイル構成

```
main.py                       実行エントリ（環境変数を読みPublisher.run()）
bootstrap_token.py            初回の短期→長期トークン交換ヘルパー
threads_poster/
  threads_api.py              ThreadsClient（container/status/publish/refresh、CAROUSEL対応）
  sheets.py                   Store抽象 + GoogleSheetStore(本番・batch書込) + MemoryStore(テスト)
  publisher.py                公開ロジック本体（時刻判定/ツリー/レート制限/トークン更新/write-ahead）
scripts/
  setup_sheet.py              Phase A: タブ/ヘッダ自動生成＋接続診断
  get_auth_url.py             Phase B: OAuth認可URL生成
  exchange_token.py           Phase B/C: 認可code→長期トークン取得
  setup_account.py            トークンから user_id 取得→accounts へ追記/更新（任意でテスト投稿）
  setup_post_tab.py           アカウント別タブ「投稿_<account>」を作成/整備＋記入例タブ生成
  migrate_headers_ja.py       既存シートの見出しを日本語化（データ保持）
  add_validation_ja.py        投稿タブにドロップダウン/日時形式チェックを付与
  local_run.sh                .env読込→DRY_RUN既定でローカル実行
  batch_to_csv.py             content→sheetブリッジ（立ち上げバッチMd→posts CSV＋スケジュール生成）
test_logic.py                 ロジック検証テスト（API不要・モック・全6ケース）
sheet_templates/              accounts.csv / posts.csv / posts_example.csv（記入例。CAROUSEL例含む）
.github/workflows/post.yml    10分おき cron（MAX_POSTS_PER_DAY はリポジトリ Variable）
requirements.txt / .env.example / README.md / SETUP.md
```

各層の責務:
- `threads_api.py` … HTTPとAPI仕様の知識のみ。状態を持たない。
- `sheets.py` … データの読み書き。`Store` インターフェースを実装すれば保管先を差し替え可能（DB化はここ）。
- `publisher.py` … ビジネスロジック。`client_factory` と `now_fn` を注入できるのでテスト容易。`dry_run=True` で無投稿実行。

---

## 5. スプレッドシート スキーマ（2026-06-15 日本語化・アカウント別タブ化）

> 真実は `threads_poster/sheets.py`（`*_FIELD_ALIASES` / `per_account_post_headers` / `POSTS_TAB_PREFIX`）。見出しは**日本語(正規)でも英語(旧名)でも読める**（エイリアス層）。タブ名（`accounts` / `投稿_*`）は変えない。見出し行も消さない。

**`accounts` タブ**（日本語見出し / 旧英語も可）:
`アカウント`(account) / `ユーザーID`(user_id) / `アクセストークン`(access_token) / `トークン更新日時`(token_updated_at) / `本日投稿数`(daily_count) / `カウント日付`(daily_count_date)

**投稿タブ = アカウントごとに分割**。タブ名 **`投稿_<アカウント名>`**（例 `投稿_takumi_kojo_navi`、`<アカウント名>`は accounts の `アカウント` と一致）。**アカウントはタブ名から自動判定するので「アカウント」列は持たない**。見出し:
`投稿ID`(row_id) / `投稿日時`(post_datetime, JST `YYYY-MM-DD HH:MM`・文字列書式) / `本文`(text) / `メディア種類`(media_type, ドロップダウン TEXT/IMAGE/VIDEO/CAROUSEL) / `メディアURL`(media_url) / `返信先ID`(reply_to=親の投稿ID) / `返信できる人`(reply_control) / `状態`(status, ドロップダウン) / `投稿後ID`(posted_id) / `投稿実施日時`(posted_at) / `エラー`(error)

- **後方互換**: 単一 `posts` タブ（`アカウント`列あり）も読める。`投稿_*` タブが1つも無いときだけ `posts` を見る。
- `状態` は空 or `queued` で投入 → システムが `posted`/`error` を書き戻す。公開直前に `publishing`（write-ahead 二重投稿防止）。`publishing` のまま残った行は中断痕＝**Threads側を確認してから**空に戻す。
- メディアは `メディアURL` に**公開到達可能な直リンク必須**（直アップ不可・セルへの画像添付は不可＝Threadsがそのリンクから取得する）。CAROUSEL は URL を**カンマ区切りで2件以上**。
- **`記入例` タブ** … 通常投稿＋ツリー例の見本（コードは読まない）。`メディア種類`/`状態`=ドロップダウン、`投稿日時`=形式チェック付き。
- **2アカ目以降**: `scripts/setup_post_tab.py --account <名>` でタブ生成 → `scripts/add_validation_ja.py --tab 投稿_<名>` で入力支援付与 → `accounts` にトークン行（`setup_account.py`）。

---

## 6. 環境変数 / Secrets

| 変数 | 用途 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントJSON全文 |
| `SPREADSHEET_ID` | スプレッドシートID |
| `TZ_NAME` | 既定 Asia/Tokyo |
| `MAX_POSTS_PER_DAY` | 1アカウント1日上限（既定50） |
| `DRY_RUN` | "1" で無投稿実行（検証用） |
| `THREADS_CLIENT_SECRET` | bootstrap_token.py 実行時のみ |

ローカル検証: `DRY_RUN=1 python main.py` / ロジック検証: `python test_logic.py`

---

## 7. 次にやること（ロードマップ / 優先順）

> **状況（2026-06-14）: 手順1〜4は完了し本番稼働中**（§2b・§12）。残るは「コンテンツ投入」「アカウント横展開」、安定後の「テンプレ化／販売(Phase E)」。
> 自前運用は **開発モード（テスター追加）で審査なしに開始できる**。App Review は第三者運用／販売（手順5・Phase E）まで不要。

1. **データ層**: Googleサービスアカウント＋空シート共有 → `scripts/setup_sheet.py` でタブ／ヘッダ自動生成 → `scripts/local_run.sh`（DRY_RUN=1）で疎通。
2. **トークン**: Metaアプリ作成＋投稿アカウントをテスターに追加・承認 → `get_auth_url.py` → 承認 → `exchange_token.py` で長期トークン取得 → accounts タブへ。
3. **コンテンツ投入**: `threads-compliance` スキルで✅判定済みの本文だけを posts タブへ。R1 等の定型バッチは `scripts/batch_to_csv.py` で posts CSV に変換できる（2アカ3hずらしの投稿スケジュールも自動生成。NGワードの最終防壁つき）。
4. **自動化**: GitHub へ push → Secrets（`GOOGLE_SERVICE_ACCOUNT_JSON`／`SPREADSHEET_ID`）登録 → Actions有効化 → `workflow_dispatch` で手動疎通 → 10分cronで自動運用。1日上限はリポジトリ Variable `MAX_POSTS_PER_DAY`（未設定なら50）で調整。
5. 安定後、`post.yml` とコードをテンプレ化し他クライアントへ複製。**第三者運用／販売の段階で初めて App Review を申請**（用途＝自社／クライアント求人の Threads 予約自動配信）。

---

## 8. 未決定事項（Masterに確認すべき）

- ~~第1垂直をどれにするか~~ → 解決: **ジャンル非依存の汎用エンジンとして運用**（特定垂直に固定しない。2026-06-14決定。投稿は都度シートに投入。製造業/占い等の実コンテンツは別フォルダの別プロジェクト）。
- トークンのシート保管はセキュリティ上の妥協。アカウント数が増えるなら `Store` をDB（Supabase/SQLite等）実装に差し替える判断。
- `MAX_POSTS_PER_DAY` の最終値（凍結回避と物量のバランス）。

---

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
- `posts` の `status=error` 行は、原因解消後に `status` を空に戻せば次回再試行される。
- 親子（ツリー）は親を必ず先の時刻に。親が同回で公開→子は同回 or 次回に自動連結。
- `status=publishing` のまま残った行は公開処理の中断痕。実際に投稿されたかThreadsで確認してから空に戻す（無確認で戻すと二重投稿の恐れ）。
- `DRY_RUN=1` はシートを**一切書き換えない**（公開対象の検出と疎通確認のみ）。実投稿後の書き戻し挙動を確かめたいときは本番（DRY_RUNなし）で。
- `MAX_POSTS_PER_DAY` は GitHub Actions ではリポジトリ Variable `vars.MAX_POSTS_PER_DAY` が単一の真実（未設定なら50）。ローカル実行は `.env`。post.yml にはハードコードしない。
- シート書き戻しは1行分を1回のAPI呼び出しにまとめている（クォータ節約・`update_cells` RAW）。

---

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
- **失敗メール通知を有効化（2026-06-18）**: GitHub純正のActions失敗通知を利用。投稿エラー時は `main.py` が exit code 2 → run failure 扱い → メール送信。受信先 `morll.0802@gmail.com`（Settings→Notifications→Default notifications email＋System→Actions=Email/"failed workflows only"）。実テスト済み。※長時間ブロック時は10分毎にメールが来るため、将来シートでalert抑制する案あり。
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

### 17e. ★情報抜け防止メカニズム（curationは手作業＝抜けが起きうるので機械ゲートで担保）
curation（ローカル知識→シート）は手作業なので、放置すると「重要な規約が抜けたまま生成」が起こりうる。これを**4重で防ぐ**（Phase 2 で実装）：
1. **必須タブ存在ゲート（generator 起動時に強制）**：各アカウントで `プロフィール_<acc>` と 事業 `ガイドライン` が**存在＋非空**でなければ generator は**生成を中止**（loud fail＋レポート/メール通知）。空・欠落のまま盲目生成させない。＝「抜け→静かに低品質投稿」を「抜け→生成停止＋通知」に変える。
2. **反映元レジストリ（明文化）**：「どのローカルファイル→どのタブ」を §17d / PRD #2 に固定。curation時はこの一覧を**必ず網羅**（チェックリスト化）。
3. **ユーザー確認（意味的完全性）**：初回curation後、`ガイドライン`/`プロフィール_<acc>` をユーザーが一度レビューし「抜けなし」を確認。
4. **機械コンプラゲート（二重の安全網）**：生成後も `ガイドライン` を参照して違反を遮断（合格分のみ queued）。
- （任意）ドリフト検知：ローカル元ファイルが更新されたのにタブが未更新なら警告。
- ゲートは「タブの存在/非空」を保証、レジストリ＋ユーザーレビューが「中身の網羅」を保証する役割分担。

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

ユーザー要望で週次レポートを刷新。①TOP5を**実際の投稿本文で全文表示**②各項目に説明をつけ曖昧さ排除③**明るい白ベースのシンプルなデザイン**④**来週の方針＋具体的な投稿例3本**（AI生成）⑤morll.0802@gmail.com へ**毎週メール配信**。

- **`html_report.py` 全面刷新**：ダーク→**明るい白ベース**。**メール(Gmail)でもそのまま読める**よう table＋全要素インラインCSS（flex/grid/`<style>`/外部CSSなし）。セクション＝KPIサマリ(各指標に1行説明)／投稿ランキングTOP5(表示数順)／同(エンゲージ率順)／傾向分析(棒グラフ＋読み解き)／来週の方針／投稿例3本。`build_fragment`(本体)＋`wrap_document`(全文書)＋`build_html`(1アカ完結)に分割し、複数アカを1通のメールに連結可能。NaN/inf は『—』表示。本文は改行保持＋HTMLエスケープ(XSS安全)。
- **`analyzer.py`**：`total_views`/`total_reactions`/`avg_er` のKPI合計と、**エンゲージ率順TOP5(`top_er`)** を追加（従来の表示数順 `top` と併存）。`_entry` に likes/replies/reposts/quotes/reactions を含め全文結合(`text`)は下流で付与。★**ER=0.0 を欠落扱いしないよう修正**（`x or ""` は数値0.0が falsy で平均/ランキングから脱落し avg_er が上振れしていた→`_has_er` で None/空文字のみ欠落判定）。
- **`strategy.py`（新規）**：来週方針(direction)＋やること(focus)＋投稿例(examples 3本)を Claude で生成（`make_anthropic_strategy_fn`・`generate_fn` 注入可）。例文は**機械コンプラゲート**(NGワード/URL/字数)を通し違反は除外。キー無し/失敗時は **None**（方針セクションなしでレポートは出る）。**読取専用＝投稿キューに触れない**。
- **`main_weekly.py`**：分析→`enrich_tops_with_text`(投稿後ID経由でTOP5に本文結合・事業ごと posts を1回読みで使い回し)→reporter→**方針生成(`generate` が True のときのみ＝PAUSED/GENERATE_POSTS を尊重・課金抑制)**→HTMLレポート(reports/保存)＋メール本文 fragment。メール本文＝各アカの視覚レポートを連結した1通。全 `open` に `encoding="utf-8"`。
- **メール配信**：`weekly.yml` の `dawidd6/action-send-mail`(smtp.gmail.com:465・宛先 morll.0802@gmail.com・本文 `reports/メール本文.html`・添付 `reports/*.html`)は既存。**有効化に `ENABLE_EMAIL=1`(Variable・設定済み)＋`MAIL_USERNAME`/`MAIL_PASSWORD`(Secret＝Gmailアプリパスワード・ユーザー作業)** が必要。次の月曜 cron から配信、または `workflow_dispatch` で即時テスト可。
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
- **テスト**：`test_phase2.py` に 個別送信(件名・通数)／1通失敗の隔離／`build_message` の3ケース追加。全PASS。**実機テスト**＝製造業(views4978)＋占い(views53)の2通をローカルSMTPで実送信成功（宛先 morll.0802@gmail.com・各 Desktop にHTMLも保存）。
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
