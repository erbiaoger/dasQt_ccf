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
#     --config ./config/config_ChangBai/cc_config.jsonc \
#     --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317 \
#     --output-root ./outputs/cc_tsi_outputs_by_folder \
#     --save-every 30 \
#     --date-start 2025-03-27 \
#     --time-start 08:00 \
#     --time-end 18:00 \
#     -- --cc-enhance tsi --refresh-file-index
#
# Parameters:
#   --config PATH          Batch config. Default: ./config/config_ChangBai/cc_config.jsonc
#   --root-data-dir PATH   Required. Root folder whose subfolders contain DAS files.
#   --output-root PATH     Output root. Default: ./outputs/cc_tsi_outputs_by_folder
#   --save-every N         Passed to single-folder compute. Default: 30
#   --start-index N        Passed to single-folder compute. Default: 0
#   --end-index N          Passed to single-folder compute. Default: unset
#   --workers N            Passed to single-folder compute. Default: 1
#   --date-start YYYY-MM-DD  Optional folder date filter.
#   --time-start HH:MM     Optional file time lower bound. Must pair with --time-end.
#   --time-end HH:MM       Optional file time upper bound. Must pair with --time-start.
#   --recursive            Recursively scan all nested subfolders.
#   --include-root         Also process the root folder itself if it directly contains data files.
#   --file-list-name NAME  Optional per-folder manifest filename.
#   --help                 Show this message.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --root-data-dir PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --config PATH           Default: ${SCRIPT_DIR}/config/config_ChangBai/cc_config.jsonc
  --root-data-dir PATH    Required. Root DAS folder.
  --output-root PATH      Default: ${SCRIPT_DIR}/outputs/cc_tsi_outputs_by_folder
  --save-every N          Default: 30
  --start-index N         Default: 0
  --end-index N           Default: unset
  --workers N             Default: 1
  --date-start DATE       Optional YYYY-MM-DD filter.
  --time-start HH:MM      Optional time filter lower bound.
  --time-end HH:MM        Optional time filter upper bound.
  --recursive             Enable recursive folder scan.
  --include-root          Include root-data-dir itself.
  --file-list-name NAME   Optional per-folder manifest filename.
  --help                  Show help.
EOF
  print_common_footer
}

CONFIG="${SCRIPT_DIR}/config/config_ChangBai/cc_config.jsonc"
ROOT_DATA_DIR=""
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/cc_tsi_outputs_by_folder"
SAVE_EVERY="30"
START_INDEX="0"
END_INDEX=""
WORKERS="1"
DATE_START=""
TIME_START=""
TIME_END=""
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
    --end-index) END_INDEX="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --date-start) DATE_START="$2"; shift 2 ;;
    --time-start) TIME_START="$2"; shift 2 ;;
    --time-end) TIME_END="$2"; shift 2 ;;
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
  "${SCRIPT_DIR}/batch_caculate_cc_tsi_from_subfolders.py"
  --root-data-dir "${ROOT_DATA_DIR}"
  --output-root "${OUTPUT_ROOT}"
  --config "${CONFIG}"
  --save-every "${SAVE_EVERY}"
  --start-index "${START_INDEX}"
  --workers "${WORKERS}"
)

if [[ -n "${END_INDEX}" ]]; then
  CMD+=(--end-index "${END_INDEX}")
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
