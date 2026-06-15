# SETUP.md — Threads自動投稿システム 導入手順書（ジャンル非依存・販売用テンプレ）

スプレッドシートに「投稿文・日付・時刻」を並べるだけで、複数のThreadsアカウントへ自動投稿する。
この手順書は**どんなジャンルのアカウントでも同じ手順で構築できる**よう汎用化してある。
新規クライアント／新規アカウントを増やすときは、このファイルを上から順になぞればよい。

> 進捗はチェックボックスで管理。完了したら `[x]` にする。

---

## 全体フロー

```
Phase 0  コード準備（依存インストール・テスト）              … 1回だけ
Phase A  Googleスプレッドシート + サービスアカウント         … データ層
Phase B  Metaアプリ + Threadsトークン取得（開発モード）      … 自分のアカウントは審査不要
Phase C  トークンをシートへ → DRY_RUN疎通 → 実投稿テスト
Phase D  GitHubへpush + Secrets + Actions自動化
Phase E  量産テンプレ化 + クライアント展開 + 販売（App Review）
```

**最短ルート:** 自分所有のアカウントは「開発モード」で審査を待たずに運用開始できる。
App Reviewは「他人のアカウントを代理運用 / システムを第三者に使わせる」段階で初めて必要。

---

## Phase 0 — コード準備（完了済み）

- [x] コードを作業ディレクトリへ展開
- [x] `pip3 install -r requirements.txt`
- [x] `python3 test_logic.py` 全PASS（ツリー連結・時刻判定・レート制限・保留・冪等性）

ローカル検証コマンド:
```bash
python3 test_logic.py            # ロジック検証（API不要）
DRY_RUN=1 python3 main.py        # 実投稿せず疎通確認（要 Phase A/B/C の環境変数）
```

---

## Phase A — Googleスプレッドシート + サービスアカウント

「投稿キュー」と「トークン保管庫」を兼ねるスプレッドシートを作り、
プログラムがアクセスできるよう「サービスアカウント」を発行して共有する。
**タブ作成とヘッダ入力は `scripts/setup_sheet.py` が自動でやる**ので、人間は「空のシートを作って共有」するだけでよい。

### A-1. Google Cloud でサービスアカウントを作る（人間）
- [ ] https://console.cloud.google.com/ にログイン。
- [ ] 上部のプロジェクト選択 →「新しいプロジェクト」→ 名前（例 `threads-poster`）→ 作成 → そのプロジェクトを選択。
- [ ] 「APIとサービス」→「ライブラリ」で **Google Sheets API** を検索 →「有効にする」。
- [ ] 「APIとサービス」→「認証情報」→「認証情報を作成」→「サービスアカウント」。
      名前（例 `poster-bot`）→ 作成して続行 → 役割は付けずに「完了」。
- [ ] 作成されたサービスアカウントをクリック →「キー」タブ →「鍵を追加」→「新しい鍵を作成」→ **JSON** → ダウンロード。
- [ ] このJSONの中の `"client_email"`（`xxxx@xxxx.iam.gserviceaccount.com`）をコピー。

### A-2. 空のスプレッドシートを作る（人間）
- [ ] https://sheets.new で新規作成（中身は空でよい。タブ名やヘッダは触らなくてよい）。
- [ ] URLの `/d/` と `/edit` の間が **スプレッドシートID** → メモ。

### A-3. シートをサービスアカウントに共有（人間）
- [ ] A-2のシート右上「共有」→ A-1の `client_email` を貼り付け、**編集者**で共有（通知不要）。

> セキュリティ: このシートはトークンの保管庫になる。共有相手は**本人とこのサービスアカウントのみ**。
> 「リンクを知っている全員」は絶対に使わない。

### A-4. タブ＆ヘッダの自動生成＋接続チェック（Claudeが実行）
- [ ] `.env` に `SPREADSHEET_ID` と `GOOGLE_SERVICE_ACCOUNT_FILE`（JSONのパス）を記入。
- [ ] 実行:
  ```bash
  python3 scripts/setup_sheet.py --json <JSONパス> --sheet <スプレッドシートID>
  ```
  → `accounts` / `posts` タブを作成し、正しいヘッダを自動設定。緑（✓）で接続成功を確認。
- [ ] 続けて無投稿の疎通確認:
  ```bash
  DRY_RUN=1 ./scripts/local_run.sh
  ```

> 人間がやるのは A-1〜A-3 のブラウザ操作だけ。ヘッダ手入力は不要（スクリプトが正確に作る）。

---

## Phase B — Metaアプリ + Threadsトークン取得（開発モード）

投稿先のThreadsアカウントと、APIを叩くための「長期トークン」を用意する。
**自分所有のアカウントなら開発モードで審査なしに投稿できる。**

### B-1. 投稿先のThreadsアカウントを用意
- [ ] 投稿したいThreadsアカウント（例: 製造業求人用）を作成 or 用意。
- [ ] 設定 → アカウント → このアカウントが「プロアカウント / professional」になっているか確認（推奨）。

### B-2. Meta開発者アプリを作る
- [ ] https://developers.facebook.com/ で開発者登録（未登録なら）。
- [ ] 「マイアプリ」→「アプリを作成」。ユースケースで **Threads API（"Access the Threads API"）** を選択。
- [ ] アプリ作成後、「Threads API」のセットアップへ。`threads_basic` と `threads_content_publish` の権限を追加。
- [ ] 「Threads OAuth設定」で **リダイレクトURI** を登録（後でトークン取得に使う。例 `https://localhost/`）。
- [ ] アプリの **App ID** と **App secret** をメモ（App secret = `THREADS_CLIENT_SECRET`）。

### B-3. 投稿するアカウントを「テスター」に追加（開発モードで投稿するため）
- [ ] アプリの「役割 / Roles」→ 投稿先Threadsアカウントを **Threadsテスター** として追加。
- [ ] 投稿先アカウント側でテスター招待を承認（Threadsの設定 → ウェブサイトの権限 等から）。

### B-4. 短期トークンを取得
- [ ] Threadsの認可URLをブラウザで開く（App ID・redirect_uri・scope を入れて生成。下記C補足参照）。
- [ ] 承認後、リダイレクト先URLの `code=` を受け取る。
- [ ] `code` を短期アクセストークンに交換（curl 1発。手順はPhase Bで一緒に組む）。
- [ ] レスポンスの `access_token`（短期1時間）と、自分の **user_id** を控える。

> ここはブラウザ操作とcurlが混ざるので、B到達時に「コピペで動くコマンド」を生成して渡す。

---

## Phase C — トークンをシートへ → DRY_RUN疎通 → 実投稿テスト

### C-1. 長期トークン（60日）を取得
**最短＝アプリ内のトークン生成ツール**: Metaアプリ「ユースケース→Threads APIにアクセス→カスタマイズ→設定」を一番下までスクロール →「ユーザートークン生成ツール」で投稿アカウントを選び生成（要: テスター承認済み）。表示された長期トークンをコピー。
- [ ] 代替（OAuth code 方式）: `python3 scripts/get_auth_url.py` → 承認 → `code` を `python3 scripts/exchange_token.py <code>`（要 `THREADS_APP_ID`/`THREADS_CLIENT_SECRET`/`THREADS_REDIRECT_URI`）。
- ※ **テスター招待の承認は Threadsアプリ側「設定→アカウント→ウェブサイトのアクセス許可」**で行う（Meta開発者画面ではない＝最大のハマりどころ）。

### C-2. accounts タブへ登録（`scripts/setup_account.py` が自動化）
- [ ] トークンを一時ファイルに保存 → `set -a; . ./.env; set +a; python3 scripts/setup_account.py --token-file <path> --account <識別名>`
      → トークンから **user_id を自動取得**し、`accounts` タブ（日本語見出し: アカウント / ユーザーID / アクセストークン / トークン更新日時 / 本日投稿数 / カウント日付）へ追記。
- [ ] 手動で入れる場合: `アカウント`=識別名、`ユーザーID`、`アクセストークン`、`トークン更新日時`=今日の日時。`本日投稿数`/`カウント日付` は空でよい。

### C-3. 投稿タブを用意してテスト投稿を1件入れる
- [ ] `python3 scripts/setup_post_tab.py --account <識別名> --update-examples` で **`投稿_<識別名>` タブ**を作成 → `python3 scripts/add_validation_ja.py --tab 投稿_<識別名>` でドロップダウン/日時チェックを付与。
- [ ] その `投稿_<識別名>` タブに1行: `投稿ID`=`T1`、`投稿日時`=数分後のJST（例 `2026-06-16 21:00`）、`本文`=テスト本文、`メディア種類`=`TEXT`、`状態`=空。※アカウント列は無い（タブ名で判別）。

### C-4. ローカルで疎通（投稿しない）
- [ ] 環境変数をセット:
  ```bash
  export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /path/to/service-account.json)"
  export SPREADSHEET_ID="<スプレッドシートID>"
  export TZ_NAME="Asia/Tokyo"
  ```
- [ ] `DRY_RUN=1 python3 main.py` → シートを読めて、投稿対象を検出できるか確認（実投稿はしない）。

### C-5. 実投稿テスト
- [ ] `python3 main.py`（DRY_RUNなし）→ Threadsに実際に投稿されるか確認。
- [ ] postsタブの該当行が `status=posted`、`posted_id` が埋まることを確認。

---

## Phase D — GitHubへpush + Secrets + Actions自動化

サーバ不要。GitHub Actionsの無料cronで10分おきに自動実行する。

### D-1. リポジトリ作成 & push
- [ ] GitHubでプライベートリポジトリ作成。
- [ ] ローカルで `git init` → commit → push（`.gitignore` で `.env`・JSON鍵を除外）。

  > **絶対にトークンやサービスアカウントJSONをコミットしない。** 全てSecretsで渡す。

### D-2. Secrets登録
- [ ] リポジトリ → Settings → Secrets and variables → Actions → New repository secret:
  - `GOOGLE_SERVICE_ACCOUNT_JSON` … JSON全文
  - `SPREADSHEET_ID` … スプレッドシートID

### D-3. Actions有効化 & 疎通
- [ ] Actionsタブ → ワークフローを有効化。
- [ ] `Run workflow`（手動実行 / workflow_dispatch）で1回流す → ログ確認。
- [ ] 以後、`*/10 * * * *`（UTC）で自動実行。投稿時刻判定はシート側のJSTで行われる。

---

## Phase E — 量産テンプレ化 + クライアント展開 + 販売

### E-1. 2アカウント目以降（同一オーナー）
- [ ] accountsタブに行を足し、postsタブの `account` 列で振り分けるだけ。コード変更不要。

### E-2. 他クライアントへ複製（納品）
- [ ] このリポジトリをテンプレートとして複製。
- [ ] クライアントごとに「スプレッドシート＋サービスアカウント＋GitHubリポジトリ＋Threadsアプリ」を1セット用意。
- [ ] この `SETUP.md` を導入マニュアルとして同梱。

### E-3. App Review（第三者運用・販売で必須）
- [ ] `threads_content_publish` の App Review を申請。
- [ ] 審査用ユースケース説明文（用途＝「自社/クライアントの求人・告知をThreadsへ予約自動配信」）を用意。
- [ ] スクリーンキャストで投稿フローを提示。

### E-4. 販売モデル（メモ）
- 売り切り（構築納品＋マニュアル）／月額運用代行／SaaS化（StoreをマルチテナントDBに差し替え）。
- コードは層分離済み（`sheets.py` の `Store` を差し替えればDB/マルチテナント化が可能）。

---

## 落とし穴（運用前に確認）

- ヘッダは日本語(正規)/英語(旧名)どちらでも可（`sheets.py` のエイリアスが吸収）。ただし定義外の語にすると読めない。投稿タブ名は `投稿_<account>` で、`<account>` は accounts の名前と一致させる。タブ名・見出し行は消さない。
- cronはUTC、投稿時刻判定はJST（混同しない）。
- GitHub Actionsのcronは数分遅延あり（秒単位の正確投稿は不可）。
- トークンをシートに置くため、共有は本人＋サービスアカウントのみ。公開リンク禁止。
- `status=error` の行は、原因解消後に `status` を空に戻せば次回再試行。
- 親子（ツリー）は親を必ず先の時刻に。親が同回公開→子は同回 or 次回に自動連結。
- 1日上限は既定50（公式250より低め＝凍結回避）。`MAX_POSTS_PER_DAY` で調整。
```
