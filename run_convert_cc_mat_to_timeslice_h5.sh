#!/usr/bin/env bash
# 长时 CCF MAT → HDF5 时间片一键转换脚本。
#
# 用法：
#   1. 修改下面“可编辑参数”区域中的输入、输出和匹配参数；
#   2. 在本目录执行：./run_convert_cc_mat_to_timeslice_h5.sh
#
# 脚本使用项目 .venv 和 uv，原始 MAT 文件不会被修改或删除。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_shell_common.sh"

# ========================= 可编辑参数 =========================
# MAT 文件根目录：脚本会递归扫描此目录。
INPUT_ROOT="/path/to/mat_root"
# HDF5 时间片和 manifest.json 输出目录。
OUTPUT_ROOT="${SCRIPT_DIR}/outputs/ccf_timeslices"
# 可选：自定义 manifest 路径；留空则写入 OUTPUT_ROOT/manifest.json。
MANIFEST_PATH=""
# MAT 文件匹配模式；每个匹配文件都会作为一个独立时间片。
MAT_GLOB="restack_*.mat"
# 压缩方式：gzip（体积较小）、lzf（读取较快）或 none（不压缩）。
COMPRESSION="gzip"
# gzip 压缩级别，仅 COMPRESSION=gzip 时有效，范围 0-9。
GZIP_LEVEL="4"
# 只试跑前 N 个时间片；留空表示全部转换。
MAX_SLICES=""
# 是否允许不同时间片的 dt/dx 等几何元数据不一致：1=允许，0=不允许。
ALLOW_METADATA_MISMATCH="0"
# 是否覆盖已经存在的 HDF5 文件：1=允许，0=保护已有文件。
OVERWRITE="0"
# 是否只扫描并打印计划、不写入文件：1=dry-run，0=实际转换。
DRY_RUN="0"
# =============================================================

CMD=(
  "examples/03_ccf/scripts/convert_cc_mat_to_timeslice_h5.py"
  --input-root "${INPUT_ROOT}"
  --output-root "${OUTPUT_ROOT}"
  --glob "${MAT_GLOB}"
  --compression "${COMPRESSION}"
  --gzip-level "${GZIP_LEVEL}"
)

if [[ -n "${MANIFEST_PATH}" ]]; then
  CMD+=(--manifest "${MANIFEST_PATH}")
fi
if [[ -n "${MAX_SLICES}" ]]; then
  CMD+=(--max-slices "${MAX_SLICES}")
fi
if [[ "${ALLOW_METADATA_MISMATCH}" == "1" ]]; then
  CMD+=(--allow-metadata-mismatch)
fi
if [[ "${OVERWRITE}" == "1" ]]; then
  CMD+=(--overwrite)
fi
if [[ "${DRY_RUN}" == "1" ]]; then
  CMD+=(--dry-run)
fi

echo "[INFO] input root : ${INPUT_ROOT}"
echo "[INFO] output root: ${OUTPUT_ROOT}"
echo "[INFO] MAT glob   : ${MAT_GLOB}"
run_uv_python "${CMD[@]}"
