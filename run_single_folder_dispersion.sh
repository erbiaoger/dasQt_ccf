#!/usr/bin/env bash
# Extract dispersion images for one folder containing CC/TSI MAT files.
#
# Purpose:
#   1) Call `tools/extract_dispersion_from_cc_mat.py`.
#   2) Process one folder of saved MAT files.
#   3) Save per-shot CC + dispersion PNG files, with optional NPZ cubes.
#
# Usage:
#   ./run_single_folder_dispersion.sh --input-dir /path/to/restack_or_cc_folder
#
# Example:
#   ./run_single_folder_dispersion.sh \
#     --input-dir ./outputs/cc_tsi_plot_outputs_by_folder/2025-03-27 \
#     --output-root ./outputs/dispersion_single \
#     --config ./config/config_ChangBai/disp_config.jsonc \
#     --shot-start 0 \
#     --shot-stop 200 \
#     -- --save-npz --overwrite
#
# 参数：
#   --input-dir PATH     必填。包含 `.mat` 文件的目录。
#   --output-root PATH   颜色谱图 PNG/NPZ 输出根目录。
#   --config PATH        颜色谱图配置 JSONC。默认：./config/config_ChangBai/disp_config.jsonc
#   --glob PATTERN       MAT 文件名模式。默认：restack_*.mat
#   --method NAME        传给 Python CLI 的可选方法覆盖参数。
#   --shot-start N       可选。包含的起始 shot 编号。
#   --shot-stop N        可选。不包含的结束 shot 编号。
#   --workers N          CPU 工作进程数。默认：1
#   --help               显示帮助信息。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --input-dir PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --input-dir PATH      Required. Directory containing MAT files.
  --output-root PATH    Default: ${SCRIPT_DIR}/outputs/dispersion_single
  --config PATH         Default: ${SCRIPT_DIR}/config/config_ChangBai/disp_config.jsonc
  --glob PATTERN        Default: restack_*.mat
  --method NAME         Optional dispersion method override.
  --shot-start N        Optional inclusive shot start.
  --shot-stop N         Optional exclusive shot stop.
  --workers N           Default: 1
  --help                Show help.
EOF
  print_common_footer
}

INPUT_DIR=""
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/dispersion_single"
CONFIG="${SCRIPT_DIR}/config/disp_config.jsonc"
GLOB_PATTERN="restack_*.mat"
METHOD=""
SHOT_START=""
SHOT_STOP=""
WORKERS="1"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir) INPUT_DIR="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --glob) GLOB_PATTERN="$2"; shift 2 ;;
    --method) METHOD="$2"; shift 2 ;;
    --shot-start) SHOT_START="$2"; shift 2 ;;
    --shot-stop) SHOT_STOP="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${INPUT_DIR}" ]]; then
  echo "[ERROR] --input-dir is required." >&2
  usage
  exit 1
fi

CMD=(
  "${SCRIPT_DIR}/tools/extract_dispersion_from_cc_mat.py"
  --input-dir "${INPUT_DIR}"
  --output-root "${OUTPUT_ROOT}"
  --config "${CONFIG}"
  --glob "${GLOB_PATTERN}"
  --workers "${WORKERS}"
)

if [[ -n "${METHOD}" ]]; then
  CMD+=(--method "${METHOD}")
fi
if [[ -n "${SHOT_START}" ]]; then
  CMD+=(--shot-start "${SHOT_START}")
fi
if [[ -n "${SHOT_STOP}" ]]; then
  CMD+=(--shot-stop "${SHOT_STOP}")
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
