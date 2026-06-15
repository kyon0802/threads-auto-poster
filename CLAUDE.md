# CLAUDE.md — Threads自動投稿システム 引き継ぎ

このファイルはClaude Codeが起動時に自動参照する。**作業を再開する前に必ず全文を読むこと。**

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
- [x] 本番の実投稿テスト成功（2026-06-14、account=`rk_riko2`）
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

**投稿タブ = アカウントごとに分割**。タブ名 **`投稿_<アカウント名>`**（例 `投稿_rk_riko2`、`<アカウント名>`は accounts の `アカウント` と一致）。**アカウントはタブ名から自動判定するので「アカウント」列は持たない**。見出し:
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

- **本番稼働（Phase B/C/D 完了）**: account=`rk_riko2` で実投稿成功（2026-06-14）。private repo `kyon0802/threads-auto-poster`、10分cron稼働。Secrets=`GOOGLE_SERVICE_ACCOUNT_JSON`/`SPREADSHEET_ID`、Variable=`MAX_POSTS_PER_DAY`。gh CLI は `/opt/homebrew/bin/gh`（PATH未通）でアカウント kyon0802・scope に workflow 追加済み。
- **シート見出しの日本語化（後方互換つき）**: `sheets.py` にエイリアス層（内部キー(英語)⇔日/英見出し）。`migrate_headers_ja.py` で既存シートをデータ保持のまま日本語化。
- **アカウント別タブ化**: 投稿タブを `投稿_<account>` で複数持てるように（タブ名からアカウント自動判定＝行に書かない）。`setup_post_tab.py` で生成。複数アカは**1シート・1リポジトリ**で運用（分けるのは Phase E のみ）。
- **入力支援**: `add_validation_ja.py` で `メディア種類`/`状態`=ドロップダウン、`投稿日時`=形式チェック＋文字列書式。`記入例` タブにツリー例。
- **local_run.sh 修正**: コマンドラインの `DRY_RUN` を `.env` より優先（`DRY_RUN=0 ./scripts/local_run.sh` が実投稿になるよう）。
- 既知メンテ: `post.yml` の `actions/checkout@v4`・`setup-python@v5` が Node20非推奨警告（2026-09-16撤去予定。動作は継続）。
