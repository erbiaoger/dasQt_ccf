# notebooks

本目录存放与 `03_ccf` 相关的分析型 Jupyter Notebook，只负责读取已经准备好的中间结果并可视化，不负责重型格式转换。

## 文件说明

- [build-disk-backed-changbai-cc-cube.ipynb](/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/notebooks/build-disk-backed-changbai-cc-cube.ipynb): 读取 `cc_cube_by_date.h5` 和可选的 `cc_cube_index.csv`，重点演示固定单炮、跨日期读取 `[:, ishot, :, :]` 以及快速统计和绘图。

## 使用方式

1. 先运行 CLI 生成 HDF5:

```bash
uv run python tools/build_cc_h5_cube.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
  --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv
```

2. 在项目环境中启动 Jupyter:

```bash
uv run python -m jupyter lab
```

3. 打开 notebook 并按顺序运行。

## 输出结果

输入:

- `cc_cube_by_date.h5`
- 可选 `cc_cube_index.csv`

输出:

- Notebook 图像和统计表，不会修改源 `.mat` 文件
