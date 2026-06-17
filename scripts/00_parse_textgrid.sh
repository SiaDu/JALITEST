#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATHS_CONFIG="${1:-configs/path_local.yaml}"
if [[ $# -gt 0 ]]; then
  shift
fi
EXTRA_ARGS=("$@")

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/jalitest-uv-cache}"

cd "$PROJECT_ROOT"

echo "Step 00: parse TextGrid word timings"
echo "Paths config: $PATHS_CONFIG"
echo "LLM calls: 0"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  "$PYTHON_BIN" -m expregaze.data.textgrid_parser \
    --paths-config "$PATHS_CONFIG" \
    "${EXTRA_ARGS[@]}"
elif command -v uv >/dev/null 2>&1; then
  uv run python -m expregaze.data.textgrid_parser \
    --paths-config "$PATHS_CONFIG" \
    "${EXTRA_ARGS[@]}"
else
  python3 -m expregaze.data.textgrid_parser \
    --paths-config "$PATHS_CONFIG" \
    "${EXTRA_ARGS[@]}"
fi
