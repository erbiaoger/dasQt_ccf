# scripts

本目录存放 `03_ccf` 的 Python CLI 实现。根目录只保留 shell 入口脚本；如果你想直接调 Python，这里才是实际入口。

## 文件说明

- [caculate_cc_tsi_from_folder.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/caculate_cc_tsi_from_folder.py): 对单个 DAS 文件夹做 CC / TSI 计算
- [batch_caculate_cc_tsi_from_subfolders.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/batch_caculate_cc_tsi_from_subfolders.py): 扫描根目录下多个子目录并批量调用单文件夹计算
- [stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/stack_saved_cc_mat.py): 对一个输出目录中的 `cc_stack*.mat` 做二次叠加与绘图
- [batch_stack_saved_cc_mat.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/batch_stack_saved_cc_mat.py): 对多个结果目录批量二次叠加
- [backfill_shot_position_meta.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/backfill_shot_position_meta.py): 给旧结果补齐 shot 位置元数据
- [plot_cc_m3_shots.py](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/plot_cc_m3_shots.py): 绘制每个 shot 的 CC 与 m3 联合图

## 运行约定

- 推荐从根目录使用 shell 脚本运行
- 如果直接运行 Python，请仍然使用项目 `.venv` 和 `uv`
- 示例：

```bash
source /Users/zhangzhiyu/MyProjects/dasQt/.venv/bin/activate
uv run python /Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/scripts/caculate_cc_tsi_from_folder.py --help
```

## 对应关系

- `../run_single_folder_compute.sh` -> `caculate_cc_tsi_from_folder.py`
- `../run_multi_folder_compute.sh` -> `batch_caculate_cc_tsi_from_subfolders.py`
- `../run_single_folder_stack.sh` -> `stack_saved_cc_mat.py`
- `../run_multi_folder_stack.sh` -> `batch_stack_saved_cc_mat.py`

频散提取和 GIF 生成功能在上级目录的 [tools](/Users/zhangzhiyu/MyProjects/dasQt-example/03_ccf/tools) 中。
