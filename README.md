# Threads 自動投稿システム

スプレッドシートに投稿を1回セットすれば、複数アカウントへ時間指定で自動投稿する。
ツリー（リプライ連結）対応。サーバ不要、GitHub Actions の無料枠で動く。

## 構成

```
スプレッドシート（投稿キュー＋トークン保管）
        ↓ 読む / 結果を書き戻す
GitHub Actions（10分おき cron）→ main.py → Threads API（公開）
```

- **投稿のセット** … スプレッドシートに行を追加するだけ
- **公開時刻の判定** … 各行の `post_datetime`（JST）が来たら公開
- **ツリー** … 子行の `reply_to` に親行の `row_id` を書く → 親の公開後IDに自動連結
- **多アカウント** … `accounts` タブにアカウントを増やすだけ
- **安全装置** … 1アカウント1日 `MAX_POSTS_PER_DAY` 件まで（既定50）、二重投稿防止、親未公開なら子は次回に保留

---

## スプレッドシートの作り方

新規スプレッドシートを作り、シートを2枚用意する。**1行目のヘッダ名は完全一致させること。**

### `accounts` タブ

| account | user_id | access_token | token_updated_at | daily_count | daily_count_date |
|---|---|---|---|---|---|
| uranai | 17841... | （長期トークン） | 2026-06-08 12:00:00 | | |
| rerise | 17842... | （長期トークン） | 2026-06-08 12:00:00 | | |

- `account` … 任意の識別名（postsタブから参照）
- `user_id` … Threads の User ID
- `access_token` … 後述のブートストラップで取得した長期トークン
- `token_updated_at` … トークンをセットした日時。以後システムが自動更新
- `daily_count` / `daily_count_date` … 空でOK。システムが管理

### `posts` タブ

| row_id | account | post_datetime | text | media_type | media_url | reply_to | reply_control | status | posted_id | posted_at | error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P001 | uranai | 2026-06-10 20:00 | 今日の運勢… | TEXT | | | everyone | | | | |
| P002 | uranai | 2026-06-10 20:01 | 続き（ツリー2投稿目） | TEXT | | P001 | | | | | |
| P003 | rerise | 2026-06-11 08:00 | 求人投稿 | IMAGE | https://.../a.jpg | | | | | | |

- `row_id` … 一意なID（自由。ツリーの親参照に使う）
- `post_datetime` … 公開時刻 JST（`YYYY-MM-DD HH:MM`）
- `media_type` … `TEXT` / `IMAGE` / `VIDEO`（空ならTEXT）
- `media_url` … 画像/動画は**公開到達可能なURL**が必須（直アップロード不可）
- `reply_to` … ツリーにする時、親の `row_id` を書く。トップ投稿は空
- `reply_control` … `everyone` / `accounts_you_follow` / `mentioned_only`（空でOK）
- `status` 以降 … 空で投入。システムが `posted` / `error` 等を書き戻す

> ツリーは「親と同時刻〜数分差」で並べればよい。親が先に公開され、その公開後IDが子の `reply_to_id` に自動で入る。親がまだ公開前なら子はその回はスキップされ、次回（最大10分後）に自動で連結公開される。

---

## セットアップ手順

### 1. Meta アプリと権限（必須・先に着手）

1. Meta for Developers でアプリを作成し Threads use case を追加
2. 必要権限: `threads_basic`, `threads_content_publish`
3. **本番公開には App Review が必要**（数週間かかる場合あり）。審査中はテストユーザーで動作確認できる
4. OAuth で認可 → 認可コード → 短期トークン（1時間）を取得
5. Threads の User ID を控える

### 2. 長期トークンの取得（初回1回）

```bash
pip install -r requirements.txt
export THREADS_CLIENT_SECRET="あなたのApp Secret"
python bootstrap_token.py <短期トークン>
```

出力された `access_token` を `accounts` タブの該当アカウントに貼り、`token_updated_at` に現在日時を入れる。
（以後、システムが約60日サイクルで自動リフレッシュする）

### 3. Google サービスアカウント（スプレッドシート接続）

1. Google Cloud で Sheets API を有効化
2. サービスアカウントを作成し JSON 鍵をダウンロード
3. **スプレッドシートをそのサービスアカウントのメールアドレスに「編集者」で共有**
4. JSON 全文と スプレッドシートID を控える

### 4. GitHub にデプロイ

1. このフォルダを **private リポジトリ** として push
2. Settings → Secrets and variables → Actions に登録:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` … JSON全文
   - `SPREADSHEET_ID` … スプレッドシートID
3. Actions タブで `threads-auto-post` を有効化
4. まず `Run workflow`（手動実行）で動作確認 → 以後10分おきに自動実行

---

## 運用のコツ

- **検証は DRY_RUN**: ローカルで `DRY_RUN=1 python main.py` を実行すると、実際には投稿せずログだけ出る
- **スパム判定回避**: `MAX_POSTS_PER_DAY` は公式上限250より低め（既定50）。投稿は数分〜数十分間隔を空けてスケジュールする（バースト投稿は凍結リスク）
- **エラー確認**: `posts` タブの `error` 列と Actions の実行ログを見る。`status=error` の行は原因解消後に `status` を空に戻せば再試行される
- **1〜2週間の放置運用**: 2週間分の投稿を `posts` に並べておけば、あとは触らなくてよい

## セキュリティ注意

長期トークンをスプレッドシートに置くため、シートは**サービスアカウントと本人のみに共有**し、公開リンクは作らないこと。より厳格に運用するなら、`accounts` の保管先を外部DB（例: Supabase/SQLite）に差し替え可能（`sheets.py` の `Store` を実装するだけ）。

## ファイル構成

```
main.py                       実行エントリ
bootstrap_token.py            初回トークン取得
threads_poster/
  threads_api.py              Threads API クライアント
  sheets.py                   スプレッドシート連携 + テスト用モック
  publisher.py                公開ロジック（時刻判定/ツリー/レート制限/トークン更新）
test_logic.py                 ロジック検証テスト（API不要）
.github/workflows/post.yml    10分おき cron
requirements.txt / .env.example
```
