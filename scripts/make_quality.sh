#!/usr/bin/env bash
set -euo pipefail

UV_BIN="${UV:-uv}"
ACTION="${1:-all}"

run_lint() {
  "$UV_BIN" run pylint src/sitesync
  "$UV_BIN" run ruff check
}

run_typecheck() {
  "$UV_BIN" run mypy src/sitesync
}

run_format() {
  "$UV_BIN" run black src tests
  "$UV_BIN" run ruff format
}

case "$ACTION" in
  lint)
    run_lint
    ;;
  typecheck)
    run_typecheck
    ;;
  format)
    run_format
    ;;
  all)
    run_lint
    run_typecheck
    ;;
  *)
    echo "Usage: $0 [lint|typecheck|format|all]" >&2
    exit 2
    ;;
esac
