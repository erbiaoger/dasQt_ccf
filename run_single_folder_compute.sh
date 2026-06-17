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
#     --config ./config/config_ChangBai/cc_config.jsonc \
#     --data-folder /mnt/nas/DATA/DAS/2024-10-14-ShenZhen/20241016_gau2m/2024-10-16 \
#     --output-dir ./outputs/cc_tsi_outputs_single \
#     --save-every 30 \
#     --start-index 10 \
#     --end-index 10 \
#     -- --cc-enhance tsi --tsi-mode single_shot_local_cc --tsi-progress
#
# Parameters:
#   --config PATH       CC/TSI JSONC config. Default: ./config/config_ChangBai/cc_config.jsonc
#   --data-folder PATH  Required. Folder containing DAS files.
#   --output-dir PATH   Output folder for grouped MAT/JSON files.
#   --save-every N      Save one grouped output every N valid files. Default: 30
#   --start-index N     Start file index after sorting/filtering. Default: 0
#   --end-index N       End file index (inclusive). Default: unset
#   --workers N         Worker count passed to Python CLI. Default: 1
#   --help              Show this message.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --data-folder PATH [options] [-- EXTRA_PY_ARGS...]

Options:
  --config PATH       Default: ${SCRIPT_DIR}/config/config_ChangBai/cc_config.jsonc
  --data-folder PATH  Required. DAS input folder.
  --output-dir PATH   Default: ${SCRIPT_DIR}/outputs/cc_tsi_outputs_single
  --save-every N      Default: 30
  --start-index N     Default: 0
  --end-index N       Default: unset
  --workers N         Default: 1
  --help              Show help.
EOF
  print_common_footer
}

CONFIG="${SCRIPT_DIR}/config/config_ChangBai/cc_config.jsonc"
DATA_FOLDER=""
OUTPUT_DIR="${SCRIPT_DIR}/outputs/cc_tsi_outputs_single"
SAVE_EVERY="30"
START_INDEX="0"
END_INDEX=""
WORKERS="1"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --data-folder) DATA_FOLDER="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --save-every) SAVE_EVERY="$2"; shift 2 ;;
    --start-index) START_INDEX="$2"; shift 2 ;;
    --end-index) END_INDEX="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
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

if [[ -n "${END_INDEX}" ]]; then
  CMD+=(--end-index "${END_INDEX}")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

run_uv_python "${CMD[@]}"
