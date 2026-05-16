# 03_ccf

本目录用于 DAS 互相关 / TSI 结果的单目录计算、批量计算、二次叠加与可视化。

## 目录作用

- [caculate_cc_tsi_from_folder.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/caculate_cc_tsi_from_folder.py): 对单个数据目录计算 CC / TSI，并周期性保存 `cc_stack*.mat`。
- [batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py): 扫描一个根数据目录下的所有子目录，逐个调用单目录脚本。
- [stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/stack_saved_cc_mat.py): 对单个输出目录中的多个 `cc_stack*.mat` 做二次叠加、出图、保存 `restack*.mat`。
- [batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_stack_saved_cc_mat.py): 批量扫描 `cc_tsi_outputs_by_folder` 这类目录，对每个子目录执行二次叠加。
- [config](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/config): 各数据源的配置目录。
- [outputs](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/outputs): 示例输出根目录。
- [run.sh](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/run.sh): 常用命令示例。

## 推荐工作流

1. 先用 [batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py) 生成每个日期/子目录的 `cc_stack_tsi_*.mat`。
2. 再用 [batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/batch_stack_saved_cc_mat.py) 批量对这些目录做二次叠加。

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

再批量叠加:

```bash
uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png
```

输出会按相同相对路径写入:

- 输入: `./outputs/cc_tsi_outputs_by_folder/2025-03-27`
- 输出: `./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27`

## Python 环境

按项目约定，运行 Python 时优先使用项目自己的 `.venv`，并通过 `uv run python ...` 执行。
