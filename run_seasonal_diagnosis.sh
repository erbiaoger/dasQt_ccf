#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${SCRIPT_DIR}/../DAS_Procee_Show/.venv/bin/python"

# 修改 config/seasonal_diagnosis.jsonc 可调整位置、日期、时段、抽样、频谱、FK和频散参数。
# 额外命令行参数会覆盖配置。例：
# ./run_seasonal_diagnosis.sh --dates 2025-03-27 2025-06-10 --positions-m 400 4400 8400
# ./run_seasonal_diagnosis.sh --set disp_fmax_hz=20 --set fk_vmin_mps=250
# ./run_seasonal_diagnosis.sh --phase plot  # 只用已保存数据重新出图
# ./run_seasonal_diagnosis.sh --phase status  # 查看长任务完成数与失败数
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/seasonal_diagnosis.py" \
  --config "${SCRIPT_DIR}/config/seasonal_diagnosis.jsonc" "$@"
