#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

if [[ -z "${MARAWA_DATABASE_URL:-}" ]]; then
  echo "Missing MARAWA_DATABASE_URL. Attach Railway Postgres and expose its connection URL to the web service." >&2
  exit 1
fi

if [[ -z "${SESSION_SECRET:-}" || "${SESSION_SECRET}" == "dev-session-secret" ]]; then
  echo "Missing a production-safe SESSION_SECRET. Set a strong random value in Railway." >&2
  exit 1
fi

if [[ -z "${DATA_DIR:-}" && -d /storage ]]; then
  export DATA_DIR=/storage
fi

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-true}"
export SESSION_COOKIE_SAME_SITE="${SESSION_COOKIE_SAME_SITE:-lax}"

echo "Starting Marawa on Railway"
echo "  backend_dir=${BACKEND_DIR}"
echo "  data_dir=${DATA_DIR:-unset}"
echo "  session_cookie_secure=${SESSION_COOKIE_SECURE}"
echo "  session_cookie_same_site=${SESSION_COOKIE_SAME_SITE}"
if [[ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" && -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  echo "  bootstrap_admin_email=${BOOTSTRAP_ADMIN_EMAIL}"
else
  echo "  bootstrap admin credentials not configured; no admin will be created automatically" >&2
fi

cd "${BACKEND_DIR}"
exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
