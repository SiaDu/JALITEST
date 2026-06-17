#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/jalitest-uv-cache}"

cd "$PROJECT_ROOT"

CLIP="Jali_proto_candidate_001_ProfessorCrystal"
SCRIPT_DIR_REL="data/processed/gaze_script/llm_process"
OUT_DIR="data/processed/gaze_script"
PATHS_CONFIG="configs/path_local.yaml"

SCRIPT_PATH="${SCRIPT_DIR_REL}/${CLIP}__script.txt"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  "$PYTHON_BIN" -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --paths-config "$PATHS_CONFIG" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
elif command -v uv >/dev/null 2>&1; then
  uv run python -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --paths-config "$PATHS_CONFIG" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
else
  python3 -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --paths-config "$PATHS_CONFIG" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
fi
