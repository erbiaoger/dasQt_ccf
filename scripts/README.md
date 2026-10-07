# scripts

本目录存放 `03_ccf` 的 Python CLI 实现。代码调用当前仓库的 `dasqt.features.dispersion.backend.components` 功能模块。

## 文件说明

- `caculate_cc_tsi_from_folder.py`：单个 DAS 文件夹的 CC / TSI 计算入口，实际实现位于 `dasqt.features.dispersion.backend.components.ccf.folder_pipeline`。
- `batch_caculate_cc_tsi_from_subfolders.py`：扫描多个子文件夹并调用单文件夹计算。
- `stack_saved_cc_mat.py`：读取一个目录中的 CC MAT 文件，二次叠加并输出图像。
- `batch_stack_saved_cc_mat.py`：对多个结果目录批量二次叠加。
- `backfill_shot_position_meta.py`：为旧 MAT 结果补齐 shot 空间元数据。
- `plot_cc_m3_shots.py`：绘制 shot 级 CC 与 m3 联图。
- `convert_cc_mat_to_timeslice_h5.py`：将每个 MAT 转换为一个 HDF5 CCF 时间片并生成 `manifest.json`，供长时 CCF GUI 使用。
- `seasonal_diagnosis.py`、`plot_seasonal_diagnosis_detailed.py`、`seasonal_diagnosis_report.py`：全天多位置原始数据和 CC 频散诊断、绘图及报告；使用 `../run_seasonal_diagnosis.sh`。

## 运行方式

推荐使用上级目录的 shell 包装脚本。直接调用 Python 时，从仓库根目录使用项目 `.venv` 和 `uv`：

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt
uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py --help
uv run python examples/03_ccf/scripts/batch_caculate_cc_tsi_from_subfolders.py --help
uv run python examples/03_ccf/scripts/stack_saved_cc_mat.py --help
uv run python examples/03_ccf/scripts/batch_stack_saved_cc_mat.py --help
uv run python examples/03_ccf/scripts/convert_cc_mat_to_timeslice_h5.py --help
```

脚本开头的文档字符串包含用途、输入输出和典型用例；运行过程会将进度和结果写入终端。

常规 CC 绘图入口使用项目统一的 Times New Roman 字体、出版级刻度、180 mm
双栏图幅和 600 dpi 输出；季节诊断单独生成 PNG/PDF。图像色图支持 `reference`、`turbo`、`RdYlBu_r`、
`Spectral_r`、`coolwarm`、`mycmap`；频散 shell 入口可用 `--cc-cmap` 与
`--dispersion-cmap` 直接切换。

对应 shell 入口：

- `../run_single_folder_compute.sh` → `caculate_cc_tsi_from_folder.py`
- `../run_multi_folder_compute.sh` → `batch_caculate_cc_tsi_from_subfolders.py`
- `../run_single_folder_stack.sh` → `stack_saved_cc_mat.py`
- `../run_multi_folder_stack.sh` → `batch_stack_saved_cc_mat.py`
