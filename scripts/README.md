# scripts

本目录存放 `03_ccf` 的 Python CLI 实现。代码调用当前仓库的 `dasqt.features.dispersion.backend.components` 功能模块。

## 文件说明

- `caculate_cc_tsi_from_folder.py`：单个 DAS 文件夹的 CC / TSI 计算入口，实际实现位于 `dasqt.features.dispersion.backend.components.ccf.folder_pipeline`。
- `batch_caculate_cc_tsi_from_subfolders.py`：扫描多个子文件夹并调用单文件夹计算。
- `stack_saved_cc_mat.py`：读取一个目录中的 CC MAT 文件，二次叠加并输出图像。
- `batch_stack_saved_cc_mat.py`：对多个结果目录批量二次叠加。
- `backfill_shot_position_meta.py`：为旧 MAT 结果补齐 shot 空间元数据。
- `plot_cc_m3_shots.py`：绘制 shot 级 CC 与 m3 联图。

## 运行方式

推荐使用上级目录的 shell 包装脚本。直接调用 Python 时，从仓库根目录使用项目 `.venv` 和 `uv`：

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt
uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py --help
uv run python examples/03_ccf/scripts/batch_caculate_cc_tsi_from_subfolders.py --help
uv run python examples/03_ccf/scripts/stack_saved_cc_mat.py --help
uv run python examples/03_ccf/scripts/batch_stack_saved_cc_mat.py --help
```

脚本开头的文档字符串包含用途、输入输出和典型用例；运行过程会将进度和结果写入终端。

对应 shell 入口：

- `../run_single_folder_compute.sh` → `caculate_cc_tsi_from_folder.py`
- `../run_multi_folder_compute.sh` → `batch_caculate_cc_tsi_from_subfolders.py`
- `../run_single_folder_stack.sh` → `stack_saved_cc_mat.py`
- `../run_multi_folder_stack.sh` → `batch_stack_saved_cc_mat.py`
