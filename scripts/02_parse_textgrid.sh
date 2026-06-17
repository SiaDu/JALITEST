#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTRA_ARGS=("$@")

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/jalitest-uv-cache}"

cd "$PROJECT_ROOT"

echo "Step 02: parse TextGrid word timings"
echo "LLM calls: 0"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  "$PYTHON_BIN" -m expregaze_jali.textgrid_parser "${EXTRA_ARGS[@]}"
elif command -v uv >/dev/null 2>&1; then
  uv run python -m expregaze_jali.textgrid_parser "${EXTRA_ARGS[@]}"
else
  python3 -m expregaze_jali.textgrid_parser "${EXTRA_ARGS[@]}"
fi
