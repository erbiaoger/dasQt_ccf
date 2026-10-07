#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_shell_common.sh"

# 修改 config/seasonal_diagnosis.jsonc 可调整位置、日期、时段、抽样、频谱和FK参数（仅原始DAT预处理诊断）。
# 额外命令行参数会覆盖配置。例：
# ./run_seasonal_diagnosis.sh --dates 2025-03-27 2025-06-10 --positions-m 400 4400 8400
# ./run_seasonal_diagnosis.sh --set spectrum_fmax_hz=50 --set fk_vmin_mps=250
# ./run_seasonal_diagnosis.sh --phase plot  # 只用已保存数据重新出图
# ./run_seasonal_diagnosis.sh --phase status  # 查看长任务完成数与失败数
ensure_dasqt_env
UV_BIN="$(command -v uv || true)"
if [[ -z "${UV_BIN}" ]]; then
  UV_BIN="${HOME}/.local/bin/uv"
fi
# 激活项目环境，通过 uv 使用已有依赖；不在运行时同步或升级依赖。
source "${DASQT_VENV_ACTIVATE}"
cd "${DASQT_REPO_DIR}"
exec "${UV_BIN}" run --no-sync python "${SCRIPT_DIR}/scripts/seasonal_diagnosis.py" \
  --config "${SCRIPT_DIR}/config/seasonal_diagnosis.jsonc" "$@"
