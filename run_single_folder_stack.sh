#!/usr/bin/env bash
# Restack saved CC/TSI MAT files for one output folder.
#
# Purpose:
#   1) Call `stack_saved_cc_mat.py`.
#   2) Read one output folder containing `cc_stack*.mat`.
#   3) Save `restack*.mat`, summary plot, and optional shot figures.
#
# Usage:
#   ./run_single_folder_stack.sh --input-dir /path/to/cc_output_folder
#
# Example:
#   ./run_single_folder_stack.sh \
#     --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-27 \
#     --output-dir ./outputs/restack_single/2025-03-27 \
#     --glob "cc_stack_tsi_*.mat" \
#     --stack-size None \
#     --interval None \
#     --stack-method robust \
#     --cc-backend cpu \
#     --cc-device cpu
#
# 参数：
#   --input-dir PATH     必填。包含已保存 `cc_stack*.mat` 的目录。
#   --output-dir PATH    `restack*.mat` 和图像的输出目录。
#   --glob PATTERN       MAT 文件名模式。默认：cc_stack_tsi_*.mat
#   --stack-size VALUE   窗口文件数。使用 None 表示全部文件。默认：None
#   --interval VALUE     窗口步长。使用 None 表示单组。默认：None
#   --start-index N      MAT 文件起始索引。默认：0
#   --stack-method NAME  堆叠方法：pws | robust | linear | mean。默认：robust
#   --cc-backend NAME    计算后端：auto | cpu | torch。默认：cpu
#   --cc-device NAME     计算设备：auto | cpu | mps | cuda。默认：cpu
#   --cc-dtype NAME      数据类型：float32 | float64。默认：float32
#   --plot-name NAME     汇总图文件名。默认：local_cc_preview.png
#   --show               保存后同时显示图像。
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
  --input-dir PATH      Required. Directory containing saved MAT files.
  --output-dir PATH     Default: ${SCRIPT_DIR}/outputs/restack_single
  --glob PATTERN        Default: cc_stack_tsi_*.mat
  --stack-size VALUE    Default: None
  --interval VALUE      Default: None
  --start-index N       Default: 0
  --stack-method NAME   Default: robust
  --cc-backend NAME     Default: cpu
  --cc-device NAME      Default: cpu
  --cc-dtype NAME       Default: float32
  --plot-name NAME      Default: local_cc_preview.png
  --show                Also show figures.
  --help                Show help.
EOF
  print_common_footer
}

INPUT_DIR=""
OUTPUT_DIR="${SCRIPT_DIR}/outputs/restack_single"
GLOB_PATTERN="tsi_*.mat"
STACK_SIZE="None"
INTERVAL="None"
START_INDEX="0"
STACK_METHOD="robust"
CC_BACKEND="cpu"
CC_DEVICE="cpu"
CC_DTYPE="float32"
PLOT_NAME="local_cc_preview.png"
SHOW="0"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir) INPUT_DIR="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --glob) GLOB_PATTERN="$2"; shift 2 ;;
    --stack-size) STACK_SIZE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --start-index) START_INDEX="$2"; shift 2 ;;
    --stack-method) STACK_METHOD="$2"; shift 2 ;;
    --cc-backend) CC_BACKEND="$2"; shift 2 ;;
    --cc-device) CC_DEVICE="$2"; shift 2 ;;
    --cc-dtype) CC_DTYPE="$2"; shift 2 ;;
    --plot-name) PLOT_NAME="$2"; shift 2 ;;
    --show) SHOW="1"; shift ;;
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
  "${SCRIPT_DIR}/scripts/stack_saved_cc_mat.py"
  --input-dir "${INPUT_DIR}"
  --glob "${GLOB_PATTERN}"
  --output-dir "${OUTPUT_DIR}"
  --stack-size "${STACK_SIZE}"
  --interval "${INTERVAL}"
  --start-index "${START_INDEX}"
  --stack-method "${STACK_METHOD}"
  --cc-backend "${CC_BACKEND}"
  --cc-device "${CC_DEVICE}"
  --cc-dtype "${CC_DTYPE}"
  --plot-name "${PLOT_NAME}"
)

if [[ "${SHOW}" == "1" ]]; then
  CMD+=(--show)
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
