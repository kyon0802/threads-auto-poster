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
