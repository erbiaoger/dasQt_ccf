#!/usr/bin/env bash
# Run CC/TSI compute for one DAS data folder.
#
# Purpose:
#   1) Call `caculate_cc_tsi_from_folder.py` for a single data folder.
#   2) Save grouped `cc_stack*.mat` and optional `*_diag.json` files.
#   3) Keep the command reproducible as a reusable shell wrapper.
#
# Usage:
#   ./run_single_folder_compute.sh --data-folder /path/to/data
#
# Example:
#   ./run_single_folder_compute.sh \
#     --config ./config/cc_config.jsonc \
#     --data-folder /mnt/nas/DATA/DAS/2024-10-14-ShenZhen/20241016_gau2m/2024-10-16 \
#     --output-dir ./outputs/cc_tsi_outputs_single \
#     --save-every 30 \
#     --start-index 10 \
#     --end-index 10 \
#     --cc-prewhiten-mode auto \
#     --cc-prewhiten-tile-channels 512 \
#     --cc-memory-safety-fraction 0.70 \
#     --profile-stages \
#     -- --cc-enhance tsi --tsi-mode single_shot_local_cc --tsi-progress
#
# 参数：
#   --config PATH       CC/TSI JSONC 配置文件。默认：./config/cc_config.jsonc
#   --data-folder PATH  必填。包含 DAS 数据文件的目录。
#   --output-dir PATH   群组 MAT/JSON 输出目录。
#   --save-every N      每累计 N 个有效文件保存一次结果。默认：30
#   --start-index N     排序/过滤后的起始文件索引。默认：0
#   --get-disp1-compatible / --no-get-disp1-compatible
#                     默认开启。把 start-index 当作锚点文件，并处理其后的文件。
#   --end-index N       结束文件索引（包含）。默认：不设置
#   --workers N         传给 Python CLI 的工作进程数。默认：1
#   --cc-prewhiten-mode MODE    auto | full | tiled | off。默认：不设置
#   --cc-prewhiten-tile-channels N  tiled 预白化的分块通道数。默认：不设置
#   --cc-memory-safety-fraction X   规划时允许使用的 CUDA 空闲内存比例。默认：不设置
#   --profile-stages    打印每个文件的阶段耗时日志。
#   --help              显示帮助信息。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --data-folder PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --config PATH       Default: ${SCRIPT_DIR}/config/cc_config.jsonc
  --data-folder PATH  Required. DAS input folder.
  --output-dir PATH   Default: ${SCRIPT_DIR}/outputs/cc_tsi_outputs_single
  --save-every N      Default: 30
  --start-index N     Default: 0
  --get-disp1-compatible
                     Default: on. Treat start-index as the anchor file.
  --end-index N       Default: unset
  --workers N         Default: 1
  --cc-prewhiten-mode MODE
                     Optional: auto | full | tiled | off
  --cc-prewhiten-tile-channels N
                     Optional tiled prewhiten channel count
  --cc-memory-safety-fraction X
                     Optional free-memory safety fraction, e.g. 0.70
  --profile-stages    Enable per-file profiling logs
  --help              Show help.
EOF
  print_common_footer
}

CONFIG="${SCRIPT_DIR}/config/cc_config.jsonc"
DATA_FOLDER=""
OUTPUT_DIR="${SCRIPT_DIR}/outputs/cc_tsi_outputs_single"
SAVE_EVERY="20"
START_INDEX="0"
END_INDEX=""
WORKERS="1"
GET_DISP1_COMPATIBLE="1"     # 默认开启：start-index 作为锚点文件，实际从后一个文件开始处理
CC_PREWHITEN_MODE="auto"    # 预白化模式默认值；留空则不额外传给 Python
CC_PREWHITEN_TILE_CHANNELS=""   # tiled 预白化的通道数；留空则不额外传给 Python
CC_MEMORY_SAFETY_FRACTION=""    # CUDA 空闲内存安全系数；留空则不额外传给 Python
PROFILE_STAGES="0"    # 阶段日志开关：0 关闭，1 开启
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --data-folder) DATA_FOLDER="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --save-every) SAVE_EVERY="$2"; shift 2 ;;
    --start-index) START_INDEX="$2"; shift 2 ;;
    --get-disp1-compatible) GET_DISP1_COMPATIBLE="1"; shift ;;
    --no-get-disp1-compatible) GET_DISP1_COMPATIBLE="0"; shift ;;
    --end-index) END_INDEX="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --cc-prewhiten-mode) CC_PREWHITEN_MODE="$2"; shift 2 ;;
    --cc-prewhiten-tile-channels) CC_PREWHITEN_TILE_CHANNELS="$2"; shift 2 ;;
    --cc-memory-safety-fraction) CC_MEMORY_SAFETY_FRACTION="$2"; shift 2 ;;
    --profile-stages) PROFILE_STAGES="1"; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${DATA_FOLDER}" ]]; then
  echo "[ERROR] --data-folder is required." >&2
  usage
  exit 1
fi

CMD=(
  "${SCRIPT_DIR}/scripts/caculate_cc_tsi_from_folder.py"
  --config "${CONFIG}"
  --data-folder "${DATA_FOLDER}"
  --output-dir "${OUTPUT_DIR}"
  --save-every "${SAVE_EVERY}"
  --start-index "${START_INDEX}"
  --workers "${WORKERS}"
)

if [[ "${GET_DISP1_COMPATIBLE}" == "1" ]]; then
  CMD+=(--start-after-index)
fi

if [[ -n "${END_INDEX}" ]]; then
  CMD+=(--end-index "${END_INDEX}")
fi
if [[ -n "${CC_PREWHITEN_MODE}" ]]; then
  CMD+=(--cc-prewhiten-mode "${CC_PREWHITEN_MODE}")
fi
if [[ -n "${CC_PREWHITEN_TILE_CHANNELS}" ]]; then
  CMD+=(--cc-prewhiten-tile-channels "${CC_PREWHITEN_TILE_CHANNELS}")
fi
if [[ -n "${CC_MEMORY_SAFETY_FRACTION}" ]]; then
  CMD+=(--cc-memory-safety-fraction "${CC_MEMORY_SAFETY_FRACTION}")
fi
if [[ "${PROFILE_STAGES}" == "1" ]]; then
  CMD+=(--profile-stages)
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
