#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

PGHOST_VALUE="${MARAWA_PGHOST:-127.0.0.1}"
PGPORT_VALUE="${MARAWA_PGPORT:-5432}"
PGUSER_VALUE="${MARAWA_PGUSER:-$USER}"
PGMAINT_DB_VALUE="${MARAWA_PGMAINT_DB:-postgres}"
DEV_DB_VALUE="${MARAWA_DEV_DB:-marawa_dev}"
TEST_DB_VALUE="${MARAWA_TEST_DB:-marawa_test}"

function usage() {
  cat <<'EOF'
Usage: scripts/local_postgres.sh <command>

Commands:
  status            Check whether the local PostgreSQL service is reachable.
  create-dev-db     Create the local development database if it does not exist.
  recreate-test-db  Drop and recreate the dedicated local smoke-test database.
  url [dev|test]    Print a MARAWA_DATABASE_URL-compatible connection URL.
  test              Recreate the smoke-test database and run the Postgres smoke test.

Environment overrides:
  MARAWA_PGHOST      Default: 127.0.0.1
  MARAWA_PGPORT      Default: 5432
  MARAWA_PGUSER      Default: current shell user
  MARAWA_PGMAINT_DB  Default: postgres
  MARAWA_DEV_DB      Default: marawa_dev
  MARAWA_TEST_DB     Default: marawa_test
EOF
}

function pg_url() {
  local db_name="$1"
  printf 'postgresql+psycopg://%s@%s:%s/%s\n' "$PGUSER_VALUE" "$PGHOST_VALUE" "$PGPORT_VALUE" "$db_name"
}

function require_server() {
  pg_isready -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" -d "$PGMAINT_DB_VALUE" >/dev/null
}

function database_exists() {
  local db_name="$1"
  local result
  result="$(
    psql \
      -h "$PGHOST_VALUE" \
      -p "$PGPORT_VALUE" \
      -U "$PGUSER_VALUE" \
      -d "$PGMAINT_DB_VALUE" \
      -Atqc "SELECT 1 FROM pg_database WHERE datname = '$db_name';"
  )"
  [[ "$result" == "1" ]]
}

function create_dev_db() {
  require_server
  if database_exists "$DEV_DB_VALUE"; then
    echo "Development database already exists: $DEV_DB_VALUE"
    return 0
  fi
  createdb -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" "$DEV_DB_VALUE"
  echo "Created development database: $DEV_DB_VALUE"
}

function recreate_test_db() {
  require_server
  dropdb --if-exists -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" "$TEST_DB_VALUE"
  createdb -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" "$TEST_DB_VALUE"
  echo "Recreated smoke-test database: $TEST_DB_VALUE"
}

function run_smoke_test() {
  recreate_test_db
  (
    cd "$BACKEND_DIR"
    MARAWA_POSTGRES_TEST_URL="$(pg_url "$TEST_DB_VALUE")" ../.venv/bin/pytest tests/test_postgres_smoke.py
  )
}

COMMAND="${1:-}"

case "$COMMAND" in
  status)
    require_server
    echo "PostgreSQL is reachable at $(pg_url "$PGMAINT_DB_VALUE")"
    ;;
  create-dev-db)
    create_dev_db
    ;;
  recreate-test-db)
    recreate_test_db
    ;;
  url)
    TARGET="${2:-dev}"
    case "$TARGET" in
      dev)
        pg_url "$DEV_DB_VALUE"
        ;;
      test)
        pg_url "$TEST_DB_VALUE"
        ;;
      *)
        echo "Unknown URL target: $TARGET" >&2
        exit 1
        ;;
    esac
    ;;
  test)
    run_smoke_test
    ;;
  *)
    usage
    exit 1
    ;;
esac
