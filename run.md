# 03_ccf 运行示例

推荐优先使用根目录的 shell 脚本；如果要直接调 Python，请使用 `./scripts/*.py`。

## 单文件夹计算

```bash
./run_single_folder_compute.sh \
  --config ./config/cc_config.jsonc \
  --data-folder /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/2025-03-25 \
  --output-dir ./outputs/cc_tsi_outputs_single \
  --save-every 30 \
  --get-disp1-compatible \
  --start-index 0 \
  --end-index 1438 \
  -- --cc-enhance tsi --tsi-progress
```

`--start-index` 表示锚点文件索引；开启 `--get-disp1-compatible` 后，实际处理从下一个文件开始。

## 多文件夹计算

```bash
./run_multi_folder_compute.sh \
  --config ./config/cc_config.jsonc \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317 \
  --output-root ./outputs/cc_tsi_outputs_by_folder \
  --save-every 30 \
  --get-disp1-compatible \
  --date-start 2025-03-27 \
  --time-start 08:00 \
  --time-end 18:00 \
  -- --cc-enhance tsi --tsi-progress --profile-stages
```

多文件夹批处理也按锚点语义选文件，避免和 Get Disp1 的窗口错位。

## 单文件夹叠加

```bash
./run_single_folder_stack.sh \
  --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-25 \
  --output-dir ./outputs/cc_tsi_plot_outputs_single/2025-03-25 \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend cpu \
  --cc-device cpu
```

## 多文件夹叠加

```bash
./run_multi_folder_stack.sh \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend cpu \
  --cc-device cpu \
  --jobs 32 \
  -- --no-save-summary-plot --no-save-shot-figures
```

## 单文件夹提频散

```bash
./run_single_folder_dispersion.sh \
  --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
  --output-root ./outputs/dispersion_outputs_single \
  --config ./config/disp_config.jsonc
```

## 多文件夹提频散

```bash
./run_multi_folder_dispersion.sh \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/disp_config.jsonc \
  --workers 32
```

## 绘制 GIF

```bash
./run_dispersion_gif.sh \
  --input-root ./outputs/dispersion_outputs_ChangBai \
  --shot-index 179 \
  --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif
```
