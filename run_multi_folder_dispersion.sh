#!/usr/bin/env bash
# Extract dispersion images for many folders under one root.
#
# Purpose:
#   1) Call `tools/extract_dispersion_from_cc_mat.py` in root-scan mode.
#   2) Recursively process many MAT folders under one root.
#   3) Save mirrored per-folder dispersion PNG/NPZ outputs.
#
# Usage:
#   ./run_multi_folder_dispersion.sh --input-root /path/to/restack_root
#
# Example:
#   ./run_multi_folder_dispersion.sh \
#     --input-root ./outputs/cc_tsi_plot_outputs_by_folder \
#     --output-root ./outputs/dispersion_outputs_by_folder \
#     --config ./config/disp_config.jsonc \
#     --workers 8 \
#     -- --save-npz --overwrite
#
# 参数：
#   --input-root PATH    必填。递归扫描 `.mat` 文件的根目录。
#   --output-root PATH   颜色谱图/立方体输出根目录。
#   --config PATH        颜色谱图配置 JSONC。默认：./config/disp_config.jsonc
#   --glob PATTERN       MAT 文件名模式。默认：restack_*.mat
#   --method NAME        可选的方法覆盖参数。
#   --shot-start N       可选。包含的起始 shot 编号。
#   --shot-stop N        可选。不包含的结束 shot 编号。
#   --workers N          CPU 进程数。默认：8
#   --cc-cmap NAME       CC 色图；默认 RdYlBu_r。
#   --dispersion-cmap NAME 频散色图；默认 reference。
#   --help               显示帮助信息。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --input-root PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --input-root PATH     Required. Root directory scanned recursively.
  --output-root PATH    Default: ${SCRIPT_DIR}/outputs/dispersion_outputs_by_folder
  --config PATH         Default: ${SCRIPT_DIR}/config/disp_config.jsonc
  --glob PATTERN        Default: restack_*.mat
  --method NAME         Optional dispersion method override.
  --shot-start N        Optional inclusive shot start.
  --shot-stop N         Optional exclusive shot stop.
  --workers N           Default: 8
  --cc-cmap NAME        Default: RdYlBu_r
  --dispersion-cmap NAME Default: reference
  --help                Show help.
EOF
  print_common_footer
}

INPUT_ROOT=""
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/dispersion_outputs_by_folder"
CONFIG="${SCRIPT_DIR}/config/disp_config.jsonc"
GLOB_PATTERN="restack_*.mat"
METHOD=""
SHOT_START=""
SHOT_STOP=""
WORKERS="8"
CC_CMAP="RdYlBu_r"
DISPERSION_CMAP="reference"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-root) INPUT_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --glob) GLOB_PATTERN="$2"; shift 2 ;;
    --method) METHOD="$2"; shift 2 ;;
    --shot-start) SHOT_START="$2"; shift 2 ;;
    --shot-stop) SHOT_STOP="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --cc-cmap) CC_CMAP="$2"; shift 2 ;;
    --dispersion-cmap) DISPERSION_CMAP="$2"; shift 2 ;;
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
  "${SCRIPT_DIR}/tools/extract_dispersion_from_cc_mat.py"
  --input-root "${INPUT_ROOT}"
  --output-root "${OUTPUT_ROOT}"
  --config "${CONFIG}"
  --glob "${GLOB_PATTERN}"
  --workers "${WORKERS}"
  --cc-cmap "${CC_CMAP}"
  --dispersion-cmap "${DISPERSION_CMAP}"
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
