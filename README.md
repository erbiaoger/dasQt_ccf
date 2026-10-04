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

## 长时 CCF 时间片转换

编辑 `run_convert_cc_mat_to_timeslice_h5.sh` 顶部的输入目录、输出目录和转换参数，
然后直接运行：

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf
./run_convert_cc_mat_to_timeslice_h5.sh
```

该脚本调用 `scripts/convert_cc_mat_to_timeslice_h5.py`，把所有匹配的 MAT 文件合并
到一个 HDF5 文件，并生成统一的 `manifest.json`。设置 `DRY_RUN="1"` 可先检查扫描结果，
设置 `OVERWRITE="1"` 才会覆盖已存在的 HDF5 文件。

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

### 真实数据：前 5 个文件与后端比较

`run_multi_folder_compute.sh` 默认复用 `~/.dasQt/config/cc_config.jsonc`，没有用户配置时回退示例配置。
设备默认自动检测；`--cc-backend cpp --cc-threads 4` 启用 C++ 四线程，
`--cc-backend torch --cc-device mps` 指定 Apple GPU。原生预算使用 MiB：8192 = 8 GiB。
通过 `--no-get-disp1-compatible --start-index 0 --end-index 4` 精确选择前 5 个文件；
`--save-every 5` 合为一组。直接存放 HDF5 的目录需要 `--include-root`。
比较时请复制配置为固定快照，避免 GUI 保存参数期间改变两次计算的工作量。

```bash
bash examples/03_ccf/run_multi_folder_compute.sh \
  --config docs/benchmarks/real_five_20260909/config.json \
  --root-data-dir /Volumes/SanDisk4T/2026-09-09 --include-root \
  --no-get-disp1-compatible --start-index 0 --end-index 4 --save-every 5 \
  --workers 1 --cc-backend cpp --cc-threads 4 --cc-memory-budget-mb 8192 \
  --profile-stages --output-root examples/03_ccf/outputs/cpp_five \
  -- --cc-prefetch-batches 1 --no-tsi-progress
```

对照 MPS 时替换后端为 `--cc-backend torch --cc-device mps` 并改输出目录。
脚本通过项目 `.venv` 的 `uv run --no-sync` 执行，避免无意同步替换正在测试的原生扩展。

与 MPS 做数值对照时，请显式添加 `--cc-prewhiten-mode off`。默认 full 模式在原 CPU/C++ 兼容路径中丢弃谱虚部，MPS 保留复谱，两者结果定义不完全一致。实测及误差见 `docs/benchmarks/real_five_20260909/README.md`。

### TSI 文件分组与保存频率独立

配置 `tsi_files_per_group`（或 CLI `--tsi-files-per-group`）控制每组多少个有效文件先叠加再增强。
默认 0 沿用旧行为。例：选取 60 文件，`--tsi-files-per-group 10 --save-every 60`，
执行 6 次 TSI，最后使用 stacking_method 叠加这 6 组增强炮集，保存一份结果，不再执行一次 TSI。
GUI 在 Advanced Settings → TSI → Files per TSI 设置；总文件数仍由原来的文件数量设置控制。

保存参数是独立的文件数量阈值；仅在完整 TSI 组完成后保存，不切断 TSI 组。
例如每 10 文件 TSI、每 15 文件保存，实际首份包含 20 文件。
新分组模式在输入结束时处理不足 N 的尾组，并保存不足保存阈值的最后结果。
各 TSI 组等权参与最终叠加，短尾组也占一组权重；诊断和 MAT 记录真实文件数及各组大小。
TSI 为非线性运算，分组增强再叠加通常不等于全部文件叠加后增强。
该参数不适用于 single_shot_final_once；同时启用会明确报错。

### 最后不足一组时的保存

`--save-every 20` 每累计 20 个有效文件保存一次；结束时剩余不足 20 个也会单独保存。
例如 32 个有效文件保存为 20 + 12 两份，只有 12 个也会保存一份；整除时不额外生成空结果。
文件名的 `n12` 与 MAT 的 `actual_file_count` 记录该组实际文件数，`source_files` 记录来源。
单目录、多目录入口均自动采用此行为，无需额外参数。
