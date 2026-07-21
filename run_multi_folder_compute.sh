#!/usr/bin/env bash
# Batch-run CC/TSI compute for multiple subfolders under one root.
#
# Purpose:
#   1) Call `batch_caculate_cc_tsi_from_subfolders.py`.
#   2) Process one root DAS directory containing many date/subfolders.
#   3) Save each folder's results to a mirrored output tree.
#
# Usage:
#   ./run_multi_folder_compute.sh --root-data-dir /path/to/root
#
# Example:
#   ./run_multi_folder_compute.sh \
#     --config ./config/cc_config.jsonc \
#     --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317 \
#     --output-root ./outputs/cc_tsi_outputs_by_folder \
#     --save-every 30 \
#     --date-start 2025-03-27 \
#     --time-start 08:00 \
#     --time-end 18:00 \
#     --cc-prewhiten-mode auto \
#     --cc-prewhiten-tile-channels 512 \
#     --cc-memory-safety-fraction 0.70 \
#     --profile-stages \
#     -- --cc-enhance tsi --refresh-file-index
#
# 参数：
#   --config PATH          批处理配置文件。默认：./config/cc_config.jsonc
#   --root-data-dir PATH   必填。包含多个子文件夹的 DAS 根目录。
#   --output-root PATH     输出根目录。默认：./outputs/cc_tsi_outputs_by_folder
#   --save-every N         传给单文件计算的保存步长。默认：30
#   --start-index N        传给单文件计算的起始索引。默认：0
#   --get-disp1-compatible / --no-get-disp1-compatible
#                        默认开启。把 start-index 当作锚点文件。
#   --end-index N          传给单文件计算的结束索引。默认：不设置
#   --workers N            传给单文件计算的工作进程数。默认：1
#   --cc-prewhiten-mode MODE    传给单文件计算的预白化模式。默认：不设置
#   --cc-prewhiten-tile-channels N  传给单文件计算的 tiled 通道数。默认：不设置
#   --cc-memory-safety-fraction X   传给单文件计算的 CUDA 内存安全系数。默认：不设置
#   --profile-stages       传给单文件计算的阶段日志开关。
#   --date-start YYYY-MM-DD  可选。日期过滤下限。
#   --time-start HH:MM     可选。时间过滤下限，需与 --time-end 同时使用。
#   --time-end HH:MM       可选。时间过滤上限，需与 --time-start 同时使用。
#   --filename-time-format FMT
#                          可选。文件名时间格式，例如：%Y%m%d_%H%M%S。
#   --recursive            递归扫描所有子文件夹。
#   --include-root         也处理 root-data-dir 本身（如果它直接包含数据文件）。
#   --file-list-name NAME  可选。每个子文件夹使用的清单文件名。
#   --help                 显示帮助信息。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --root-data-dir PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --config PATH           Default: ${SCRIPT_DIR}/config/cc_config.jsonc
  --root-data-dir PATH    Required. Root DAS folder.
  --output-root PATH      Default: ${SCRIPT_DIR}/outputs/cc_tsi_outputs_by_folder
  --save-every N          Default: 30
  --start-index N         Default: 0
  --get-disp1-compatible
                         Default: on. start-index becomes the anchor file.
  --end-index N           Default: unset
  --workers N             Default: 1
  --cc-prewhiten-mode MODE
                         Optional: auto | full | tiled | off
  --cc-prewhiten-tile-channels N
                         Optional tiled prewhiten channel count
  --cc-memory-safety-fraction X
                         Optional free-memory safety fraction, e.g. 0.70
  --profile-stages        Enable per-file profiling logs
  --date-start DATE       Optional YYYY-MM-DD filter.
  --time-start HH:MM      Optional time filter lower bound.
  --time-end HH:MM        Optional time filter upper bound.
  --filename-time-format FMT
                         Optional Python datetime.strptime filename timestamp format.
                         Example: %%Y%%m%%d_%%H%%M%%S
  --recursive             Enable recursive folder scan.
  --include-root          Include root-data-dir itself.
  --file-list-name NAME   Optional per-folder manifest filename.
  --help                  Show help.
EOF
  print_common_footer
}

CONFIG="${SCRIPT_DIR}/config/cc_config.jsonc"
ROOT_DATA_DIR=""
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/cc_tsi_outputs_by_folder"
SAVE_EVERY="30"
START_INDEX="0"
END_INDEX=""
WORKERS="1"
GET_DISP1_COMPATIBLE="1"  # 默认开启：批处理窗口按 Get Disp1 语义取后续文件
CC_PREWHITEN_MODE=""
CC_PREWHITEN_TILE_CHANNELS=""
CC_MEMORY_SAFETY_FRACTION=""
PROFILE_STAGES="0"  # 阶段日志开关：0 关闭，1 开启
DATE_START=""
TIME_START=""
TIME_END=""
# 文件名中的时间戳格式。示例文件 ..._20260603_235022.778.h5 使用：
# %Y%m%d_%H%M%S（不含毫秒也可以）。启用时间过滤时请确认此处格式正确。
FILENAME_TIME_FORMAT="%Y%m%d_%H%M%S"
RECURSIVE="0"
INCLUDE_ROOT="0"
FILE_LIST_NAME=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --root-data-dir) ROOT_DATA_DIR="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
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
    --date-start) DATE_START="$2"; shift 2 ;;
    --time-start) TIME_START="$2"; shift 2 ;;
    --time-end) TIME_END="$2"; shift 2 ;;
    --filename-time-format) FILENAME_TIME_FORMAT="$2"; shift 2 ;;
    --recursive) RECURSIVE="1"; shift ;;
    --include-root) INCLUDE_ROOT="1"; shift ;;
    --file-list-name) FILE_LIST_NAME="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${ROOT_DATA_DIR}" ]]; then
  echo "[ERROR] --root-data-dir is required." >&2
  usage
  exit 1
fi

CMD=(
  "${SCRIPT_DIR}/scripts/batch_caculate_cc_tsi_from_subfolders.py"
  --root-data-dir "${ROOT_DATA_DIR}"
  --output-root "${OUTPUT_ROOT}"
  --config "${CONFIG}"
  --save-every "${SAVE_EVERY}"
  --start-index "${START_INDEX}"
  --workers "${WORKERS}"
)

if [[ "${GET_DISP1_COMPATIBLE}" == "1" ]]; then
  CMD+=(--get-disp1-compatible)
else
  CMD+=(--no-get-disp1-compatible)
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
if [[ -n "${DATE_START}" ]]; then
  CMD+=(--date-start "${DATE_START}")
fi
if [[ -n "${TIME_START}" ]]; then
  CMD+=(--time-start "${TIME_START}")
fi
if [[ -n "${TIME_END}" ]]; then
  CMD+=(--time-end "${TIME_END}")
fi
if [[ -n "${TIME_START}" || -n "${TIME_END}" ]]; then
  if [[ -z "${FILENAME_TIME_FORMAT}" ]]; then
    echo "[ERROR] FILENAME_TIME_FORMAT must be set when time filtering is enabled." >&2
    exit 1
  fi
  CMD+=(--filename-time-format "${FILENAME_TIME_FORMAT}")
fi
if [[ "${RECURSIVE}" == "1" ]]; then
  CMD+=(--recursive)
fi
if [[ "${INCLUDE_ROOT}" == "1" ]]; then
  CMD+=(--include-root)
fi
if [[ -n "${FILE_LIST_NAME}" ]]; then
  CMD+=(--file-list-name "${FILE_LIST_NAME}")
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
