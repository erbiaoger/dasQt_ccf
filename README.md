# 03_ccf

本目录用于 DAS 互相关 / TSI 的计算、二次叠加、频散提取和 GIF 可视化。

## 目录结构

- 根目录保留 7 个 shell 入口脚本，适合直接运行。
- [scripts](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts) 放具体 Python CLI 实现。
- [config](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/config) 放各数据源配置。
- [tools](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/tools) 放辅助工具，如提频散、建 HDF5、生成 GIF。
- [notebooks](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/notebooks) 放探索分析 notebook。
- [outputs](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/outputs) 放示例输出目录。

## 推荐入口

优先使用根目录下这 7 个 shell 脚本。它们会自动：

- 切到 `dasQt` 项目的 `.venv`
- 使用 `uv run python ...`
- 保持参数名和底层 Python CLI 基本一致
- 支持在命令末尾用 `--` 继续透传底层 Python 参数

脚本列表：

- [run_single_folder_compute.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_single_folder_compute.sh): 单文件夹计算 CC / TSI
- [run_multi_folder_compute.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_compute.sh): 多文件夹批量计算 CC / TSI
- [run_single_folder_stack.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_single_folder_stack.sh): 单文件夹二次叠加
- [run_multi_folder_stack.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_stack.sh): 多文件夹二次叠加
- [run_single_folder_dispersion.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_single_folder_dispersion.sh): 单文件夹提频散
- [run_multi_folder_dispersion.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_dispersion.sh): 多文件夹提频散
- [run_dispersion_gif.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_dispersion_gif.sh): 频散图片生成 GIF

公共环境辅助脚本：

- [_shell_common.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/_shell_common.sh)

## Python 脚本

Python CLI 已统一移动到 [scripts](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts)：

- [scripts/caculate_cc_tsi_from_folder.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/caculate_cc_tsi_from_folder.py): 单文件夹计算
- [scripts/batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/batch_caculate_cc_tsi_from_subfolders.py): 多文件夹批量计算
- [scripts/stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/stack_saved_cc_mat.py): 单文件夹二次叠加
- [scripts/batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/batch_stack_saved_cc_mat.py): 多文件夹二次叠加
- [scripts/backfill_shot_position_meta.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/backfill_shot_position_meta.py): 给旧 MAT 回填 shot 空间元数据
- [scripts/plot_cc_m3_shots.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/plot_cc_m3_shots.py): 绘制 shot 级 CC + m3 联图

更多用途和参数说明见：

- [scripts/README.md](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/README.md)

## 推荐工作流

1. 用 [run_multi_folder_compute.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_compute.sh) 生成每个日期/子目录的 `cc_stack_tsi_*.mat`
2. 用 [run_multi_folder_stack.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_stack.sh) 对各目录做二次叠加
3. 用 [run_multi_folder_dispersion.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_multi_folder_dispersion.sh) 提取频散图
4. 用 [run_dispersion_gif.sh](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/run_dispersion_gif.sh) 把同一炮号或同一目录图片合成为 GIF

## 快速开始

单文件夹计算：

```bash
./run_single_folder_compute.sh \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --data-folder /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/2025-03-25 \
  --output-dir ./outputs/cc_tsi_outputs_single \
  --save-every 30 \
  --get-disp1-compatible \
  --start-index 0 \
  --end-index 1438 \
  -- --cc-enhance tsi --tsi-progress
```

说明：这里的 `--start-index` 是锚点文件索引。开启 `--get-disp1-compatible` 后，实际处理的是它后面的文件。

多文件夹计算：

```bash
./run_multi_folder_compute.sh \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317 \
  --output-root ./outputs/cc_tsi_outputs_by_folder \
  --save-every 30 \
  --get-disp1-compatible \
  --date-start 2025-03-27 \
  --time-start 08:00 \
  --time-end 18:00 \
  -- --cc-enhance tsi --tsi-progress
```

多文件夹批处理同样遵循锚点语义：`start-index` 表示当前文件，真正参与计算的是后续文件。

多文件夹叠加：

```bash
./run_multi_folder_stack.sh \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_by_folder \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend cpu \
  --cc-device cpu \
  --jobs 8
```

多文件夹提频散：

```bash
./run_multi_folder_dispersion.sh \
  --input-root ./outputs/cc_tsi_plot_outputs_by_folder \
  --output-root ./outputs/dispersion_outputs_by_folder \
  --config ./config/config_ChangBai/disp_config.jsonc \
  --workers 8
```
