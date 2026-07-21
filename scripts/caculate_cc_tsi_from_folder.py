#!/usr/bin/env python3
"""
Run the CC + TSI folder pipeline.

Purpose:
    1) Read DAS files from a folder.
    2) Compute one pairwise CC cube per file for the selected [xmin, xmax] segment.
    3) Stack cubes by file groups.
    4) Export grouped all-shot gathers in one of these modes:
       - raw
       - tsi + strict_sparse_all_shot
       - tsi + single_shot
       - tsi + single_shot_local_cc
       - tsi + single_shot_final_once

Implementation:
    This file is intentionally a stable CLI entrypoint. The reusable
    implementation lives in:
        dasQt.features.dispersion.backend.components.ccf.folder_pipeline

Typical run:
    uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py \
        --config examples/03_ccf/config/cc_config.jsonc

Time-window run:
    uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py \
        --config examples/03_ccf/config/cc_config.jsonc \
        --time-start 08:00 --time-end 18:00

GPU/local-shot TSI run:
    uv run python examples/03_ccf/scripts/caculate_cc_tsi_from_folder.py \
        --config examples/03_ccf/config/cc_config.jsonc \
        --cc-enhance tsi \
        --tsi-mode single_shot_local_cc \
        --cc-backend torch \
        --cc-device cuda \
        --cc-batch-chunks 4 \
        --cc-shot-batch-size 2 \
        --tsi-shot-batch-size 16

Core parameters:
    --config PATH
        JSONC config file. Supports both flat cc_config.jsonc style and legacy
        nested schema with CC / disp / TSI sections.
    --data-folder PATH
        Folder containing DAS files. If omitted, uses cc_path from config.
    --file-list PATH
        Optional manifest with one file path per line. Relative paths are
        resolved against --data-folder.
    --time-start HH:MM, --time-end HH:MM
        Keep only files whose filename timestamp falls in the half-open daily
        window [time-start, time-end). Both arguments must be provided together.
    --filename-time-format FORMAT
        Python datetime.strptime format used to find the timestamp anywhere in
        each filename, for example %%Y%%m%%d_%%H%%M%%S.
    --output-dir PATH
        Folder for grouped .mat outputs and diagnostics.
    --save-every N
        Save one grouped output for every N valid files.
    --start-index N, --end-index N
        Select a 0-based inclusive file-index range after sorting/filtering.
    --start-after-index
        Treat --start-index as the anchor file and start from the next file.
    --workers N
        Number of worker processes. GPU modes force workers=1 for stability.
    --refresh-file-index
        Rebuild the sorted file-index cache for --data-folder.
    --profile-stages
        Print per-file local-CC timing information.

CC runtime parameters:
    --cc-enhance {raw,tsi}
        Override cc_enhance_method from config.
    --cc-backend {auto,cpu,torch}
        Numerical backend for CC and stacking.
    --cc-device {auto,cpu,mps,cuda,all_mps,all_cuda}
        Preferred compute device. all_mps/all_cuda push more local-CC work to
        the selected device when supported.
    --cc-batch-chunks N
        Number of time chunks processed per CC backend batch.
    --cc-shot-batch-size N
        Number of local shots correlated together in single_shot_local_cc mode.
    --cc-dtype {float32,float64}
        CC compute dtype.
    --cc-auto-batch / --no-cc-auto-batch
        Enable or disable adaptive CUDA local-CC batch sizing. When enabled,
        CUDA out-of-memory errors halve the local segment batch and retry.
    --cc-max-vram-fraction VALUE
        Target maximum CUDA VRAM fraction used by adaptive GPU work.
    --cc-prewhiten-segment-once / --no-cc-prewhiten-segment-once
        Enable or disable the fast local-CC path that preprocesses and whitens
        each selected segment time chunk once before slicing shot windows.

TSI parameters:
    --tsi-mode {strict_sparse_all_shot,single_shot,single_shot_local_cc,single_shot_final_once}
        TSI strategy when --cc-enhance tsi is active.
    --tsi-iterations N
        Maximum TSI iterations.
    --tsi-stop-rel-l1 VALUE
        Relative L1 convergence threshold for early stopping.
    --tsi-local-expand-offsets N
        Extra offset-lengths used as local TSI context in single_shot_local_cc.
        Final saved gathers still keep the base offset width.
    --tsi-shot-batch-size N
        Number of local shot windows processed per batched TSI call.
    --tsi-progress / --no-tsi-progress
        Enable or disable TSI progress bars.

File indexing:
    - Supported suffixes are .pkl, .pickle, .tdms, .h5, .hdf5, .segy, .sgy, .dat.
    - The script builds a sorted file index for --data-folder.
    - The cache is stored under --output-dir/.file_index_cache/.
    - Use --refresh-file-index when source files changed but folder mtime did
      not update reliably, for example on some NAS mounts.

Output:
    - Grouped .mat files under --output-dir.
    - For TSI modes, matching *_diag.json diagnostics files.
    - Terminal summary with selected file count, runtime backend/device, saved
      group count, and skipped file count.
"""

from __future__ import annotations

from dasQt.features.dispersion.backend.components.ccf.folder_pipeline import (
    run_cc_tsi_folder_pipeline,
)


def main() -> None:
    run_cc_tsi_folder_pipeline()


if __name__ == "__main__":
    main()
