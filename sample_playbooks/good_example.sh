#!/usr/bin/env bash
set -euo pipefail

readonly MAX_RETRIES=3
readonly DEPLOY_TIMEOUT=30

DB_PASSWORD="${VAULT_DB_PASSWORD}"

TMPFILE=$(mktemp /tmp/deploy_output.XXXXXX)
trap 'rm -f "$TMPFILE"' EXIT

FILES=$(ls -la "$DEPLOY_DIR")

cd "$TARGET_DIR" || exit 1
/bin/rm -rf ./old_deploy || { echo "ERROR: cleanup failed" >&2; exit 1; }

curl --cacert /etc/ssl/certs/ca.crt "https://internal-api.example.com/data" > "$TMPFILE"
