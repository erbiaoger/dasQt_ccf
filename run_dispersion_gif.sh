#!/usr/bin/env bash
# Build GIFs from saved dispersion PNG images.
#
# Purpose:
#   1) Call `tools/create_shot_dispersion_gif.py`.
#   2) Support two modes:
#      - all images in one folder
#      - one fixed shot index across many folders
#   3) Produce a reproducible GIF command with clear parameters.
#
# Usage:
#   ./run_dispersion_gif.sh --input-root /path/to/dispersion_root --all-images
#   ./run_dispersion_gif.sh --input-root /path/to/dispersion_root --shot-index 179
#
# Examples:
#   ./run_dispersion_gif.sh \
#     --input-root ./outputs/dispersion_outputs_by_folder/2025-03-27 \
#     --all-images \
#     --output-gif ./outputs/dispersion_outputs_by_folder/2025-03-27/dispersion_all.gif
#
#   ./run_dispersion_gif.sh \
#     --input-root ./outputs/dispersion_outputs_by_folder \
#     --shot-index 179 \
#     --output-gif ./outputs/dispersion_outputs_by_folder/shot_0179_dispersion.gif \
#     --recursive
#
# Parameters:
#   --input-root PATH     Required. Image folder or dispersion output root.
#   --all-images          Collect all images under input-root.
#   --shot-index N        Collect one shot_XXXX_dispersion.png across folders.
#   --recursive           Used with --all-images to scan subfolders recursively.
#   --output-gif PATH     Optional output GIF path.
#   --duration-ms N       Optional frame duration in ms.
#   --loop N              Optional GIF loop count. 0 means infinite.
#   --help                Show this message.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --input-root PATH (--all-images | --shot-index N) [options] [-- EXTRA_PY_ARGS...]

Options:
  --input-root PATH     Required. Image folder or dispersion output root.
  --all-images          Build one GIF from all images under input-root.
  --shot-index N        Build one GIF for a fixed shot index across folders.
  --recursive           Recursive scan for --all-images mode.
  --output-gif PATH     Optional output GIF path.
  --duration-ms N       Optional frame duration in milliseconds.
  --loop N              Optional GIF loop count. 0 means infinite.
  --help                Show help.
EOF
  print_common_footer
}

INPUT_ROOT=""
ALL_IMAGES="0"
SHOT_INDEX=""
RECURSIVE="0"
OUTPUT_GIF=""
DURATION_MS=""
LOOP_COUNT=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-root) INPUT_ROOT="$2"; shift 2 ;;
    --all-images) ALL_IMAGES="1"; shift ;;
    --shot-index) SHOT_INDEX="$2"; shift 2 ;;
    --recursive) RECURSIVE="1"; shift ;;
    --output-gif) OUTPUT_GIF="$2"; shift 2 ;;
    --duration-ms) DURATION_MS="$2"; shift 2 ;;
    --loop) LOOP_COUNT="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${INPUT_ROOT}" ]]; then
  echo "[ERROR] --input-root is required." >&2
  usage
  exit 1
fi
if [[ "${ALL_IMAGES}" != "1" && -z "${SHOT_INDEX}" ]]; then
  echo "[ERROR] Choose either --all-images or --shot-index." >&2
  usage
  exit 1
fi
if [[ "${ALL_IMAGES}" == "1" && -n "${SHOT_INDEX}" ]]; then
  echo "[ERROR] --all-images and --shot-index cannot be used together." >&2
  usage
  exit 1
fi

CMD=(
  "${SCRIPT_DIR}/tools/create_shot_dispersion_gif.py"
  --input-root "${INPUT_ROOT}"
)

if [[ "${ALL_IMAGES}" == "1" ]]; then
  CMD+=(--all-images)
fi
if [[ -n "${SHOT_INDEX}" ]]; then
  CMD+=(--shot-index "${SHOT_INDEX}")
fi
if [[ "${RECURSIVE}" == "1" ]]; then
  CMD+=(--recursive)
fi
if [[ -n "${OUTPUT_GIF}" ]]; then
  CMD+=(--output-gif "${OUTPUT_GIF}")
fi
if [[ -n "${DURATION_MS}" ]]; then
  CMD+=(--duration-ms "${DURATION_MS}")
fi
if [[ -n "${LOOP_COUNT}" ]]; then
  CMD+=(--loop "${LOOP_COUNT}")
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

ensure_dasqt_env
(
  cd "${DASQT_REPO_DIR}"
  # shellcheck disable=SC1090
  source "${DASQT_VENV_ACTIVATE}"
  uv run --with pillow python "${CMD[@]}"
)
