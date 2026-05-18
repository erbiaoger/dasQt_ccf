# 计算单文件夹文件
uv run python caculate_cc_tsi_from_folder.py \
  --data-folder /mnt/expansion/Lhgz_Test/202511.8_StrainRate_SR3.2kHzDS320Hz_GL4m_SSI4m_RF50_LP80Hz_0_5800 \
  --output-dir ./cc_tsi_outputs \
  --config ./config/cc_config.jsonc \
  --tsi-progress \
  --save-every 2 \
  --start-index 10 \
  --end-index 20

# 计算光谷互连的多文件夹的数据
uv batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07 \
  --output-root ./cc_tsi_outputs_by_folder \
  --config /Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/config/cc_config.jsonc \
  --tsi-progress \
  --save-every 10 \
  --start-index 0 \
  --end-index 1440

# 计算叠加数据
uv run python stack_saved_cc_mat.py \
  --input-dir /Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/cc_tsi_outputs_by_folder/2025-01-08 \
  --glob "cc_stack_tsi_*.mat" \
  --output-dir ./cc_tsi_plot_outputs \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.pdf



uv run python caculate_cc_tsi_from_folder.py \
  --data-folder /Volumes/Download/usbshare2/Lhgz_Test/20250930 \
  --output-dir ./cc_tsi_outputs \
  --config ./config_usbshare2/cc_config.jsonc \
  --tsi-progress \
  --save-every 6 \
  --start-index 10 \
  --end-index 144






## usbshare3
uv run python caculate_cc_tsi_from_folder.py \
  --config ./config_usbshare3/cc_config.jsonc \
  --cc-enhance tsi \
  --tsi-shot-batch-size 8 \
  --tsi-progress \
  --data-folder /mnt/nas_3/usbshare3/iDAS-3-Linzhi-20241208 \
  --output-dir ./cc_tsi_outputs_usb3 \
  --save-every 10 \
  --start-index 30 \
  --end-index 2300

uv run python stack_saved_cc_mat.py \
  --input-dir cc_tsi_outputs_usb3 \
  --glob "cc_stack_*.mat" \
  --output-dir ./cc_tsi_plot_outputs_usb3 \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png


## usbshare4
uv run python caculate_cc_tsi_from_folder.py \
  --config ./config_usbshare4/cc_config.jsonc \
  --cc-enhance tsi \
  --tsi-progress \
  --data-folder /mnt/nas_3/usbshare4/small_das_ZH_20250618_0810 \
  --output-dir ./cc_tsi_outputs_usb4 \
  --save-every 30 \
  --start-index 30 \
  --end-index 1000

uv run python stack_saved_cc_mat.py \
  --input-dir cc_tsi_outputs_usb4 \
  --glob "cc_stack_tsi_*.mat" \
  --output-dir ./cc_tsi_plot_outputs_usb4 \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png




## ChangBai
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

uv run python stack_saved_cc_mat.py \
  --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-25 \
  --glob "cc_stack_tsi_*.mat" \
  --output-dir ./outputs/cc_tsi_plot_outputs_ChangBai \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png

### 多文件夹
uv run python batch_caculate_cc_tsi_from_subfolders.py \
  --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
  --output-root ./cc_tsi_outputs_by_folder \
  --config ./config_ChangBai/cc_config.jsonc \
  --save-every 30 \
  --date-start 2025-03-25 \
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
  --profile-stages



### 多文件夹叠加

uv run python batch_stack_saved_cc_mat.py \
  --input-root ./outputs/cc_tsi_outputs_by_folder \
  --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --glob "cc_stack_tsi_*.mat" \
  --stack-size None \
  --interval None \
  --stack-method robust \
  --plot-name local_cc_preview.png


uv run python tools/build_cc_h5_cube.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
  --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv



### 提频散

#### 但文件夹的
uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
  --output-root ./outputs/dispersion_outputs_ChangBai_single \
  --config ./config/config_ChangBai/dispersion.jsonc

直接用这个命令：

uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/dispersion.jsonc \
  --workers 8

如果只想先做部分炮：

uv run python tools/extract_dispersion_from_cc_mat.py \
  --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
  --output-root ./outputs/dispersion_outputs_ChangBai \
  --config ./config/config_ChangBai/dispersion.jsonc \
  --shot-stop 20 \
  --save-npz

source /csim2/zhangzhiyu/MyProjects/DAS_Procee_Show/.venv/bin/activate
