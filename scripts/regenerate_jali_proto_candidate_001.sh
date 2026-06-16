#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/jalitest-uv-cache}"

cd "$PROJECT_ROOT"

CLIP="Jali_proto_candidate_001_ProfessorCrystal"
BASE="data/processed/gaze_script/llm_process"

SCRIPT_PATH="${BASE}/${CLIP}__script.txt"
OUT_DIR="${BASE}"

# JALI / Maya project path mounted in WSL.
# If your TextGrid extension is actually .TextGrid, change this line.
TEXTGRID_PATH="/mnt/e/maya_project/JALI_test/scenes/sounds_proto1/${CLIP}.Textgrid"

if [[ ! -f "$TEXTGRID_PATH" ]]; then
  ALT_TEXTGRID_PATH="/mnt/e/maya_project/JALI_test/scenes/sounds_proto1/${CLIP}.TextGrid"
  if [[ -f "$ALT_TEXTGRID_PATH" ]]; then
    TEXTGRID_PATH="$ALT_TEXTGRID_PATH"
  else
    echo "[ERROR] TextGrid not found:"
    echo "  $TEXTGRID_PATH"
    echo "  $ALT_TEXTGRID_PATH"
    exit 1
  fi
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  "$PYTHON_BIN" -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --textgrid "$TEXTGRID_PATH" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
elif command -v uv >/dev/null 2>&1; then
  uv run python -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --textgrid "$TEXTGRID_PATH" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
else
  python3 -m expregaze_jali.process_performance_annotation \
    --script "$SCRIPT_PATH" \
    --textgrid "$TEXTGRID_PATH" \
    --out-dir "$OUT_DIR" \
    --clip-name "$CLIP" \
    --fps 30.0 \
    --clip-end-frame 1064.2
fi
