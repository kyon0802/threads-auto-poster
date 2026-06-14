#!/usr/bin/env bash
# ローカル実行ヘルパー。.env を読み込み、サービスアカウントJSONを環境変数に展開して main.py を実行する。
# 既定は DRY_RUN=1（実投稿しない）。実投稿は: DRY_RUN=0 ./scripts/local_run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# コマンドラインで明示された DRY_RUN（例: DRY_RUN=0 ./scripts/local_run.sh）を退避。
# .env を source すると DRY_RUN が .env の値で上書きされてしまうため、後で復元する。
_CLI_DRY_RUN="${DRY_RUN:-}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# CLI で明示された DRY_RUN を .env より優先する（未指定なら .env / 既定値を使う）
if [ -n "${_CLI_DRY_RUN}" ]; then
  DRY_RUN="${_CLI_DRY_RUN}"
fi

# ファイルパスで渡された場合はJSON全文に展開（main.py は GOOGLE_SERVICE_ACCOUNT_JSON を読む）
if [ -n "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" ] && [ -z "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ]; then
  GOOGLE_SERVICE_ACCOUNT_JSON="$(cat "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}")"
  export GOOGLE_SERVICE_ACCOUNT_JSON
fi

export DRY_RUN="${DRY_RUN:-1}"
export TZ_NAME="${TZ_NAME:-Asia/Tokyo}"

echo "DRY_RUN=${DRY_RUN}（1=実投稿しない / 0=実投稿）で実行します..."
python3 main.py
