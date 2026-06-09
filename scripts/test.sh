#!/usr/bin/env bash
# Run pytest. Pass any pytest args/filters as arguments.
# Examples:
#   ./scripts/test.sh                        # all tests
#   ./scripts/test.sh tests/test_grader.py   # one file
#   ./scripts/test.sh -k "grader"            # name filter
#   ./scripts/test.sh -x -v                  # stop-on-fail, verbose
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv/bin/activate"

if [[ ! -f "$VENV" ]]; then
  echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

source "$VENV"
cd "$ROOT"

exec pytest "$@"
