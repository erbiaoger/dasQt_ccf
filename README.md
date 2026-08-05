# 03_ccf

本目录是当前 `dasQt` 仓库的 CCF / TSI 示例工作流，覆盖：

1. DAS 文件夹的互相关与 TSI 计算；
2. 已保存 CC 结果的二次叠加；
3. 逐炮频散提取；
4. 频散图片与 HDF5 立方体等后处理。

示例脚本使用当前仓库中的功能 API：

```python
from dasqt.features.dispersion.backend.components.ccf import (
    run_cc_tsi_folder_pipeline,
    run_batch_ccf,
    run_batch_restack,
)
from dasqt.features.dispersion.backend.components.utils.dispersion_utils import (
    compute_dispersion_image,
)
```

## 目录结构

- `config/`：当前示例使用的 JSONC 配置，包含 `cc_config.jsonc` 和 `disp_config.jsonc`。
- `scripts/`：CC/TSI 计算、二次叠加和结果整理 CLI。
- `tools/`：频散提取、GIF、HDF5 立方体等后处理工具。
- `notebooks/`：探索分析 notebook。
- `outputs/`：本地运行输出，已加入忽略规则。
- `run_*.sh`：带中文参数说明的可执行入口。

## 推荐工作流

所有命令从本目录执行。shell 入口会切到仓库 `.venv`，并用 `uv run python` 调用脚本。

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf

# 1. 计算单个数据文件夹
./run_single_folder_compute.sh \
  --config ./config/cc_config.jsonc \
  --data-folder /path/to/das-folder \
  --output-dir ./outputs/cc_tsi_outputs_single \
  --save-every 30 \
  -- --cc-enhance tsi --tsi-progress

# 2. 对多个子文件夹批量计算
./run_multi_folder_compute.sh \
  --config ./config/cc_config.jsonc \
  --root-data-dir /path/to/das-root \
  --output-root ./outputs/cc_tsi_outputs_by_folder

# 3. 二次叠加
./run_single_folder_stack.sh \
  --input-dir ./outputs/cc_tsi_outputs_single \
  --output-dir ./outputs/restack_single \
  --glob 'cc_stack_tsi_*.mat' \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend cpu \
  --cc-device cpu

# 4. 批量二次叠加
./run_multi_folder_stack.sh \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_by_folder

# 5. 提取频散
./run_single_folder_dispersion.sh \
  --input-dir ./outputs/restack_single \
  --output-root ./outputs/dispersion_single \
  --config ./config/disp_config.jsonc

# 6. 生成 GIF
./run_dispersion_gif.sh \
  --input-root ./outputs/dispersion_single \
  --all-images \
  --output-gif ./outputs/dispersion_single/all.gif
```

如需向底层 Python CLI 传参，在 shell 命令末尾使用 `--`，例如：

```bash
./run_multi_folder_dispersion.sh \
  --input-root ./outputs/cc_tsi_plot_outputs_by_folder \
  --output-root ./outputs/dispersion_outputs_by_folder \
  --config ./config/disp_config.jsonc \
  --workers 8 \
  -- --save-npz --overwrite
```

CC/TSI 的完整参数说明见 [`scripts/README.md`](scripts/README.md)，辅助工具说明见 [`tools/README.md`](tools/README.md)。

## 直接运行 Python CLI

从仓库根目录运行时：

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt
uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py --help
uv run python examples/03_ccf/scripts/stack_saved_cc_mat.py --help
uv run python examples/03_ccf/tools/extract_dispersion_from_cc_mat.py --help
```

绘图工具已统一设置 `Times New Roman` 字体。长时间计算可以把上述命令放入 `tmux` 窗口运行。
