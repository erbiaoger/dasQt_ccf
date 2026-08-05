# tools

本目录存放 CCF / TSI 工作流的后处理工具。

## 文件说明

- `build_cc_h5_cube.py`：把多个日期或批次的 CC MAT 结果汇总为 HDF5 立方体，并输出索引 CSV。
- `extract_dispersion_from_cc_mat.py`：逐炮调用当前 `dasqt.features.dispersion.backend.components.utils.dispersion_utils.compute_dispersion_image()`，保存 CC+频散双图和可选 NPZ 立方体。
- `create_shot_dispersion_gif.py`：按目录内全部图片或固定 shot 编号合成 GIF。

## 运行方式

从仓库根目录执行：

```bash
cd /Users/zhangzhiyu/MyProjects/dasQt
uv run python examples/03_ccf/tools/build_cc_h5_cube.py --help
uv run python examples/03_ccf/tools/extract_dispersion_from_cc_mat.py --help
uv run --with pillow python examples/03_ccf/tools/create_shot_dispersion_gif.py --help
```

也可以从 `examples/03_ccf` 目录使用 `../run_single_folder_dispersion.sh` 和 `../run_dispersion_gif.sh`。

绘图输出统一使用 `Times New Roman` 字体。每个脚本开头的文档字符串包含输入、输出和参数说明。
