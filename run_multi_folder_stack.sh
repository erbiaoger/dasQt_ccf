#!/usr/bin/env bash
# Batch restack saved CC/TSI MAT files for many output folders.
#
# Purpose:
#   1) Call `batch_stack_saved_cc_mat.py`.
#   2) Scan one output root containing many folders with `cc_stack*.mat`.
#   3) Save each folder's restack result to a mirrored output tree.
#
# Usage:
#   ./run_multi_folder_stack.sh --input-root /path/to/cc_output_root
#
# Example:
#   ./run_multi_folder_stack.sh \
#     --input-root ./outputs/cc_tsi_outputs_by_folder \
#     --output-root ./outputs/cc_tsi_plot_outputs_by_folder \
#     --glob "cc_stack_tsi_*.mat" \
#     --stack-size None \
#     --interval None \
#     --stack-method robust \
#     --cc-backend cpu \
#     --cc-device cpu \
#     --jobs 8
#
# Parameters:
#   --input-root PATH    Required. Root folder containing many CC/TSI result folders.
#   --output-root PATH   Output root for restack results.
#   --glob PATTERN       MAT filename pattern. Default: cc_stack_tsi_*.mat
#   --folder-glob GLOB   Optional relative-folder filter, e.g. 2025-03-*.
#   --stack-size VALUE   Default: None
#   --interval VALUE     Default: None
#   --start-index N      Default: 0
#   --stack-method NAME  Default: robust
#   --cc-backend NAME    Default: cpu
#   --cc-device NAME     Default: cpu
#   --cc-dtype NAME      Default: float32
#   --plot-name NAME     Default: local_cc_preview.png
#   --jobs N             Parallel folder count. Default: 8
#   --no-recursive       Disable recursive scan.
#   --help               Show this message.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --input-root PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --input-root PATH     Required. Root directory of saved MAT outputs.
  --output-root PATH    Default: ${SCRIPT_DIR}/outputs/cc_tsi_plot_outputs_by_folder
  --glob PATTERN        Default: cc_stack_tsi_*.mat
  --folder-glob GLOB    Optional relative-folder filter.
  --stack-size VALUE    Default: None
  --interval VALUE      Default: None
  --start-index N       Default: 0
  --stack-method NAME   Default: robust
  --cc-backend NAME     Default: cpu
  --cc-device NAME      Default: cpu
  --cc-dtype NAME       Default: float32
  --plot-name NAME      Default: local_cc_preview.png
  --jobs N              Default: 8
  --no-recursive        Only scan input-root one level.
  --help                Show help.
EOF
  print_common_footer
}

INPUT_ROOT=""
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/cc_tsi_plot_outputs_by_folder"
GLOB_PATTERN="cc_stack_tsi_*.mat"
FOLDER_GLOB=""
STACK_SIZE="None"
INTERVAL="None"
START_INDEX="0"
STACK_METHOD="robust"
CC_BACKEND="cpu"
CC_DEVICE="cpu"
CC_DTYPE="float32"
PLOT_NAME="local_cc_preview.png"
JOBS="8"
RECURSIVE="1"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-root) INPUT_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --glob) GLOB_PATTERN="$2"; shift 2 ;;
    --folder-glob) FOLDER_GLOB="$2"; shift 2 ;;
    --stack-size) STACK_SIZE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --start-index) START_INDEX="$2"; shift 2 ;;
    --stack-method) STACK_METHOD="$2"; shift 2 ;;
    --cc-backend) CC_BACKEND="$2"; shift 2 ;;
    --cc-device) CC_DEVICE="$2"; shift 2 ;;
    --cc-dtype) CC_DTYPE="$2"; shift 2 ;;
    --plot-name) PLOT_NAME="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --no-recursive) RECURSIVE="0"; shift ;;
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

CMD=(
  "${SCRIPT_DIR}/batch_stack_saved_cc_mat.py"
  --input-root "${INPUT_ROOT}"
  --output-root "${OUTPUT_ROOT}"
  --glob "${GLOB_PATTERN}"
  --stack-size "${STACK_SIZE}"
  --interval "${INTERVAL}"
  --start-index "${START_INDEX}"
  --stack-method "${STACK_METHOD}"
  --plot-name "${PLOT_NAME}"
  --cc-backend "${CC_BACKEND}"
  --cc-device "${CC_DEVICE}"
  --cc-dtype "${CC_DTYPE}"
  --jobs "${JOBS}"
)

if [[ -n "${FOLDER_GLOB}" ]]; then
  CMD+=(--folder-glob "${FOLDER_GLOB}")
fi
if [[ "${RECURSIVE}" == "1" ]]; then
  CMD+=(--recursive)
else
  CMD+=(--no-recursive)
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
