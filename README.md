# 03_ccf

本目录用于 DAS 互相关 / TSI 结果的单目录计算、批量计算、二次叠加与可视化。

## 目录作用

- [caculate_cc_tsi_from_folder.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/caculate_cc_tsi_from_folder.py): 对单个数据目录计算 CC / TSI，并周期性保存 `cc_stack*.mat`。
- [batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py): 扫描一个根数据目录下的所有子目录，逐个调用单目录脚本。
- [stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/stack_saved_cc_mat.py): 对单个输出目录中的多个 `cc_stack*.mat` 做二次叠加、出图、保存 `restack*.mat`。
- [batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_stack_saved_cc_mat.py): 批量扫描 `cc_tsi_outputs_by_folder` 这类目录，对每个子目录执行二次叠加，支持多文件夹 CPU 并行。
- [config](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/config): 各数据源的配置目录。
- [tools](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools): 离线辅助 CLI 工具目录，负责格式转换和索引构建。
- [notebooks](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/notebooks): Notebook 目录，用于磁盘后端数据集构建与探索分析。
- [outputs](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/outputs): 示例输出根目录。
- [run.sh](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/run.sh): 常用命令示例。

## 推荐工作流

1. 先用 [batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py) 生成每个日期/子目录的 `cc_stack_tsi_*.mat`。
2. 再用 [batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_stack_saved_cc_mat.py) 批量对这些目录做二次叠加。
3. 再用 [build_cc_h5_cube.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools/build_cc_h5_cube.py) 把每个日期目录里的 `.mat` 汇总成磁盘后端 HDF5 立方体。
4. 最后用 [build-disk-backed-changbai-cc-cube.ipynb](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/notebooks/build-disk-backed-changbai-cc-cube.ipynb) 只读 HDF5 并做绘图分析。
5. 如果需要逐炮 CC+频散对照图，使用 [extract_dispersion_from_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools/extract_dispersion_from_cc_mat.py) 从每个 `.mat` 直接批量提取并保存。
6. 如果需要把固定炮号在所有日期/子目录中的频散图串成动图，使用 [create_shot_dispersion_gif.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools/create_shot_dispersion_gif.py) 生成 GIF。

## ChangBai 示例

先批量计算:

```bash
uv run python batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
  --output-root ./outputs/cc_tsi_outputs_by_folder \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --save-every 30 \
  --date-start 2025-03-27 \
  --time-start 08:00 \
  --time-end 18:00 \
  --tsi-progress
```

ChangBai 的推荐 GPU 配置在 `config/config_ChangBai/cc_config.jsonc` 中，默认启用:

- `cc_device=all_cuda`
- `tsi_mode=single_shot_local_cc`
- `cc_prewhiten_segment_once=true`
- `cc_auto_batch=true`

如果需要查看瓶颈拆分，可以临时加 `--profile-stages`。输出会包含 `read/prewhiten/gpu_cc/stack_runtime/cpu_transfer/align` 等耗时；如果 CUDA 显存不足，local CC batch 会自动减半重试。

再批量叠加:

```bash
uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend cpu \
  --cc-device cpu \
  --jobs 8 \
  --plot-name local_cc_preview.png
```

如果只想保存 `restack_*.mat`，不出任何图:

```bash
uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --no-save-summary-plot \
  --no-save-shot-figures
```

这里的 `--jobs` 是“多文件夹并行度”:

- `--jobs 1`: 串行逐目录叠加。
- `--jobs 8`: 最多同时启动 8 个子目录叠加进程，适合多核 CPU。
- 并行单位是“文件夹”，不是单个文件夹内部的 shot。

这里的 `--cc-backend / --cc-device / --cc-dtype` 是“叠加计算设备”:

- `--cc-backend cpu --cc-device cpu`: 强制走 CPU。
- `--cc-backend auto --cc-device auto`: 自动选择可用设备。
- `--cc-backend torch --cc-device mps`: 强制走 Apple Silicon GPU。
- `--cc-backend torch --cc-device cuda`: 强制走 CUDA GPU。
- `linear / pws / robust` 都会按你指定的后端执行；如果你请求的 torch 设备不可用，底层会发出 warning 并回退到 CPU。

输出会按相同相对路径写入:

- 输入: `./outputs/cc_tsi_outputs_by_folder/2025-03-27`
- 输出: `./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27`

再转换成 HDF5:

```bash
uv run python tools/build_cc_h5_cube.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
  --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv
```

批量提取所有炮的 CC+频散对照图:

```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/disp_config.jsonc \
  --workers 8
```

这里的 `--workers` 是 CPU 多进程并行度，并行单位为单个 `.mat` 文件，因此可以同时跑多个日期/子目录。

只处理单个文件夹:

```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
  --output-root ./outputs/dispersion_outputs_ChangBai_single \
  --config ./config/config_ChangBai/disp_config.jsonc
```

把第 179 炮在所有子目录中的频散图合成 GIF:

```bash
uv run python tools/create_shot_dispersion_gif.py \
  --input-root ./outputs/dispersion_outputs_ChangBai \
  --shot-index 179 \
  --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif
```

## Python 环境

按项目约定，运行 Python 时优先使用项目自己的 `.venv`，并通过 `uv run python ...` 执行。
