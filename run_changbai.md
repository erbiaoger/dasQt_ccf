# ChangBai 工作流示例

下面这套命令以 ChangBai 数据为例，统一使用根目录 shell 包装脚本。

## 1. 单文件夹计算

```bash
./run_single_folder_compute.sh \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --data-folder /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/2025-03-25 \
  --output-dir ./outputs/cc_tsi_outputs_ChangBai \
  --save-every 30 \
  --start-index 0 \
  --end-index 1438 \
  -- --cc-enhance tsi --tsi-progress
```

## 2. 多文件夹计算

```bash
./run_multi_folder_compute.sh \
  --config ./config/config_ChangBai/cc_config.jsonc \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317 \
  --output-root ./outputs/cc_tsi_outputs_by_folder \
  --save-every 30 \
  --date-start 2025-03-27 \
  --time-start 08:00 \
  --time-end 18:00 \
  -- --cc-enhance tsi --tsi-progress
```

推荐 GPU 相关配置放在 `config/config_ChangBai/cc_config.jsonc` 内维护。

## 3. 多文件夹叠加

```bash
./run_multi_folder_stack.sh \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --cc-backend auto \
  --cc-device auto \
  --jobs 32 \
  -- --no-save-summary-plot --no-save-shot-figures
```

## 4. 多文件夹提频散

```bash
./run_multi_folder_dispersion.sh \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/disp_config.jsonc \
  --workers 32
```

## 5. 绘制 GIF

```bash
./run_dispersion_gif.sh \
  --input-root ./outputs/dispersion_outputs_ChangBai \
  --shot-index 179 \
  --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif
```
