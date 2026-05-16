# tools

本目录放 `03_ccf` 的辅助 CLI 工具，主要负责离线整理、格式转换和数据索引构建。

## 文件说明

- [build_cc_h5_cube.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools/build_cc_h5_cube.py): 扫描 `cc_tsi_plot_outputs_ChangBai` 一类目录，把每个日期目录中的一个 `.mat` 汇总成 HDF5 4D 立方体，并输出索引 CSV。
- [extract_dispersion_from_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/tools/extract_dispersion_from_cc_mat.py): 扫描 `.mat` 目录树，逐个 `.mat` 对所有炮提取频散，并保存横向双图 PNG（左 CC，右频散），可选保存频散数据立方 `npz`。

## 使用方式

```bash
uv run python tools/build_cc_h5_cube.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
  --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv
```

如果后续主要读取 `[:, ishot, :, :]`，保持默认 `--chunk-mode single_shot_across_dates` 即可。

批量提取所有炮的 CC+频散对照图:

```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/dispersion.jsonc
```

只处理单个文件夹:

```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
  --output-root ./outputs/dispersion_outputs_ChangBai_single \
  --config ./config/config_ChangBai/dispersion.jsonc
```

## 输出结果

- `cc_cube_by_date.h5`: HDF5 磁盘后端 4D 数据立方体
- `cc_cube_index.csv`: 每个 date 样本对应的源目录、源文件、shape 和候选文件列表
- `dispersion_outputs_ChangBai/.../shot_XXXX_dispersion.png`: 每炮横向双图，左 CC，右频散
- `dispersion_outputs_ChangBai/dispersion_index.csv`: 全部处理文件索引
