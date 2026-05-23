```bash
source /csim2/zhangzhiyu/MyProjects/DAS_Procee_Show/.venv/bin/activate
```

## 单文件夹计算

```bash
uv run python caculate_cc_tsi_from_folder.py \
  --config ./config_ChangBai/cc_config.jsonc \
  --cc-enhance tsi \
  --tsi-progress \
  --data-folder /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/2025-03-25 \
  --output-dir ./cc_tsi_outputs_ChangBai \
  --save-every 30 \
  --start-index 0 \
  --end-index 1438

uv run python caculate_cc_tsi_from_folder.py \
  --config ./config_ChangBai/cc_config.jsonc \
  --cc-enhance tsi \
  --tsi-progress \
  --data-folder /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/2025-03-25 \
  --file-list ./changbai_2025-03-25_files.txt \
  --output-dir ./cc_tsi_outputs_ChangBai \
  --save-every 30 \
  --start-index 0 \
  --end-index 1438
```

## 单文件夹叠加

```bash
uv run python stack_saved_cc_mat.py \
  --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-25 \
  --glob "cc_stack_tsi_*.mat" \
  --output-dir ./outputs/cc_tsi_plot_outputs_ChangBai \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png
```


## 多文件夹计算

### CPU 计算（不推荐）

```bash
uv run python batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
  --output-root ./cc_tsi_outputs_by_folder \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --save-every 30 \
  --date-start 2025-03-25 \
  --time-start 08:00 \
  --time-end 18:00 \
  --workers 64 \
  --tsi-progress
```


### GPU 计算

```bash
uv run python batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
  --output-root ./outputs/cc_tsi \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --save-every 30 \
  --date-start 2025-07-07 \
  --time-start 08:00 \
  --time-end 18:00 \
  --tsi-progress

uv run python batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
  --output-root ./outputs/cc_tsi_outputs_by_folder \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --save-every 30 \
  --date-start 2025-03-27 \
  --time-start 08:00 \
  --time-end 18:00 \
  --tsi-progress \
  --profile-stages      # 是否显示详细的运行说明，一般不用
```

推荐使用 `config/config_ChangBai/cc_config.jsonc` 中的 GPU 默认值:

- `cc_device=all_cuda`
- `tsi_mode=single_shot_local_cc`
- `cc_prewhiten_segment_once=true`
- `cc_auto_batch=true`

如果要临时关闭整段预白化快速路径，命令行加 `--no-cc-prewhiten-segment-once`。
如果要固定 batch、不自动按显存降档，命令行加 `--no-cc-auto-batch`。


## 多文件夹叠加

```bash
uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi \
  --output-root ./outputs/stacks \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend auto \
  --cc-device auto \
  --jobs 32 \
  --no-save-summary-plot \
  --no-save-shot-figures

uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --no-save-summary-plot \
  --no-save-shot-figures
```


uv run python tools/build_cc_h5_cube.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
  --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv




## 单文件夹提频散
```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
  --output-root ./outputs/dispersion_outputs_ChangBai_single \
  --config ./config/config_ChangBai/dispersion.jsonc
```

## 多文件夹提频散
```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/stacks \
  --output-root ./outputs/dispersion \
  --config ./config/config_ChangBai/dispersion.jsonc \
  --workers 32
```

如果只想先做部分炮：

```bash
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/dispersion.jsonc \
  --shot-stop 20 \
  --save-npz
```

### 绘制gif

```bash
uv run python tools/create_shot_dispersion_gif.py \
  --input-root ./outputs/dispersion_outputs_ChangBai \
  --shot-index 179 \
  --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif
```
