#!/usr/bin/env python3
"""
CC + TSI folder pipeline based on processor classes.

Purpose:
1) Read DAS files from a folder.
2) Compute one pairwise CC cube per file for the selected [xmin, xmax] segment.
3) Stack cubes by file groups.
4) Export grouped all-shot gathers in one of three modes:
   - raw
   - tsi + strict_sparse_all_shot
   - tsi + single_shot
   - tsi + single_shot_local_cc
   - tsi + single_shot_final_once

Config/CLI behavior:
- The script is config-driven and supports both `config/cc_config.jsonc` (flat schema)
  and legacy nested schema (`CC` + `disp` + optional `TSI`).
- GPU acceleration is controlled by config fields:
  `cc_backend`, `cc_device`, `cc_batch_chunks`, `cc_shot_batch_size`, `cc_dtype`.
- In `tsi_mode=single_shot`, `tsi_shot_batch_size` controls how many local shot windows
  are sent to one batched TSI call (torch/cuda path) to improve GPU occupancy.
- In `tsi_mode=single_shot_local_cc`, `cc_shot_batch_size` controls how many local shots
  are correlated together in one batched GPU CC call.
- In `tsi_mode=single_shot_local_cc`, `tsi_local_expand_offsets` controls how many
  extra offset-lengths are added to the local CC window (receiver-side) for TSI context.
  Example: base offset=50 m and `tsi_local_expand_offsets=1` -> local TSI context uses 100 m.
  Final saved gather still keeps the base offset window width.
- `tsi_show_progress` controls single_shot batch progress bars in terminal.
- CLI flags can override these runtime fields when needed.

Typical run:
`uv run python examples/03_ccf/caculate_cc_tsi_from_folder.py --config examples/03_ccf/config/cc_config.jsonc`

Time-window run:
`uv run python examples/03_ccf/caculate_cc_tsi_from_folder.py --config examples/03_ccf/config/cc_config.jsonc --time-start 08:00 --time-end 18:00`

File indexing:
- The script builds a sorted file index for `--data-folder`.
- A cache is stored under `--output-dir/.file_index_cache/` and reused when the
  source folder mtime is unchanged. This avoids repeated slow NAS directory scans.
- Use `--refresh-file-index` to force a rebuild when needed.
- To bypass NAS directory scans entirely, provide `--file-list` with one file path
  per line. Relative paths are resolved against `--data-folder`.
- To keep only a daily hour window, provide `--time-start HH:MM --time-end HH:MM`.
  Current ChangBai files are parsed from `YYYY-MM-DD-HH-MM-SS-out.dat`.
- To print per-file local CC stage profiling, add `--profile-stages`.

Output:
- Grouped `.mat` files under `--output-dir`.
- For TSI modes, extra `*_diag.json` diagnostics files.
"""


from __future__ import annotations

import argparse
from contextlib import nullcontext
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, time as dt_time
from functools import partial
import hashlib
import inspect
import json
import os
import sys
from time import perf_counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import savemat
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dasQt.core.dispersion_components.processors import (
    AllShotTSIProcessor,
    CCProcessor,
    TSIProcessor,
)
from dasQt.core.dispersion_components.backends.cc_backend import stack_chunks as backend_stack_chunks
from dasQt.dispersion import DAS_module
from dasQt.utils.math import normalize
from dasQt.utils.config import load_jsonc_file
from dasQt.utils.io import read

try:
    import torch  # type: ignore
except Exception:
    torch = None


SUPPORTED_SUFFIXES = (".pkl", ".pickle", ".tdms", ".h5", ".hdf5", ".segy", ".sgy", ".dat")
_PREPROCESS_HAS_RETURN_NUMPY: bool | None = None
VALID_CC_ENHANCE = {"raw", "tsi"}
VALID_CC_BACKENDS = {"auto", "cpu", "torch"}
VALID_CC_DEVICES = {"auto", "cpu", "mps", "cuda", "all_mps", "all_cuda"}
VALID_CC_DTYPES = {"float32", "float64"}
TSI_MODE_STRICT_SPARSE_ALL_SHOT = "strict_sparse_all_shot"
TSI_MODE_SINGLE_SHOT = "single_shot"
TSI_MODE_SINGLE_SHOT_LOCAL_CC = "single_shot_local_cc"
TSI_MODE_SINGLE_SHOT_FINAL_ONCE = "single_shot_final_once"
VALID_TSI_MODES = {
    TSI_MODE_STRICT_SPARSE_ALL_SHOT,
    TSI_MODE_SINGLE_SHOT,
    TSI_MODE_SINGLE_SHOT_LOCAL_CC,
    TSI_MODE_SINGLE_SHOT_FINAL_ONCE,
}
FILENAME_TIME_FORMAT = "%Y-%m-%d-%H-%M-%S"


def load_config_json(config_path: Path) -> dict[str, Any]:
    return load_jsonc_file(str(config_path))


def _preprocess_raw_make_stat_compat(
    tdata: np.ndarray,
    prepro_para: dict[str, Any],
    *,
    return_numpy: bool,
) -> tuple[np.ndarray, Any]:
    """Call preprocess_raw_make_stat with backward compatibility."""
    global _PREPROCESS_HAS_RETURN_NUMPY
    if _PREPROCESS_HAS_RETURN_NUMPY is None:
        try:
            sig = inspect.signature(DAS_module.preprocess_raw_make_stat)
            _PREPROCESS_HAS_RETURN_NUMPY = "return_numpy" in sig.parameters
        except Exception:
            _PREPROCESS_HAS_RETURN_NUMPY = False

    if _PREPROCESS_HAS_RETURN_NUMPY:
        return DAS_module.preprocess_raw_make_stat(
            tdata,
            prepro_para,
            return_numpy=bool(return_numpy),
        )
    return DAS_module.preprocess_raw_make_stat(tdata, prepro_para)


def discover_files(folder: Path) -> list[Path]:
    return discover_files_with_cache(folder)[0]


def _build_file_index_cache_path(folder: Path, cache_dir: Path) -> Path:
    folder_key = hashlib.sha1(str(folder).encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{folder.name or 'data'}_{folder_key}.json"


def _load_cached_file_index(folder: Path, cache_path: Path) -> list[Path] | None:
    if not cache_path.exists():
        return None

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    try:
        folder_stat = folder.stat()
    except OSError:
        return None

    if payload.get("folder") != str(folder):
        return None
    if int(payload.get("folder_mtime_ns", -1)) != int(folder_stat.st_mtime_ns):
        return None
    if tuple(payload.get("suffixes", [])) != SUPPORTED_SUFFIXES:
        return None

    files = payload.get("files", [])
    if not isinstance(files, list):
        return None
    return [folder / str(name) for name in files]


def _write_cached_file_index(folder: Path, cache_path: Path, files: list[Path]) -> None:
    try:
        folder_stat = folder.stat()
    except OSError:
        return

    payload = {
        "folder": str(folder),
        "folder_mtime_ns": int(folder_stat.st_mtime_ns),
        "suffixes": list(SUPPORTED_SUFFIXES),
        "files": [path.name for path in files],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(cache_path)


def discover_files_with_cache(
    folder: Path,
    *,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> tuple[list[Path], str]:
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = _build_file_index_cache_path(folder, cache_dir)
        if not refresh_cache:
            cached_files = _load_cached_file_index(folder, cache_path)
            if cached_files is not None:
                return cached_files, "cache"

    file_names: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if Path(entry.name).suffix.lower() in SUPPORTED_SUFFIXES:
                file_names.append(entry.name)

    file_names.sort()
    files = [folder / name for name in file_names]

    if cache_path is not None:
        _write_cached_file_index(folder, cache_path, files)

    return files, "scan"


def load_files_from_manifest(file_list_path: Path, data_folder: Path) -> list[Path]:
    files: list[Path] = []
    for raw_line in file_list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line).expanduser()
        if not candidate.is_absolute():
            candidate = data_folder / candidate
        if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        files.append(candidate.resolve())
    return files


def parse_hour_minute(value: str, *, arg_name: str) -> dt_time:
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"Invalid {arg_name}: {value!r}. Expected HH:MM in 24-hour format.") from exc
    return parsed.time()


def parse_timestamp_from_filename(path: Path) -> datetime:
    name = path.name
    if len(name) < 19 or name[19] != "-":
        raise ValueError(
            f"Cannot parse timestamp from filename: {path.name!r}. "
            f"Expected leading timestamp format '{FILENAME_TIME_FORMAT}-...'."
        )
    try:
        return datetime.strptime(name[:19], FILENAME_TIME_FORMAT)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse timestamp from filename: {path.name!r}. "
            f"Expected leading timestamp format '{FILENAME_TIME_FORMAT}-...'."
        ) from exc


def filter_files_by_time_window(
    files: list[Path],
    *,
    time_start: dt_time | None,
    time_end: dt_time | None,
) -> list[Path]:
    if time_start is None and time_end is None:
        return list(files)
    if time_start is None or time_end is None:
        raise ValueError("Both --time-start and --time-end must be provided together.")
    if time_start >= time_end:
        raise ValueError("--time-start must be earlier than --time-end. Cross-midnight windows are not supported.")

    filtered: list[Path] = []
    for path in files:
        ts = parse_timestamp_from_filename(path)
        tod = ts.time()
        if time_start <= tod < time_end:
            filtered.append(path)
    return filtered


def _torch_inference_context():
    if torch is None:
        return nullcontext()
    return torch.inference_mode()


def _maybe_clear_cuda_cache(cc_params: dict[str, Any]) -> None:
    if torch is None:
        return
    runtime_device = str(cc_params.get("_runtime_cc_device", cc_params.get("cc_device", "auto"))).strip().lower()
    if runtime_device != "cuda":
        return
    if not torch.cuda.is_available():
        return
    torch.cuda.empty_cache()


def _prepare_local_shot_batch_inputs(
    segment_data: np.ndarray,
    batch_starts: np.ndarray,
    i_offset: int,
    mm: int,
    chunk_npts: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts_arr = np.asarray(batch_starts, dtype=int).ravel()
    if starts_arr.size == 0:
        raise ValueError("batch_starts is empty.")

    local_views = [np.asarray(segment_data[:, start:start + i_offset], dtype=float) for start in starts_arr]
    local_batch = np.ascontiguousarray(np.stack(local_views, axis=0), dtype=float)
    expected_nt = int(mm * chunk_npts)
    if local_batch.shape[1] < expected_nt:
        raise ValueError(
            f"Local batch is too short for chunk layout: nt={local_batch.shape[1]}, expected at least {expected_nt}"
        )
    window_batch = local_batch[:, :expected_nt, :].reshape(int(starts_arr.size), int(mm), int(chunk_npts), int(i_offset))
    return starts_arr, local_batch, window_batch


def _format_profile_seconds(value: float) -> str:
    return f"{float(value):.3f}s"


def normalize_orientation(data: np.ndarray, metadata: dict[str, Any]) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2-D data, got shape {arr.shape}")

    nt = metadata.get("nt")
    nx = metadata.get("nx")
    if isinstance(nt, (int, np.integer)) and isinstance(nx, (int, np.integer)):
        if arr.shape == (int(nt), int(nx)):
            return arr
        if arr.shape == (int(nx), int(nt)):
            return arr.T

    if arr.shape[0] < arr.shape[1]:
        return arr.T
    return arr


def build_meta(data: np.ndarray, metadata: dict[str, Any]) -> dict[str, Any]:
    dt = metadata.get("dt")
    if dt in (None, 0):
        fs = metadata.get("fs")
        if fs in (None, 0):
            raise ValueError("Metadata missing dt/fs.")
        dt = 1.0 / float(fs)
    dt = float(dt)

    dx = metadata.get("dx")
    if dx in (None, 0):
        raise ValueError("Metadata missing dx.")
    dx = float(dx)

    nx = int(data.shape[1])
    start_distance = float(metadata.get("start_distance", 0.0) or 0.0)
    profile_x = start_distance + np.arange(nx, dtype=float) * dx

    return {"dt": dt, "dx": dx, "profileX": profile_x}


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _resolve_downsample_factors(params: dict[str, Any]) -> tuple[int, int]:
    space_factor = max(_int_or_default(params.get("space_downsample", 1), 1), 1)
    time_factor = max(_int_or_default(params.get("time_downsample", 1), 1), 1)
    return space_factor, time_factor


def apply_runtime_downsample(
    data: np.ndarray,
    meta: dict[str, Any],
    params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    runtime_data = np.asarray(data)
    if runtime_data.ndim != 2:
        raise ValueError(f"Expected 2-D data before downsampling, got shape {runtime_data.shape}")

    source_meta = dict(meta)
    space_factor, time_factor = _resolve_downsample_factors(params)
    runtime_data = runtime_data[::time_factor, ::space_factor]

    base_dt = float(source_meta["dt"])
    base_dx = float(source_meta["dx"])
    runtime_dt = base_dt * float(time_factor)
    runtime_dx = base_dx * float(space_factor)

    runtime_meta = dict(source_meta)
    runtime_meta["dt"] = runtime_dt
    runtime_meta["dx"] = runtime_dx
    runtime_meta["fs"] = 1.0 / runtime_dt
    runtime_meta["nt"] = int(runtime_data.shape[0])
    runtime_meta["nx"] = int(runtime_data.shape[1])

    profile_x = np.asarray(source_meta.get("profileX", []), dtype=float).ravel()
    if profile_x.size:
        profile_x = profile_x[::space_factor]
    if profile_x.size != runtime_data.shape[1]:
        start_x = float(profile_x[0]) if profile_x.size else 0.0
        profile_x = start_x + np.arange(runtime_data.shape[1], dtype=float) * runtime_dx
    runtime_meta["profileX"] = profile_x

    return runtime_data, runtime_meta


def build_run_params(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    Map config JSON to processor-friendly parameters.
    Supports:
    1) legacy dispersion.jsonc style: {"CC": {...}, "disp": {...}}
    2) flat cc_config.jsonc style
    """
    cc_cfg = config.get("CC", {})
    if isinstance(cc_cfg, dict) and cc_cfg:
        filter_cfg = cc_cfg.get("filter", {})
        smooth_cfg = cc_cfg.get("smooth", {})
        cc_run_cfg = cc_cfg.get("cc", {})
        disp_cfg = config.get("disp", {})
        ms_cfg = disp_cfg.get("multiple_shot", {}) if isinstance(disp_cfg, dict) else {}
        tsi_cfg = config.get("TSI", {}) if isinstance(config.get("TSI", {}), dict) else {}

        params = {
            "ishot_inf": _int_or_default(cc_run_cfg.get("Shot"), 0),
            "freqmin": _float_or_default(filter_cfg.get("fmin"), 2.0),
            "freqmax": _float_or_default(filter_cfg.get("fmax"), 30.0),
            "freq_norm": str(filter_cfg.get("freq_norm", "rma")),
            "time_norm": str(filter_cfg.get("time_norm", "no")),
            "cc_method": str(filter_cfg.get("cc_method", "coherency")),
            "smooth_N": _int_or_default(smooth_cfg.get("smooth_N"), 5),
            "smoothspect_N": _int_or_default(smooth_cfg.get("smoothspect_N"), 5),
            "maxlag": _float_or_default(smooth_cfg.get("maxlag"), 0.4),
            "max_over_std": _float_or_default(cc_run_cfg.get("max_over_std"), 1e9),
            "cc_len": _float_or_default(cc_run_cfg.get("CC_len"), 1.0),
            "cha1": _float_or_default(cc_run_cfg.get("Xmin"), 0.0),
            "cha2": _float_or_default(cc_run_cfg.get("Xmax"), 0.0),
            "xmin": _float_or_default(cc_run_cfg.get("Xmin"), 0.0),
            "xmax": _float_or_default(cc_run_cfg.get("Xmax"), 0.0),
            "stacking_method": str(cc_run_cfg.get("Smethod", "pws")),
            "stack_method": str(cc_run_cfg.get("Smethod", "pws")),
            "dshot": _float_or_default(ms_cfg.get("dshot"), 1.0),
            "offset": _float_or_default(ms_cfg.get("offset"), 1.0),
            "space_downsample": _int_or_default(cc_run_cfg.get("space_downsample", cc_cfg.get("space_downsample")), 1),
            "time_downsample": _int_or_default(cc_run_cfg.get("time_downsample", cc_cfg.get("time_downsample")), 1),
            "cc_enhance_method": str(tsi_cfg.get("cc_enhance_method", "raw")),
            "cc_backend": str(cc_cfg.get("cc_backend", config.get("cc_backend", "auto"))),
            "cc_device": str(cc_cfg.get("cc_device", config.get("cc_device", "auto"))),
            "cc_batch_chunks": _int_or_default(
                cc_cfg.get("cc_batch_chunks", config.get("cc_batch_chunks")),
                8,
            ),
            "cc_shot_batch_size": _int_or_default(
                cc_cfg.get("cc_shot_batch_size", config.get("cc_shot_batch_size")),
                1,
            ),
            "cc_dtype": str(cc_cfg.get("cc_dtype", config.get("cc_dtype", "float32"))),
            "tsi_mode": str(tsi_cfg.get("tsi_mode", "strict_sparse_all_shot")),
            "tsi_iterations": _int_or_default(tsi_cfg.get("tsi_iterations"), 5),
            "tsi_stop_rel_l1": _float_or_default(tsi_cfg.get("tsi_stop_rel_l1"), 5e-3),
            "tsi_taper_enabled": bool(tsi_cfg.get("tsi_taper_enabled", True)),
            "tsi_taper_vmin": _float_or_default(tsi_cfg.get("tsi_taper_vmin"), 100.0),
            "tsi_taper_vmax": _float_or_default(tsi_cfg.get("tsi_taper_vmax"), 800.0),
            "tsi_taper_outside_scale": _float_or_default(tsi_cfg.get("tsi_taper_outside_scale"), 0.25),
            "tsi_taper_shoulder_fraction": _float_or_default(tsi_cfg.get("tsi_taper_shoulder_fraction"), 0.25),
            "tsi_local_expand_offsets": _int_or_default(
                tsi_cfg.get("tsi_local_expand_offsets", cc_cfg.get("tsi_local_expand_offsets", config.get("tsi_local_expand_offsets"))),
                0,
            ),
            "tsi_shot_batch_size": _int_or_default(
                tsi_cfg.get(
                    "tsi_shot_batch_size",
                    cc_cfg.get("tsi_shot_batch_size", config.get("tsi_shot_batch_size", cc_cfg.get("cc_batch_chunks", 8))),
                ),
                8,
            ),
            "tsi_show_progress": bool(
                tsi_cfg.get(
                    "tsi_show_progress",
                    cc_cfg.get("tsi_show_progress", config.get("tsi_show_progress", True)),
                )
            ),
        }
        data_folder = str(cc_cfg.get("cc_path", "")).strip()
        return params, data_folder

    default_offset = _float_or_default(config.get("xmax"), 0.0) - _float_or_default(config.get("xmin"), 0.0)

    params = {
        "ishot_inf": _int_or_default(config.get("ishot_inf", config.get("xshot")), 0),
        "freqmin": _float_or_default(config.get("freqmin"), 2.0),
        "freqmax": _float_or_default(config.get("freqmax"), 30.0),
        "freq_norm": str(config.get("freq_norm", "rma")),
        "time_norm": str(config.get("time_norm", "no")),
        "cc_method": str(config.get("cc_method", "coherency")),
        "smooth_N": _int_or_default(config.get("smooth_N"), 5),
        "smoothspect_N": _int_or_default(config.get("smoothspect_N"), 5),
        "maxlag": _float_or_default(config.get("maxlag"), 0.4),
        "max_over_std": _float_or_default(config.get("max_over_std"), 1e9),
        "cc_len": _float_or_default(config.get("cc_len"), 1.0),
        "cha1": _float_or_default(config.get("xmin"), 0.0),
        "cha2": _float_or_default(config.get("xmax"), 0.0),
        "xmin": _float_or_default(config.get("xmin"), 0.0),
        "xmax": _float_or_default(config.get("xmax"), 0.0),
        "stacking_method": str(config.get("stacking_method", "pws")),
        "stack_method": str(config.get("stacking_method", "pws")),
        "dshot": _float_or_default(config.get("dshot"), 1.0),
        "offset": _float_or_default(config.get("offset"), default_offset),
        "space_downsample": _int_or_default(config.get("space_downsample"), 1),
        "time_downsample": _int_or_default(config.get("time_downsample"), 1),
        "cc_enhance_method": str(config.get("cc_enhance_method", "raw")),
        "cc_backend": str(config.get("cc_backend", "auto")),
        "cc_device": str(config.get("cc_device", "auto")),
        "cc_batch_chunks": _int_or_default(config.get("cc_batch_chunks"), 8),
        "cc_shot_batch_size": _int_or_default(config.get("cc_shot_batch_size"), 1),
        "cc_dtype": str(config.get("cc_dtype", "float32")),
        "tsi_mode": str(config.get("tsi_mode", "strict_sparse_all_shot")),
        "tsi_iterations": _int_or_default(config.get("tsi_iterations"), 5),
        "tsi_stop_rel_l1": _float_or_default(config.get("tsi_stop_rel_l1"), 5e-3),
        "tsi_taper_enabled": bool(config.get("tsi_taper_enabled", True)),
        "tsi_taper_vmin": _float_or_default(config.get("tsi_taper_vmin"), 100.0),
        "tsi_taper_vmax": _float_or_default(config.get("tsi_taper_vmax"), 800.0),
        "tsi_taper_outside_scale": _float_or_default(config.get("tsi_taper_outside_scale"), 0.25),
        "tsi_taper_shoulder_fraction": _float_or_default(config.get("tsi_taper_shoulder_fraction"), 0.25),
        "tsi_local_expand_offsets": _int_or_default(config.get("tsi_local_expand_offsets"), 0),
        "tsi_shot_batch_size": _int_or_default(config.get("tsi_shot_batch_size", config.get("cc_batch_chunks", 8)), 8),
        "tsi_show_progress": bool(config.get("tsi_show_progress", True)),
    }
    data_folder = str(config.get("cc_path", "")).strip()
    return params, data_folder


def normalize_run_params(params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    out["cc_enhance_method"] = str(out.get("cc_enhance_method", "raw")).strip().lower()
    if out["cc_enhance_method"] not in VALID_CC_ENHANCE:
        raise ValueError(f"Invalid cc_enhance_method: {out['cc_enhance_method']}")

    out["cc_backend"] = str(out.get("cc_backend", "auto")).strip().lower()
    if out["cc_backend"] not in VALID_CC_BACKENDS:
        raise ValueError(f"Invalid cc_backend: {out['cc_backend']}")

    out["cc_device"] = str(out.get("cc_device", "auto")).strip().lower()
    if out["cc_device"] not in VALID_CC_DEVICES:
        raise ValueError(f"Invalid cc_device: {out['cc_device']}")

    out["cc_dtype"] = str(out.get("cc_dtype", "float32")).strip().lower()
    if out["cc_dtype"] not in VALID_CC_DTYPES:
        raise ValueError(f"Invalid cc_dtype: {out['cc_dtype']}")

    out["cc_batch_chunks"] = max(_int_or_default(out.get("cc_batch_chunks"), 8), 1)
    out["cc_shot_batch_size"] = max(_int_or_default(out.get("cc_shot_batch_size"), 1), 1)

    out["tsi_mode"] = str(out.get("tsi_mode", "strict_sparse_all_shot")).strip().lower()
    if out["tsi_mode"] not in VALID_TSI_MODES:
        raise ValueError(f"Invalid tsi_mode: {out['tsi_mode']}")

    out["tsi_iterations"] = max(_int_or_default(out.get("tsi_iterations"), 5), 1)
    out["tsi_stop_rel_l1"] = max(_float_or_default(out.get("tsi_stop_rel_l1"), 5e-3), 1e-12)
    out["tsi_taper_enabled"] = bool(out.get("tsi_taper_enabled", True))
    out["tsi_taper_vmin"] = _float_or_default(out.get("tsi_taper_vmin"), 100.0)
    out["tsi_taper_vmax"] = _float_or_default(out.get("tsi_taper_vmax"), 800.0)
    out["tsi_taper_outside_scale"] = _float_or_default(out.get("tsi_taper_outside_scale"), 0.25)
    out["tsi_taper_shoulder_fraction"] = _float_or_default(out.get("tsi_taper_shoulder_fraction"), 0.25)
    out["tsi_local_expand_offsets"] = max(_int_or_default(out.get("tsi_local_expand_offsets"), 0), 0)
    out["tsi_shot_batch_size"] = max(
        _int_or_default(out.get("tsi_shot_batch_size", out.get("cc_batch_chunks", 8)), 8),
        1,
    )
    out["tsi_show_progress"] = bool(out.get("tsi_show_progress", True))
    out["space_downsample"] = max(_int_or_default(out.get("space_downsample"), 1), 1)
    out["time_downsample"] = max(_int_or_default(out.get("time_downsample"), 1), 1)
    out["stacking_method"] = str(out.get("stacking_method", out.get("stack_method", "pws")))
    out["stack_method"] = out["stacking_method"]
    out["fk_filt"] = {
        "enabled": bool(out.get("fk_enabled", False)),
        "cmin": _float_or_default(out.get("fk_cmin"), 5.0),
        "cmax": _float_or_default(out.get("fk_cmax"), 1000.0),
        "sgn": str(out.get("fk_sgn", "both")),
    }
    return out


def is_single_shot_final_once_mode(params: dict[str, Any]) -> bool:
    return (
        str(params.get("cc_enhance_method", "raw")).strip().lower() == "tsi"
        and str(params.get("tsi_mode", TSI_MODE_STRICT_SPARSE_ALL_SHOT)).strip().lower()
        == TSI_MODE_SINGLE_SHOT_FINAL_ONCE
    )


def apply_cli_overrides(args: argparse.Namespace, params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    if args.cc_enhance is not None:
        out["cc_enhance_method"] = args.cc_enhance
    if args.cc_backend is not None:
        out["cc_backend"] = args.cc_backend
    if args.cc_device is not None:
        out["cc_device"] = args.cc_device
    if args.cc_batch_chunks is not None:
        out["cc_batch_chunks"] = int(args.cc_batch_chunks)
    if args.cc_shot_batch_size is not None:
        out["cc_shot_batch_size"] = int(args.cc_shot_batch_size)
    if args.cc_dtype is not None:
        out["cc_dtype"] = args.cc_dtype
    if args.tsi_mode is not None:
        out["tsi_mode"] = args.tsi_mode
    if args.tsi_iterations is not None:
        out["tsi_iterations"] = int(args.tsi_iterations)
    if args.tsi_stop_rel_l1 is not None:
        out["tsi_stop_rel_l1"] = float(args.tsi_stop_rel_l1)
    if args.tsi_local_expand_offsets is not None:
        out["tsi_local_expand_offsets"] = int(args.tsi_local_expand_offsets)
    if args.tsi_shot_batch_size is not None:
        out["tsi_shot_batch_size"] = int(args.tsi_shot_batch_size)
    if args.tsi_show_progress is not None:
        out["tsi_show_progress"] = bool(args.tsi_show_progress)
    return normalize_run_params(out)


def resolve_cc_device_runtime(device: str) -> tuple[str, bool]:
    cc_device = str(device).strip().lower()
    if cc_device == "all_mps":
        return "mps", True
    if cc_device == "all_cuda":
        return "cuda", True
    return cc_device, False


def with_runtime_cc_device(params: dict[str, Any]) -> dict[str, Any]:
    runtime_device, all_device_mode = resolve_cc_device_runtime(str(params.get("cc_device", "auto")))
    out = dict(params)
    out["_runtime_cc_device"] = runtime_device
    out["_all_device_mode"] = bool(all_device_mode)
    return out


def should_force_single_worker_for_gpu(params: dict[str, Any]) -> bool:
    backend = str(params.get("cc_backend", "auto")).strip().lower()
    device, _ = resolve_cc_device_runtime(str(params.get("cc_device", "auto")))
    if backend == "cpu" or device == "cpu":
        return False
    if device in {"mps", "cuda"}:
        return True
    if backend == "torch":
        return True
    if backend == "auto" and device == "auto":
        try:
            import torch  # type: ignore
        except Exception:
            return False
        mps_available = bool(
            getattr(torch.backends, "mps", None) is not None
            and torch.backends.mps.is_available()
        )
        cuda_available = bool(torch.cuda.is_available())
        return mps_available or cuda_available
    return False


def resolve_segment_indices(profile_x: np.ndarray, params: dict[str, Any]) -> tuple[int, int]:
    x_min = float(params.get("cha1", params.get("xmin", 0.0)))
    x_max = float(params.get("cha2", params.get("xmax", 0.0)))
    cha1 = int(np.abs(profile_x - x_min).argmin())
    cha2 = int(np.abs(profile_x - x_max).argmin())
    if cha2 < cha1:
        cha1, cha2 = cha2, cha1
    if cha2 == cha1:
        cha2 = min(cha2 + 1, profile_x.size)
        cha1 = max(0, cha2 - 2)
    if cha2 <= cha1:
        raise ValueError(f"Invalid segment range: [{cha1}, {cha2})")
    return cha1, cha2


def build_segment_profile_x(meta: dict[str, Any], params: dict[str, Any]) -> np.ndarray:
    profile_x = np.asarray(meta["profileX"], dtype=float)
    cha1, cha2 = resolve_segment_indices(profile_x, params)
    return profile_x[cha1:cha2].copy()


def build_shot_geometry(nch: int, dx: float, params: dict[str, Any]) -> tuple[int, int, int]:
    i_dshot = max(int(round(float(params.get("dshot", dx)) / max(dx, 1e-12))), 1)
    i_offset = max(int(round(float(params.get("offset", dx)) / max(dx, 1e-12))), 2)
    if nch < i_offset:
        raise ValueError(
            f"Shot geometry invalid for segment: nch={nch}, i_offset={i_offset}, dx={dx}, "
            f"dshot={params.get('dshot')}, offset={params.get('offset')}"
        )
    n_shots = (nch - i_offset) // i_dshot + 1
    if n_shots <= 0:
        raise ValueError(
            f"No valid shots generated: nch={nch}, i_dshot={i_dshot}, i_offset={i_offset}"
        )
    return i_dshot, i_offset, int(n_shots)


def _extract_source_local_indices_from_diagnostics(
    diagnostics: dict[str, Any] | None,
    n_shots: int,
) -> np.ndarray:
    out = np.zeros(int(n_shots), dtype=int)
    if not isinstance(diagnostics, dict):
        return out
    shot_diag = diagnostics.get("shot_diagnostics")
    if not isinstance(shot_diag, list):
        return out
    for item in shot_diag:
        if not isinstance(item, dict):
            continue
        shot_index = item.get("shot_index")
        source_local_index = item.get("source_local_index")
        if shot_index is None or source_local_index is None:
            continue
        try:
            i = int(shot_index)
            s = int(source_local_index)
        except Exception:
            continue
        if 0 <= i < out.size:
            out[i] = s
    return out


def build_shot_position_meta(
    segment_profile_x: np.ndarray,
    shot_starts: np.ndarray,
    source_local_indices: np.ndarray | None = None,
) -> dict[str, Any]:
    """
    Build per-shot absolute/relative position metadata in meters.
    """
    profile_x = np.asarray(segment_profile_x, dtype=float).ravel()
    starts = np.asarray(shot_starts, dtype=int).ravel()
    if starts.size == 0:
        raise ValueError("shot_starts is empty.")
    if profile_x.size == 0:
        raise ValueError("segment_profile_x is empty.")
    if np.any(starts < 0) or np.any(starts >= profile_x.size):
        raise ValueError(
            f"shot_starts out of bounds: min={int(starts.min())}, max={int(starts.max())}, profile_size={profile_x.size}"
        )

    if source_local_indices is None:
        source_local = np.zeros(starts.size, dtype=int)
    else:
        source_local = np.asarray(source_local_indices, dtype=int).ravel()
        if source_local.size != starts.size:
            raise ValueError(
                f"source_local_indices size {source_local.size} != shot_starts size {starts.size}"
            )

    source_channels = starts + source_local
    if np.any(source_channels < 0) or np.any(source_channels >= profile_x.size):
        raise ValueError(
            f"source channel out of bounds: min={int(source_channels.min())}, max={int(source_channels.max())}, profile_size={profile_x.size}"
        )

    shot_start_x = profile_x[starts]
    shot_source_x = profile_x[source_channels]
    return {
        "shot_index": np.arange(starts.size, dtype=int),
        "shot_start_channel": starts.astype(int),
        "shot_source_channel": source_channels.astype(int),
        "shot_source_local_index": source_local.astype(int),
        "shot_start_x_m": np.asarray(shot_start_x, dtype=float),
        "shot_source_x_m": np.asarray(shot_source_x, dtype=float),
        "shot_start_x_rel_m": np.asarray(shot_start_x - shot_start_x[0], dtype=float),
        "shot_source_x_rel_m": np.asarray(shot_source_x - shot_source_x[0], dtype=float),
        "shot_position_unit": "m",
    }


def process_file_for_pairwise_cube(file_path_str: str, cc_params: dict[str, Any]) -> tuple[str, np.ndarray, float, float, np.ndarray]:
    file_path = Path(file_path_str)
    data_raw, metadata = read(str(file_path), output_type="array")
    data = normalize_orientation(np.asarray(data_raw), metadata)
    meta = build_meta(data, metadata)
    runtime_data, runtime_meta = apply_runtime_downsample(data, meta, cc_params)
    segment_profile_x = build_segment_profile_x(runtime_meta, cc_params)
    processor = CCProcessor(runtime_meta)
    cube = processor.compute_pairwise_cc_cube(runtime_data, cc_params)
    return file_path.name, cube, float(runtime_meta["dt"]), float(runtime_meta["dx"]), segment_profile_x


def _center_crop_or_pad_last_axis(array: np.ndarray, target_len: int) -> np.ndarray:
    data = np.asarray(array, dtype=float)
    cur_len = int(data.shape[-1])
    target_len = int(target_len)
    if target_len <= 0:
        raise ValueError(f"target_len must be positive, got {target_len}")
    if cur_len == target_len:
        return data.copy()
    if cur_len > target_len:
        start = (cur_len - target_len) // 2
        stop = start + target_len
        return data[..., start:stop].copy()

    out = np.zeros(data.shape[:-1] + (target_len,), dtype=data.dtype)
    start = (target_len - cur_len) // 2
    out[..., start:start + cur_len] = data
    return out


def _align_local_cubes_lag(local_cubes: list[np.ndarray]) -> np.ndarray:
    if not local_cubes:
        raise ValueError("No local shot cubes to align.")
    target_len = min(int(np.asarray(cube).shape[-1]) for cube in local_cubes)
    aligned = [
        _center_crop_or_pad_last_axis(np.asarray(cube, dtype=float), target_len)
        for cube in local_cubes
    ]
    return np.asarray(aligned, dtype=float)


def _to_numpy_backend(array: Any) -> np.ndarray:
    if hasattr(array, "detach") and hasattr(array, "cpu"):
        return array.detach().cpu().numpy()
    return np.asarray(array)


def _stack_cc_with_runtime(
    chunks: Any,
    dt: float,
    smethod: str,
    cc_params: dict[str, Any],
) -> np.ndarray:
    if torch is not None and isinstance(chunks, torch.Tensor):
        if chunks.ndim not in {3, 4}:
            raise ValueError(f"chunks must be 3D or 4D (n, nt, nx) / (nsrc, n, nt, nx), got shape={tuple(chunks.shape)}")
        data = chunks
        if data.ndim == 3:
            n, nt, nx = (int(v) for v in data.shape)
        else:
            _nsrc, n, nt, nx = (int(v) for v in data.shape)
    else:
        data = np.asarray(chunks, dtype=float)
        if data.ndim not in {3, 4}:
            raise ValueError(f"chunks must be 3D or 4D, got shape={data.shape}")
        if data.ndim == 3:
            n, nt, nx = data.shape
        else:
            _nsrc, n, nt, nx = data.shape

    stacked = backend_stack_chunks(
        data,
        sampling_rate=1.0 / float(dt),
        method=str(smethod),
        backend=str(cc_params.get("cc_backend", "auto")),
        device=str(cc_params.get("_runtime_cc_device", cc_params.get("cc_device", "auto"))),
        dtype=str(cc_params.get("cc_dtype", "float32")),
    )
    stacked = np.asarray(stacked, dtype=float)
    if stacked.ndim == 2:
        stacked = normalize(stacked)
        return stacked - np.mean(stacked, axis=0)
    if stacked.ndim == 3:
        max_values = np.max(np.abs(stacked), axis=1, keepdims=True)
        max_values = np.where(max_values == 0, 1.0, max_values)
        stacked = stacked / max_values
        return stacked - np.mean(stacked, axis=1, keepdims=True)
    raise ValueError(f"stacked output must be 2D or 3D, got shape={stacked.shape}")


def _align_cc_chunk_list_lag(chunks: list[np.ndarray]) -> np.ndarray:
    if not chunks:
        raise ValueError("No CC chunks to align.")
    target_len = min(int(np.asarray(chunk).shape[0]) for chunk in chunks)
    aligned = [
        _center_crop_or_pad_last_axis(np.asarray(chunk, dtype=float).T, target_len).T
        for chunk in chunks
    ]
    return np.asarray(aligned, dtype=float)


def process_file_for_local_shot_cubes(
    file_path_str: str,
    cc_params: dict[str, Any],
) -> tuple[str, np.ndarray, float, float, np.ndarray, np.ndarray, int]:
    """
    Build per-shot local pairwise cubes for one file.

    Unlike global pairwise mode, this function only computes i_offset x i_offset
    local cubes for each shot start, which is typically much cheaper when
    i_offset << nch.
    """
    file_path = Path(file_path_str)
    profile_enabled = bool(cc_params.get("profile_stages", False))
    profile: dict[str, float] = {
        "read_data_s": 0.0,
        "prepare_batch_s": 0.0,
        "prefetch_wait_s": 0.0,
        "fast_preprocess_s": 0.0,
        "gpu_cc_s": 0.0,
        "fallback_cpu_cc_s": 0.0,
        "stack_s": 0.0,
        "stack_runtime_s": 0.0,
        "stack_finalize_s": 0.0,
        "align_s": 0.0,
        "total_s": 0.0,
    }
    file_t0 = perf_counter()

    read_t0 = perf_counter()
    data_raw, metadata = read(str(file_path), output_type="array")
    profile["read_data_s"] += perf_counter() - read_t0
    data = normalize_orientation(np.asarray(data_raw), metadata)
    meta = build_meta(data, metadata)
    runtime_data, runtime_meta = apply_runtime_downsample(data, meta, cc_params)
    profile_x = np.asarray(runtime_meta["profileX"], dtype=float)
    cha1, cha2 = resolve_segment_indices(profile_x, cc_params)

    segment_data = np.asarray(runtime_data[:, cha1:cha2], dtype=float)
    segment_profile_x = np.asarray(profile_x[cha1:cha2], dtype=float)
    dt = float(runtime_meta["dt"])
    dx = float(runtime_meta["dx"])

    nch = int(segment_data.shape[1])
    i_dshot, base_i_offset, _n_shots = build_shot_geometry(nch, dx, cc_params)
    expand_offsets = max(int(cc_params.get("tsi_local_expand_offsets", 0)), 0)
    i_offset = int(base_i_offset * (1 + expand_offsets))
    if i_offset < 3:
        raise ValueError(
            f"single_shot_local_cc requires at least 3 channels in the offset window, got {i_offset}."
        )
    if nch < i_offset:
        raise ValueError(
            f"Expanded local offset window is too large: nch={nch}, "
            f"base_i_offset={base_i_offset}, expand={expand_offsets}, expanded_i_offset={i_offset}"
        )
    starts = np.arange(0, nch - i_offset + 1, i_dshot, dtype=int)
    if starts.size == 0:
        raise ValueError(
            f"No local shots generated for nch={nch}, i_offset={i_offset}, i_dshot={i_dshot}"
        )

    sps = float(1.0 / max(dt, 1e-12))
    chunk_npts = int(round(float(cc_params["cc_len"]) * sps))
    if chunk_npts <= 0:
        raise ValueError(f"Invalid cc_len/dt combination: cc_len={cc_params['cc_len']}, dt={dt}")
    mm = int(segment_data.shape[0] // chunk_npts)
    if mm <= 0:
        raise ValueError(
            f"Data length is too short for cc_len: nt={segment_data.shape[0]}, chunk_npts={chunk_npts}, cc_len={cc_params['cc_len']}"
        )

    prepro_para = dict(cc_params)
    runtime_cc_device = str(cc_params.get("_runtime_cc_device", cc_params.get("cc_device", "auto")))
    all_device_mode = bool(cc_params.get("_all_device_mode", False))
    prepro_para["cc_device"] = runtime_cc_device
    prepro_para.update(
        {
            "sps": sps,
            "samp_freq": sps,
            "npts_chunk": int(chunk_npts),
            "nsta": int(i_offset),
            "cha_list": np.arange(i_offset, dtype=int),
            "dx": float(dx),
        }
    )
    expected_nchan = int(i_offset)
    full_indices = np.arange(expected_nchan, dtype=int)
    stack_method = str(cc_params.get("stacking_method", "pws"))
    max_over_std = float(prepro_para.get("max_over_std", 1e9))
    cc_batch_chunks = max(int(cc_params.get("cc_batch_chunks", 8)), 1)
    shot_batch_size = max(int(cc_params.get("cc_shot_batch_size", 1)), 1)
    segment_batch_size = max(cc_batch_chunks * shot_batch_size, 1)
    torch_dtype = None
    if torch is not None:
        torch_dtype = torch.float64 if str(cc_params.get("cc_dtype", "float32")) == "float64" else torch.float32

    processor = CCProcessor({"dt": dt, "dx": dx})
    local_shot_cubes: list[np.ndarray] = []
    shot_batch_ranges = [
        (int(shot_batch_start), min(int(shot_batch_start + shot_batch_size), int(starts.size)))
        for shot_batch_start in range(0, int(starts.size), shot_batch_size)
    ]
    fast_shot_count = 0
    fallback_shot_count = 0
    fast_segment_count = 0
    total_segment_count = int(starts.size) * int(mm)
    with ThreadPoolExecutor(max_workers=1) as prefetch_executor:
        next_batch_future = None

        def _submit_prefetch(batch_range: tuple[int, int]):
            batch_begin, batch_end = batch_range
            batch_starts = np.asarray(starts[batch_begin:batch_end], dtype=int)
            def _runner():
                prep_t0 = perf_counter()
                starts_arr, local_batch, window_batch = _prepare_local_shot_batch_inputs(
                    segment_data,
                    batch_starts,
                    i_offset,
                    mm,
                    chunk_npts,
                )
                return starts_arr, local_batch, window_batch, perf_counter() - prep_t0

            return prefetch_executor.submit(_runner)

        if shot_batch_ranges:
            next_batch_future = _submit_prefetch(shot_batch_ranges[0])

        for batch_idx, _batch_range in enumerate(shot_batch_ranges):
            if next_batch_future is None:
                raise RuntimeError("Shot batch prefetch future was unexpectedly None.")
            wait_t0 = perf_counter()
            batch_starts, local_batch_data, window_batch, prepare_batch_s = next_batch_future.result()
            profile["prefetch_wait_s"] += perf_counter() - wait_t0
            profile["prepare_batch_s"] += prepare_batch_s
            if batch_idx + 1 < len(shot_batch_ranges):
                next_batch_future = _submit_prefetch(shot_batch_ranges[batch_idx + 1])
            else:
                next_batch_future = None

            n_batch_shots = int(batch_starts.size)

            shot_source_chunks_torch = None
            shot_source_chunks_np: np.ndarray | None = None
            shot_window_valid = np.zeros((n_batch_shots, mm), dtype=bool)
            fallback_local_data: dict[int, np.ndarray] = {}

            fast_segments: list[Any] = []
            fast_segment_map: list[tuple[int, int]] = []

            preprocess_t0 = perf_counter()
            for shot_local_idx, start in enumerate(batch_starts):
                local_data = local_batch_data[shot_local_idx]
                if local_data.shape[1] != i_offset:
                    raise ValueError(
                        f"Invalid local data shape at start={start}: got {local_data.shape}, expected second axis={i_offset}"
                    )

                per_window_data: list[np.ndarray] = []
                shot_fast = True
                shot_windows = window_batch[shot_local_idx]
                for win_idx in range(mm):
                    tdata = shot_windows[win_idx]
                    trace_stdS, dataS = _preprocess_raw_make_stat_compat(
                        tdata,
                        prepro_para,
                        return_numpy=not (all_device_mode and torch is not None and runtime_cc_device in {"mps", "cuda"}),
                    )
                    if all_device_mode and torch is not None and runtime_cc_device in {"mps", "cuda"}:
                        dataS_dev = (
                            dataS
                            if isinstance(dataS, torch.Tensor)
                            else torch.as_tensor(np.asarray(dataS, dtype=float), device=runtime_cc_device, dtype=torch_dtype)
                        )
                        row_max = torch.amax(torch.abs(dataS_dev), dim=1)
                        row_std = torch.std(dataS_dev, dim=1, unbiased=False)
                        trace_stdS_dev = row_max / torch.clamp(row_std, min=torch.finfo(row_std.dtype).eps)
                        valid_mask = (
                            torch.isfinite(trace_stdS_dev)
                            & (trace_stdS_dev > 0)
                            & (trace_stdS_dev < float(max_over_std))
                        )
                        if not bool(torch.all(valid_mask).item()):
                            shot_fast = False
                            break
                        per_window_data.append(dataS_dev)
                    else:
                        ind = np.where(
                            (trace_stdS < max_over_std)
                            & (trace_stdS > 0)
                            & ~np.isnan(trace_stdS)
                        )[0]
                        if not ind.size:
                            raise ValueError("the max_over_std criteria is too high which results in no data")
                        if ind.size != expected_nchan or not np.array_equal(ind, full_indices):
                            shot_fast = False
                            break
                        per_window_data.append(np.asarray(dataS, dtype=float))

                if shot_fast:
                    fast_shot_count += 1
                    for win_idx, dataS in enumerate(per_window_data):
                        fast_segments.append(dataS if (torch is not None and isinstance(dataS, torch.Tensor)) else np.asarray(dataS, dtype=float))
                        fast_segment_map.append((int(shot_local_idx), int(win_idx)))
                else:
                    fallback_shot_count += 1
                    fallback_local_data[int(shot_local_idx)] = local_data
            profile["fast_preprocess_s"] += perf_counter() - preprocess_t0
            fast_segment_count += len(fast_segments)

            if fast_segments:
                gpu_cc_t0 = perf_counter()
                for seg_start in range(0, len(fast_segments), segment_batch_size):
                    seg_stop = min(seg_start + segment_batch_size, len(fast_segments))
                    with _torch_inference_context():
                        if all_device_mode and torch is not None and runtime_cc_device in {"mps", "cuda"}:
                            seg_data = torch.stack([seg for seg in fast_segments[seg_start:seg_stop]], dim=0)
                        else:
                            seg_data = np.asarray(fast_segments[seg_start:seg_stop], dtype=float)
                        seg_map = fast_segment_map[seg_start:seg_stop]

                        white_spect_batch = DAS_module.noise_processing(
                            seg_data,
                            prepro_para,
                            return_numpy=False,
                        )
                        nfft = int(white_spect_batch.shape[-1])
                        data_white_batch = white_spect_batch[..., : nfft // 2]
                        source_spec_batch = data_white_batch[:, :i_offset, :]
                        sfft1_batch = DAS_module.smooth_source_spect(
                            source_spec_batch,
                            prepro_para,
                            return_numpy=False,
                        )
                        corr_batch, _ = DAS_module.correlate(
                            sfft1_batch,
                            data_white_batch,
                            prepro_para,
                            nfft,
                            return_numpy=False,
                        )
                        if all_device_mode and torch is not None and isinstance(corr_batch, torch.Tensor):
                            if corr_batch.ndim != 4:
                                raise ValueError(
                                    f"Unexpected batched correlation shape {tuple(corr_batch.shape)}; expected 4D (batch, nsrc, nrec, nlag)"
                                )
                            corr_batch_tensor = corr_batch.permute(0, 1, 3, 2).contiguous()
                            corr_batch_np = None
                        else:
                            corr_batch_np = _to_numpy_backend(corr_batch)
                            if corr_batch_np.ndim != 4:
                                raise ValueError(
                                    f"Unexpected batched correlation shape {corr_batch_np.shape}; expected 4D (batch, nsrc, nrec, nlag)"
                                )
                            corr_batch_np = np.ascontiguousarray(np.transpose(corr_batch_np, (0, 1, 3, 2)))
                            corr_batch_tensor = None

                    shot_idx_arr = np.asarray([shot_local_idx for shot_local_idx, _ in seg_map], dtype=int)
                    win_idx_arr = np.asarray([win_idx for _, win_idx in seg_map], dtype=int)
                    if corr_batch_tensor is None:
                        if corr_batch_np.shape[1] != i_offset or corr_batch_np.shape[3] != i_offset:
                            raise ValueError(
                                f"Unexpected source/receiver dimensions: {corr_batch_np.shape}, expected (*, {i_offset}, nlag, {i_offset})"
                            )
                        if shot_source_chunks_np is None:
                            shot_source_chunks_np = np.empty(
                                (n_batch_shots, i_offset, mm, corr_batch_np.shape[2], i_offset),
                                dtype=float,
                            )
                        shot_source_chunks_np[shot_idx_arr, :, win_idx_arr, :, :] = np.asarray(corr_batch_np, dtype=float)
                    else:
                        if int(corr_batch_tensor.shape[1]) != i_offset or int(corr_batch_tensor.shape[3]) != i_offset:
                            raise ValueError(
                                f"Unexpected source/receiver dimensions: {tuple(corr_batch_tensor.shape)}, expected (*, {i_offset}, nlag, {i_offset})"
                            )
                        if shot_source_chunks_torch is None:
                            shot_source_chunks_torch = torch.empty(
                                (n_batch_shots, i_offset, mm, int(corr_batch_tensor.shape[2]), i_offset),
                                dtype=corr_batch_tensor.dtype,
                                device=corr_batch_tensor.device,
                            )
                        shot_idx_tensor = torch.as_tensor(shot_idx_arr, device=corr_batch_tensor.device, dtype=torch.long)
                        win_idx_tensor = torch.as_tensor(win_idx_arr, device=corr_batch_tensor.device, dtype=torch.long)
                        shot_source_chunks_torch[shot_idx_tensor, :, win_idx_tensor, :, :] = corr_batch_tensor
                        del shot_idx_tensor
                        del win_idx_tensor
                    shot_window_valid[shot_idx_arr, win_idx_arr] = True
                    del seg_data
                    del white_spect_batch
                    del data_white_batch
                    del source_spec_batch
                    del sfft1_batch
                    del corr_batch
                    if corr_batch_tensor is not None:
                        del corr_batch_tensor
                    if corr_batch_np is not None:
                        del corr_batch_np
                profile["gpu_cc_s"] += perf_counter() - gpu_cc_t0

            for shot_local_idx, start in enumerate(batch_starts):
                if shot_local_idx in fallback_local_data:
                    local_data = fallback_local_data[shot_local_idx]
                    source_cubes: list[np.ndarray] = []
                    fallback_t0 = perf_counter()
                    for source_idx in range(i_offset):
                        local_params = dict(cc_params)
                        local_params["ishot_inf"] = int(source_idx)
                        gather = processor.compute_cc_unstacked_new(local_data, local_params)
                        source_cubes.append(np.asarray(gather.T, dtype=float))
                    profile["fallback_cpu_cc_s"] += perf_counter() - fallback_t0
                    local_shot_cubes.append(np.stack(source_cubes, axis=0))
                    continue

                source_cubes: list[np.ndarray] = []
                stack_t0 = perf_counter()
                if not bool(np.all(shot_window_valid[shot_local_idx])):
                    raise RuntimeError(
                        f"Missing CC chunks for shot={int(start)}"
                    )
                stack_runtime_t0 = perf_counter()
                if (
                    all_device_mode
                    and torch is not None
                    and runtime_cc_device in {"mps", "cuda"}
                    and shot_source_chunks_torch is not None
                ):
                    with _torch_inference_context():
                        gather_batch = _stack_cc_with_runtime(
                            shot_source_chunks_torch[shot_local_idx],
                            dt=float(dt),
                            smethod=stack_method,
                            cc_params=cc_params,
                        )
                else:
                    if shot_source_chunks_np is None:
                        raise RuntimeError(
                            f"Missing CC chunks for shot={int(start)}"
                        )
                    gather_batch = _stack_cc_with_runtime(
                        np.asarray(shot_source_chunks_np[shot_local_idx], dtype=float),
                        dt=float(dt),
                        smethod=stack_method,
                        cc_params=cc_params,
                    )
                profile["stack_runtime_s"] += perf_counter() - stack_runtime_t0
                stack_finalize_t0 = perf_counter()
                source_cubes = [np.asarray(source_cube, dtype=float) for source_cube in np.transpose(np.asarray(gather_batch, dtype=float), (0, 2, 1))]
                profile["stack_finalize_s"] += perf_counter() - stack_finalize_t0
                del gather_batch
                profile["stack_s"] += perf_counter() - stack_t0
                local_shot_cubes.append(np.stack(source_cubes, axis=0))

            del local_batch_data
            del window_batch
            del shot_window_valid
            if shot_source_chunks_torch is not None:
                del shot_source_chunks_torch
            if shot_source_chunks_np is not None:
                del shot_source_chunks_np

    align_t0 = perf_counter()
    local_cube_array = _align_local_cubes_lag(local_shot_cubes)
    profile["align_s"] += perf_counter() - align_t0
    profile["total_s"] = perf_counter() - file_t0
    if profile_enabled:
        print(
            f"[PROFILE] local-cc file={file_path.name} "
            f"total={_format_profile_seconds(profile['total_s'])} "
            f"read={_format_profile_seconds(profile['read_data_s'])} "
            f"prepare_batch={_format_profile_seconds(profile['prepare_batch_s'])} "
            f"prefetch_wait={_format_profile_seconds(profile['prefetch_wait_s'])} "
            f"fast_preprocess={_format_profile_seconds(profile['fast_preprocess_s'])} "
            f"gpu_cc={_format_profile_seconds(profile['gpu_cc_s'])} "
            f"fallback_cpu_cc={_format_profile_seconds(profile['fallback_cpu_cc_s'])} "
            f"stack={_format_profile_seconds(profile['stack_s'])} "
            f"stack_runtime={_format_profile_seconds(profile['stack_runtime_s'])} "
            f"stack_finalize={_format_profile_seconds(profile['stack_finalize_s'])} "
            f"align={_format_profile_seconds(profile['align_s'])}"
        )
        print(
            f"[PROFILE] local-cc counts file={file_path.name} "
            f"shots_total={int(starts.size)} fast_shots={int(fast_shot_count)} fallback_shots={int(fallback_shot_count)} "
            f"segments_total={int(total_segment_count)} fast_segments={int(fast_segment_count)}"
        )
    return (
        file_path.name,
        local_cube_array,
        dt,
        dx,
        segment_profile_x,
        starts.astype(int),
        int(base_i_offset),
    )


def save_stack_shots_mat(
    output_file: Path,
    shots_data: np.ndarray,
    dt: float,
    dx: float,
    dshot: float | None = None,
    cc_meta: dict[str, Any] | None = None,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    data_arr = np.asarray(shots_data, dtype=float)
    if data_arr.ndim == 2:
        data_arr = data_arr[None, :, :]

    mat_dict: dict[str, Any] = {
        "data": data_arr,
        "dt": float(dt),
        "dx": float(dx),
    }
    if dshot is not None:
        mat_dict["dshot"] = float(dshot)
    if cc_meta:
        for key, value in cc_meta.items():
            if value is None:
                continue
            if isinstance(value, str):
                mat_dict[key] = str(value)
            elif isinstance(value, (bool, np.bool_)):
                mat_dict[key] = bool(value)
            elif isinstance(value, (int, float, np.integer, np.floating)):
                mat_dict[key] = float(value)
            else:
                mat_dict[key] = value
    savemat(output_file, mat_dict)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def save_diagnostics_json(output_file: Path, diagnostics: dict[str, Any]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(_to_jsonable(diagnostics), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run_single_shot_tsi_on_local_cubes(
    local_cubes: np.ndarray,
    local_profile_x_list: list[np.ndarray],
    shot_starts: np.ndarray,
    dt: float,
    cc_params: dict[str, Any],
    tsi_mode_label: str,
    output_receiver_count: int | None = None,
    shot_source_indices: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    cubes = np.asarray(local_cubes, dtype=float)
    if cubes.ndim != 4 or cubes.shape[1] != cubes.shape[2]:
        raise ValueError(
            f"local_cubes must have shape (n_shots, nch, nch, nlag), got {cubes.shape}"
        )
    n_shots = int(cubes.shape[0])
    i_offset = int(cubes.shape[1])
    if i_offset < 3:
        raise ValueError(
            f"{tsi_mode_label} requires at least 3 channels in the offset window, got {i_offset}."
        )
    if len(local_profile_x_list) != n_shots:
        raise ValueError(
            f"local_profile_x_list length {len(local_profile_x_list)} != n_shots {n_shots}"
        )
    if int(np.asarray(shot_starts, dtype=int).size) != n_shots:
        raise ValueError(
            f"shot_starts size {int(np.asarray(shot_starts, dtype=int).size)} != n_shots {n_shots}"
        )
    if output_receiver_count is None:
        output_receiver_count = i_offset
    output_receiver_count = max(min(int(output_receiver_count), i_offset), 1)
    if shot_source_indices is None:
        shot_source_indices = np.zeros(n_shots, dtype=int)
    shot_source_indices = np.asarray(shot_source_indices, dtype=int).ravel()
    if shot_source_indices.size != n_shots:
        raise ValueError(f"shot_source_indices size {shot_source_indices.size} != n_shots {n_shots}")
    if np.any(shot_source_indices < 0) or np.any(shot_source_indices >= i_offset):
        raise ValueError("shot_source_indices has out-of-range source index.")

    shot_batch_size = max(int(cc_params.get("tsi_shot_batch_size", 1)), 1)
    show_tsi_progress = bool(cc_params.get("tsi_show_progress", True))

    base_local_profile_x = np.asarray(local_profile_x_list[0], dtype=float)
    base_local_profile_rel = base_local_profile_x - float(base_local_profile_x[0])

    def _make_tsi_processor(local_profile_x: np.ndarray) -> TSIProcessor:
        return TSIProcessor(
            np.asarray(local_profile_x, dtype=float),
            dt=dt,
            velocity_min=float(cc_params["tsi_taper_vmin"]),
            velocity_max=float(cc_params["tsi_taper_vmax"]),
            apply_velocity_gate=bool(cc_params["tsi_taper_enabled"]),
            outside_scale=float(cc_params["tsi_taper_outside_scale"]),
            shoulder_fraction=float(cc_params["tsi_taper_shoulder_fraction"]),
            backend=str(cc_params.get("cc_backend", "auto")),
            device=str(cc_params.get("_runtime_cc_device", cc_params.get("cc_device", "auto"))),
            dtype=str(cc_params.get("cc_dtype", "float32")),
        )

    processor = _make_tsi_processor(base_local_profile_x)
    enhanced_shots = []
    shot_diagnostics = []
    batch_starts_idx = list(range(0, n_shots, shot_batch_size))
    batch_iter = tqdm(
        batch_starts_idx,
        total=len(batch_starts_idx),
        desc=f"TSI {tsi_mode_label} batches",
        ncols=100,
        leave=False,
    ) if show_tsi_progress else batch_starts_idx

    starts = np.asarray(shot_starts, dtype=int)
    for batch_begin in batch_iter:
        batch_stop = min(batch_begin + shot_batch_size, n_shots)
        local_batch = np.asarray(cubes[batch_begin:batch_stop], dtype=processor.runtime.numpy_dtype)
        profile_batch = local_profile_x_list[batch_begin:batch_stop]

        can_batch = local_batch.shape[0] > 1
        if can_batch:
            for local_profile_x in profile_batch[1:]:
                local_profile_x = np.asarray(local_profile_x, dtype=float)
                if local_profile_x.size != i_offset:
                    can_batch = False
                    break
                local_rel = local_profile_x - float(local_profile_x[0])
                if not np.allclose(local_rel, base_local_profile_rel, rtol=0.0, atol=1e-9):
                    can_batch = False
                    break

        if can_batch:
            with _torch_inference_context():
                enhanced_batch, batch_diag = processor.enhance_batch(
                    local_batch,
                    max_iterations=int(cc_params["tsi_iterations"]),
                    stop_rel_l1=float(cc_params["tsi_stop_rel_l1"]),
                    show_progress=False,
                )
            per_shot_iterations = [
                int(v) for v in batch_diag.get("per_shot_iterations", [0] * local_batch.shape[0])
            ]
            per_shot_converged = [
                bool(v) for v in batch_diag.get("per_shot_converged", [False] * local_batch.shape[0])
            ]
            compute_backend = str(batch_diag.get("compute_backend", processor.runtime.backend))
            compute_device = str(batch_diag.get("compute_device", processor.runtime.device))
        else:
            enhanced_list = []
            per_shot_iterations = []
            per_shot_converged = []
            compute_backend = str(processor.runtime.backend)
            compute_device = str(processor.runtime.device)
            for local_idx in range(local_batch.shape[0]):
                local_profile_x = np.asarray(profile_batch[local_idx], dtype=float)
                local_rel = local_profile_x - float(local_profile_x[0])
                local_processor = processor
                if not np.allclose(local_rel, base_local_profile_rel, rtol=0.0, atol=1e-9):
                    local_processor = _make_tsi_processor(local_profile_x)
                with _torch_inference_context():
                    enhanced_cube, local_diag = local_processor.enhance(
                        local_batch[local_idx],
                        max_iterations=int(cc_params["tsi_iterations"]),
                        stop_rel_l1=float(cc_params["tsi_stop_rel_l1"]),
                        show_progress=False,
                    )
                enhanced_list.append(np.asarray(enhanced_cube, dtype=float))
                per_shot_iterations.append(int(local_diag.get("iterations_completed", 0)))
                per_shot_converged.append(bool(local_diag.get("converged", False)))
                compute_backend = str(local_diag.get("compute_backend", compute_backend))
                compute_device = str(local_diag.get("compute_device", compute_device))
            enhanced_batch = np.asarray(enhanced_list, dtype=float)

        for local_idx in range(enhanced_batch.shape[0]):
            shot_idx = int(batch_begin + local_idx)
            enhanced_cube = np.asarray(enhanced_batch[local_idx], dtype=float)
            source_idx = int(shot_source_indices[shot_idx])
            enhanced_shots.append(enhanced_cube[source_idx, :output_receiver_count, :].T)
            shot_diagnostics.append(
                {
                    "shot_index": int(shot_idx),
                    "start_channel": int(starts[shot_idx]),
                    "source_local_index": int(source_idx),
                    "iterations_completed": int(
                        per_shot_iterations[local_idx] if local_idx < len(per_shot_iterations) else 0
                    ),
                    "converged": bool(
                        per_shot_converged[local_idx] if local_idx < len(per_shot_converged) else False
                    ),
                    "compute_backend": compute_backend,
                    "compute_device": compute_device,
                }
            )
        del local_batch
        del enhanced_batch
        if can_batch:
            del batch_diag
        else:
            del enhanced_list

    diagnostics = {
        "cc_enhance_method": "tsi",
        "tsi_mode": tsi_mode_label,
        "n_shots": n_shots,
        "offset_channels": i_offset,
        "output_receiver_channels": int(output_receiver_count),
        "tsi_shot_batch_size": int(shot_batch_size),
        "tsi_show_progress": bool(show_tsi_progress),
        "compute_backend": str(processor.runtime.backend),
        "compute_device": str(processor.runtime.device),
        "shot_diagnostics": shot_diagnostics,
    }
    return np.asarray(enhanced_shots, dtype=float), diagnostics


def build_output_shots_from_stacked_cube(
    stacked_cube: np.ndarray,
    segment_profile_x: np.ndarray,
    dt: float,
    dx: float,
    cc_params: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    cube = np.asarray(stacked_cube, dtype=float)
    if cube.ndim != 3 or cube.shape[0] != cube.shape[1]:
        raise ValueError(f"stacked_cube must have shape (nch, nch, nt), got {cube.shape}")

    nch = int(cube.shape[0])
    i_dshot, i_offset, n_shots = build_shot_geometry(nch, dx, cc_params)
    all_shot_processor = AllShotTSIProcessor()
    raw_shots = all_shot_processor.extract_stack_shots(
        cube,
        dshot_channels=i_dshot,
        offset_channels=i_offset,
        n_shots=n_shots,
    )

    if cc_params["cc_enhance_method"] != "tsi":
        return raw_shots, {
            "cc_enhance_method": "raw",
            "n_shots": n_shots,
            "dshot_channels": i_dshot,
            "offset_channels": i_offset,
        }

    if cc_params["tsi_mode"] == TSI_MODE_STRICT_SPARSE_ALL_SHOT:
        loaded = all_shot_processor.from_arrays(
            stack_shots=raw_shots,
            dt=dt,
            dx=dx,
            dshot_channels=i_dshot,
            dshot_raw=float(cc_params.get("dshot", i_dshot * dx)),
        )
        result = all_shot_processor.enhance_all_shot(
            loaded,
            max_iterations=int(cc_params["tsi_iterations"]),
            stop_rel_l1=float(cc_params["tsi_stop_rel_l1"]),
            velocity_min=float(cc_params["tsi_taper_vmin"]),
            velocity_max=float(cc_params["tsi_taper_vmax"]),
            apply_velocity_gate=bool(cc_params["tsi_taper_enabled"]),
            outside_scale=float(cc_params["tsi_taper_outside_scale"]),
            shoulder_fraction=float(cc_params["tsi_taper_shoulder_fraction"]),
            backend=str(cc_params.get("cc_backend", "auto")),
            device=str(cc_params.get("_runtime_cc_device", cc_params.get("cc_device", "auto"))),
            dtype=str(cc_params.get("cc_dtype", "float32")),
        )
        diagnostics = dict(result["diagnostics"])
        diagnostics["cc_enhance_method"] = "tsi"
        diagnostics["tsi_mode"] = TSI_MODE_STRICT_SPARSE_ALL_SHOT
        return np.asarray(result["enhanced_shots"], dtype=float), diagnostics

    if cc_params["tsi_mode"] == TSI_MODE_SINGLE_SHOT:
        starts = list(range(0, nch - i_offset + 1, i_dshot))
        local_cubes = np.asarray(
            [cube[s:s + i_offset, s:s + i_offset, :] for s in starts],
            dtype=float,
        )
        local_profiles = [
            np.asarray(segment_profile_x[s:s + i_offset], dtype=float)
            for s in starts
        ]
        enhanced_shots, diagnostics = _run_single_shot_tsi_on_local_cubes(
            local_cubes=local_cubes,
            local_profile_x_list=local_profiles,
            shot_starts=np.asarray(starts, dtype=int),
            dt=dt,
            cc_params=cc_params,
            tsi_mode_label=TSI_MODE_SINGLE_SHOT,
        )
        diagnostics["dshot_channels"] = int(i_dshot)
        return enhanced_shots, diagnostics

    if cc_params["tsi_mode"] == TSI_MODE_SINGLE_SHOT_FINAL_ONCE:
        return raw_shots, {
            "cc_enhance_method": "raw",
            "deferred_tsi_mode": TSI_MODE_SINGLE_SHOT_FINAL_ONCE,
            "n_shots": n_shots,
            "dshot_channels": i_dshot,
            "offset_channels": i_offset,
        }

    raise ValueError(f"Unsupported tsi_mode: {cc_params['tsi_mode']}")


def build_output_shots_from_local_shot_cubes(
    stacked_local_shot_cubes: np.ndarray,
    segment_profile_x: np.ndarray,
    shot_starts: np.ndarray,
    dt: float,
    dx: float,
    cc_params: dict[str, Any],
    base_offset_channels: int | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    local_cubes = np.asarray(stacked_local_shot_cubes, dtype=float)
    if local_cubes.ndim != 4 or local_cubes.shape[1] != local_cubes.shape[2]:
        raise ValueError(
            f"stacked_local_shot_cubes must have shape (n_shots, nch, nch, nlag), got {local_cubes.shape}"
        )
    starts = np.asarray(shot_starts, dtype=int).ravel()
    if starts.size != int(local_cubes.shape[0]):
        raise ValueError(
            f"shot_starts size {starts.size} != n_shots {int(local_cubes.shape[0])}"
        )

    i_offset = int(local_cubes.shape[1])
    n_shots = int(local_cubes.shape[0])
    if base_offset_channels is None:
        base_offset_channels = max(int(round(float(cc_params.get("offset", dx)) / max(dx, 1e-12))), 2)
    base_offset_channels = max(min(int(base_offset_channels), i_offset), 1)
    nch = int(np.asarray(segment_profile_x, dtype=float).size)
    i_dshot, _i_offset_from_geom, _n_shots_geom = build_shot_geometry(nch, dx, cc_params)
    local_profiles = [
        np.asarray(segment_profile_x[s:s + i_offset], dtype=float)
        for s in starts
    ]

    if cc_params["cc_enhance_method"] != "tsi":
        raw_shots = np.asarray([local_cubes[i, 0, :base_offset_channels, :].T for i in range(n_shots)], dtype=float)
        return raw_shots, {
            "cc_enhance_method": "raw",
            "tsi_mode": TSI_MODE_SINGLE_SHOT_LOCAL_CC,
            "n_shots": n_shots,
            "dshot_channels": int(i_dshot),
            "offset_channels": int(base_offset_channels),
            "tsi_context_offset_channels": int(i_offset),
        }

    if cc_params["tsi_mode"] != TSI_MODE_SINGLE_SHOT_LOCAL_CC:
        raise ValueError(
            f"build_output_shots_from_local_shot_cubes only supports tsi_mode={TSI_MODE_SINGLE_SHOT_LOCAL_CC}, got {cc_params['tsi_mode']}"
        )

    enhanced_shots, diagnostics = _run_single_shot_tsi_on_local_cubes(
        local_cubes=local_cubes,
        local_profile_x_list=local_profiles,
        shot_starts=starts,
        dt=dt,
        cc_params=cc_params,
        tsi_mode_label=TSI_MODE_SINGLE_SHOT_LOCAL_CC,
        output_receiver_count=int(base_offset_channels),
    )
    diagnostics["dshot_channels"] = int(i_dshot)
    diagnostics["offset_channels"] = int(base_offset_channels)
    diagnostics["tsi_context_offset_channels"] = int(i_offset)
    return enhanced_shots, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description="CC + TSI calculation from a DAS folder.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/config/cc_config.jsonc"),
        help="Path to config JSONC (cc_config.jsonc or dispersion.jsonc).",
    )
    parser.add_argument(
        "--data-folder",
        type=Path,
        default=None,
        help="Folder containing data files (default: use cc_path from config).",
    )
    parser.add_argument(
        "--file-list",
        type=Path,
        default=None,
        help="Optional manifest file with one data file path per line. Relative paths are resolved against --data-folder.",
    )
    parser.add_argument(
        "--time-start",
        type=str,
        default=None,
        help="Keep only files with filename time >= HH:MM. Requires --time-end.",
    )
    parser.add_argument(
        "--time-end",
        type=str,
        default=None,
        help="Keep only files with filename time < HH:MM. Requires --time-start.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "cc_tsi_outputs",
        help="Folder to save grouped outputs.",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=10,
        help="Save one grouped output for every N valid files.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Start file index (0-based, inclusive).",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=None,
        help="End file index (0-based, inclusive). Default: last file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel worker count. 1 means serial.",
    )
    parser.add_argument(
        "--refresh-file-index",
        action="store_true",
        help="Force rebuilding the cached sorted file index for --data-folder.",
    )
    parser.add_argument(
        "--profile-stages",
        action="store_true",
        help="Print per-file profiling for local CC stages, including CPU prepare time, GPU CC time, and fallback counts.",
    )
    parser.add_argument(
        "--cc-enhance",
        choices=sorted(VALID_CC_ENHANCE),
        default=None,
        help="Override cc_enhance_method from config.",
    )
    parser.add_argument(
        "--cc-backend",
        choices=sorted(VALID_CC_BACKENDS),
        default=None,
        help="Override cc_backend from config.",
    )
    parser.add_argument(
        "--cc-device",
        choices=sorted(VALID_CC_DEVICES),
        default=None,
        help="Override cc_device from config.",
    )
    parser.add_argument(
        "--cc-batch-chunks",
        type=int,
        default=None,
        help="Override cc_batch_chunks from config.",
    )
    parser.add_argument(
        "--cc-shot-batch-size",
        type=int,
        default=None,
        help="Number of local shots processed together for GPU correlation in single_shot_local_cc mode.",
    )
    parser.add_argument(
        "--cc-dtype",
        choices=sorted(VALID_CC_DTYPES),
        default=None,
        help="Override cc_dtype from config.",
    )
    parser.add_argument(
        "--tsi-mode",
        choices=sorted(VALID_TSI_MODES),
        default=None,
        help="Override tsi_mode from config when cc_enhance_method=tsi.",
    )
    parser.add_argument(
        "--tsi-iterations",
        type=int,
        default=None,
        help="Override tsi_iterations from config.",
    )
    parser.add_argument(
        "--tsi-stop-rel-l1",
        type=float,
        default=None,
        help="Override tsi_stop_rel_l1 from config.",
    )
    parser.add_argument(
        "--tsi-local-expand-offsets",
        type=int,
        default=None,
        help="Extra offset-lengths to extend local CC window for TSI context in single_shot_local_cc mode.",
    )
    parser.add_argument(
        "--tsi-shot-batch-size",
        type=int,
        default=None,
        help="Number of single_shot windows processed per batched TSI call. Higher can improve GPU occupancy.",
    )
    parser.add_argument(
        "--tsi-progress",
        dest="tsi_show_progress",
        action="store_true",
        help="Show single_shot TSI batch progress bars in terminal.",
    )
    parser.add_argument(
        "--no-tsi-progress",
        dest="tsi_show_progress",
        action="store_false",
        help="Disable single_shot TSI batch progress bars in terminal.",
    )
    parser.set_defaults(tsi_show_progress=None)
    args = parser.parse_args()

    config = load_config_json(args.config)
    cc_params, config_folder = build_run_params(config)
    cc_params = apply_cli_overrides(args, cc_params)
    cc_params = with_runtime_cc_device(cc_params)
    cc_params["profile_stages"] = bool(args.profile_stages)

    cc_meta_for_mat = {
        "offset": cc_params.get("offset"),
        "freqmin": cc_params.get("freqmin"),
        "freqmax": cc_params.get("freqmax"),
        "freq_norm": cc_params.get("freq_norm"),
        "time_norm": cc_params.get("time_norm"),
        "cc_method": cc_params.get("cc_method"),
        "xmin": cc_params.get("cha1"),
        "xmax": cc_params.get("cha2"),
        "space_downsample": cc_params.get("space_downsample"),
        "time_downsample": cc_params.get("time_downsample"),
        "cc_enhance_method": cc_params.get("cc_enhance_method"),
        "cc_backend": cc_params.get("cc_backend"),
        "cc_device": cc_params.get("cc_device"),
        "cc_runtime_device": cc_params.get("_runtime_cc_device"),
        "cc_all_device_mode": bool(cc_params.get("_all_device_mode", False)),
        "cc_batch_chunks": cc_params.get("cc_batch_chunks"),
        "cc_shot_batch_size": cc_params.get("cc_shot_batch_size"),
        "cc_dtype": cc_params.get("cc_dtype"),
        "tsi_mode": cc_params.get("tsi_mode") if cc_params.get("cc_enhance_method") == "tsi" else None,
        "tsi_iterations": cc_params.get("tsi_iterations") if cc_params.get("cc_enhance_method") == "tsi" else None,
        "tsi_stop_rel_l1": cc_params.get("tsi_stop_rel_l1") if cc_params.get("cc_enhance_method") == "tsi" else None,
        "tsi_local_expand_offsets": cc_params.get("tsi_local_expand_offsets") if cc_params.get("cc_enhance_method") == "tsi" else None,
        "tsi_shot_batch_size": cc_params.get("tsi_shot_batch_size") if cc_params.get("cc_enhance_method") == "tsi" else None,
    }

    data_folder = Path(args.data_folder) if args.data_folder is not None else Path(config_folder)
    if data_folder is None or str(data_folder).strip() == "":
        raise ValueError("No data folder specified. Use --data-folder or set cc_path in config.")
    data_folder = data_folder.expanduser().resolve()
    if not data_folder.exists() or not data_folder.is_dir():
        raise ValueError(f"Data folder not found: {data_folder}")
    time_start = parse_hour_minute(args.time_start, arg_name="--time-start") if args.time_start is not None else None
    time_end = parse_hour_minute(args.time_end, arg_name="--time-end") if args.time_end is not None else None

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    file_index_cache_dir = output_dir / ".file_index_cache"
    if args.file_list is not None:
        file_list_path = Path(args.file_list).expanduser().resolve()
        if not file_list_path.exists() or not file_list_path.is_file():
            raise ValueError(f"file-list not found: {file_list_path}")
        files = load_files_from_manifest(file_list_path, data_folder)
        file_index_source = f"manifest:{file_list_path}"
    else:
        files, file_index_source = discover_files_with_cache(
            data_folder,
            cache_dir=file_index_cache_dir,
            refresh_cache=bool(args.refresh_file_index),
        )
    raw_total_files = len(files)
    files = filter_files_by_time_window(files, time_start=time_start, time_end=time_end)
    if not files:
        if time_start is not None and time_end is not None:
            raise FileNotFoundError(
                f"No supported DAS files matched time window "
                f"[{time_start.strftime('%H:%M')}, {time_end.strftime('%H:%M')}) in: {data_folder}"
            )
        raise FileNotFoundError(f"No supported DAS files found in: {data_folder}")

    total_files = len(files)
    start_index = int(args.start_index)
    end_index = (total_files - 1) if args.end_index is None else int(args.end_index)
    if start_index < 0 or end_index < 0:
        raise ValueError("start/end index must be >= 0")
    if start_index > end_index:
        raise ValueError("--start-index must be <= --end-index")
    if start_index >= total_files:
        raise ValueError(f"--start-index out of range: {start_index}, total files: {total_files}")
    if end_index >= total_files:
        raise ValueError(f"--end-index out of range: {end_index}, total files: {total_files}")
    save_every = int(args.save_every)
    if save_every <= 0:
        raise ValueError("--save-every must be > 0")

    selected_files = files[start_index:end_index + 1]
    workers = int(args.workers)
    if workers <= 0:
        raise ValueError("--workers must be >= 1")
    workers = min(workers, len(selected_files)) if selected_files else 1
    gpu_requested = should_force_single_worker_for_gpu(cc_params)
    if gpu_requested and workers > 1:
        print(
            "[WARN] GPU mode is enabled. For stability, forcing --workers=1 to avoid "
            "multi-process GPU contention."
        )
        workers = 1

    print(f"[INFO] config        : {args.config.resolve()}")
    print(f"[INFO] data folder   : {data_folder}")
    print(f"[INFO] output dir    : {output_dir}")
    print(f"[INFO] file index    : {file_index_source} ({file_index_cache_dir})")
    if time_start is not None and time_end is not None:
        print(f"[INFO] time window  : [{time_start.strftime('%H:%M')}, {time_end.strftime('%H:%M')})")
    print(f"[INFO] raw files     : {raw_total_files}")
    print(f"[INFO] total files   : {total_files}")
    print(f"[INFO] index range   : [{start_index}, {end_index}]")
    print(f"[INFO] selected      : {len(selected_files)}")
    print(f"[INFO] workers       : {workers}")
    print(f"[INFO] profiling    : {bool(cc_params.get('profile_stages', False))}")
    print(
        f"[INFO] cc runtime    : backend={cc_params['cc_backend']}, "
        f"device={cc_params['cc_device']}->runtime:{cc_params['_runtime_cc_device']}, "
        f"dtype={cc_params['cc_dtype']}, "
        f"batch_chunks={cc_params['cc_batch_chunks']}, shot_batch={cc_params['cc_shot_batch_size']}"
    )
    print(f"[INFO] all-device cc : {bool(cc_params.get('_all_device_mode', False))}")
    print(f"[INFO] cc enhance    : {cc_params['cc_enhance_method']}")
    print(f"[INFO] downsample    : time={cc_params['time_downsample']}, space={cc_params['space_downsample']}")
    if cc_params["cc_enhance_method"] == "tsi":
        print(f"[INFO] tsi mode      : {cc_params['tsi_mode']}")
        print(f"[INFO] tsi iterations: {cc_params['tsi_iterations']}")
        print(f"[INFO] tsi stop l1   : {cc_params['tsi_stop_rel_l1']:.6g}")
        print(f"[INFO] tsi local ext : {cc_params['tsi_local_expand_offsets']}")
        print(f"[INFO] tsi shot batch: {cc_params['tsi_shot_batch_size']}")
        print(f"[INFO] tsi progress  : {cc_params['tsi_show_progress']}")
        if is_single_shot_final_once_mode(cc_params):
            print("[INFO] tsi strategy  : defer TSI to one final pass after grouped stacks.")

    use_local_single_shot_cc = (
        cc_params["cc_enhance_method"] == "tsi"
        and cc_params["tsi_mode"] == TSI_MODE_SINGLE_SHOT_LOCAL_CC
    )
    if use_local_single_shot_cc:
        print(
            "[INFO] tsi strategy  : single_shot_local_cc (compute only per-shot local CC cubes; "
            "skip full pairwise global cube)."
        )

        group_local_cubes: list[np.ndarray] = []
        group_file_names: list[str] = []
        saved_group_count = 0
        skipped_for_stack = 0

        base_dt: float | None = None
        base_dx: float | None = None
        base_segment_profile_x: np.ndarray | None = None
        base_shot_starts: np.ndarray | None = None
        base_shot_shape: tuple[int, int, int] | None = None  # (n_shots, i_offset, ntlag)
        base_output_offset_channels: int | None = None

        def consume_one_local(
            file_name: str,
            local_shot_cubes: np.ndarray,
            dt_val: float,
            dx_val: float,
            segment_profile_x: np.ndarray,
            shot_starts: np.ndarray,
            output_offset_channels: int,
        ) -> None:
            nonlocal base_dt, base_dx, base_segment_profile_x, base_shot_starts, base_shot_shape
            nonlocal base_output_offset_channels
            nonlocal saved_group_count, skipped_for_stack

            local_shot_cubes = np.asarray(local_shot_cubes, dtype=float)
            segment_profile_x = np.asarray(segment_profile_x, dtype=float)
            shot_starts = np.asarray(shot_starts, dtype=int).ravel()
            output_offset_channels = int(output_offset_channels)

            if (
                local_shot_cubes.ndim != 4
                or local_shot_cubes.shape[1] != local_shot_cubes.shape[2]
            ):
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (invalid local cube shape): {file_name}, "
                    f"shape={local_shot_cubes.shape}"
                )
                return

            n_shots = int(local_shot_cubes.shape[0])
            i_offset = int(local_shot_cubes.shape[1])
            ntlag = int(local_shot_cubes.shape[3])
            if shot_starts.size != n_shots:
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (shot starts mismatch): {file_name}, "
                    f"starts={shot_starts.size}, n_shots={n_shots}"
                )
                return

            if base_shot_shape is None:
                base_dt = float(dt_val)
                base_dx = float(dx_val)
                base_segment_profile_x = segment_profile_x.copy()
                base_shot_starts = shot_starts.copy()
                base_shot_shape = (n_shots, i_offset, ntlag)
                base_output_offset_channels = int(output_offset_channels)
                i_dshot = int(base_shot_starts[1] - base_shot_starts[0]) if n_shots > 1 else 1
                print(
                    f"[INFO] first local   : n_shots={n_shots}, i_offset={i_offset}, ntlag={ntlag}, "
                    f"dt={base_dt:.6g}, dx={base_dx:.6g}"
                )
                print(
                    f"[INFO] local offset  : output={base_output_offset_channels}, tsi_context={i_offset}"
                )
                print(
                    f"[INFO] local geometry: i_dshot={i_dshot}, starts=[{int(base_shot_starts[0])}, "
                    f"{int(base_shot_starts[-1])}]"
                )

            if abs(float(dt_val) - float(base_dt)) > 1e-12 or abs(float(dx_val) - float(base_dx)) > 1e-9:
                print(
                    f"[WARN] dt/dx mismatch ignored: {file_name}, "
                    f"dt={dt_val}, dx={dx_val}, base_dt={base_dt}, base_dx={base_dx}"
                )
            if (
                segment_profile_x.shape != base_segment_profile_x.shape
                or not np.allclose(segment_profile_x, base_segment_profile_x)
            ):
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (segment profile mismatch): {file_name}, "
                    f"profile_shape={segment_profile_x.shape}, base_profile_shape={base_segment_profile_x.shape}"
                )
                return
            if not np.array_equal(shot_starts, base_shot_starts):
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (shot starts mismatch): {file_name}, "
                    f"starts={shot_starts[:3]}..., base_starts={base_shot_starts[:3]}..."
                )
                return
            if int(output_offset_channels) != int(base_output_offset_channels):
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (output offset mismatch): {file_name}, "
                    f"output_offset={output_offset_channels}, base_output_offset={base_output_offset_channels}"
                )
                return
            if local_shot_cubes.shape[0] != base_shot_shape[0] or local_shot_cubes.shape[1] != base_shot_shape[1]:
                skipped_for_stack += 1
                print(
                    f"[WARN] skip in stack (shot shape mismatch): {file_name}, "
                    f"shape={local_shot_cubes.shape}, base=({base_shot_shape[0]}, {base_shot_shape[1]}, *)"
                )
                return

            group_local_cubes.append(local_shot_cubes)
            group_file_names.append(file_name)
            if len(group_local_cubes) != save_every:
                return

            stacker = CCProcessor({"dt": float(base_dt)})
            stacked_local_shot_cubes: list[np.ndarray] = []
            for shot_idx in range(base_shot_shape[0]):
                shot_cube_list = [
                    np.asarray(file_local[shot_idx], dtype=float)
                    for file_local in group_local_cubes
                ]
                stacked_local_shot_cubes.append(
                    stacker.stack_pairwise_cubes(
                        shot_cube_list,
                        smethod=str(cc_params.get("stacking_method", "pws")),
                    )
                )
            stacked_local_shot_cubes_arr = _align_local_cubes_lag(stacked_local_shot_cubes)

            output_shots, diagnostics = build_output_shots_from_local_shot_cubes(
                stacked_local_shot_cubes=stacked_local_shot_cubes_arr,
                segment_profile_x=base_segment_profile_x,
                shot_starts=base_shot_starts,
                dt=float(base_dt),
                dx=float(base_dx),
                cc_params=cc_params,
                base_offset_channels=int(base_output_offset_channels),
            )
            print(
                f"[INFO] tsi compute   : backend={diagnostics.get('compute_backend', 'unknown')}, "
                f"device={diagnostics.get('compute_device', 'unknown')}"
            )

            saved_group_count += 1
            start_name = group_file_names[0]
            end_name = group_file_names[-1]
            suffix = (
                f"tsi_{cc_params['tsi_mode']}"
                if cc_params["cc_enhance_method"] == "tsi"
                else cc_params["cc_enhance_method"]
            )
            group_file = output_dir / (
                f"cc_stack_{suffix}_{saved_group_count:04d}_"
                f"n{save_every}_{Path(start_name).stem}_to_{Path(end_name).stem}.mat"
            )
            save_meta = dict(cc_meta_for_mat)
            save_meta["cc_local_mode"] = True
            try:
                n_out_shots = int(np.asarray(output_shots).shape[0])
                source_local_indices = _extract_source_local_indices_from_diagnostics(
                    diagnostics,
                    n_shots=n_out_shots,
                )
                shot_meta = build_shot_position_meta(
                    base_segment_profile_x,
                    np.asarray(base_shot_starts, dtype=int)[:n_out_shots],
                    source_local_indices=source_local_indices,
                )
                save_meta.update(shot_meta)
                diagnostics.update(shot_meta)
            except Exception as exc:
                print(f"[WARN] failed to append shot position metadata: {exc}")
            save_stack_shots_mat(
                group_file,
                output_shots,
                dt=float(base_dt),
                dx=float(base_dx),
                dshot=float(cc_params.get("dshot", base_dx)),
                cc_meta=save_meta,
            )
            diag_file = group_file.with_name(f"{group_file.stem}_diag.json")
            save_diagnostics_json(diag_file, diagnostics)
            print(f"[OK] saved diagnostics : {diag_file.name}")
            print(f"[OK] saved grouped stack: {group_file.name}")
            group_local_cubes.clear()
            group_file_names.clear()

        if workers == 1:
            for file_path in tqdm(selected_files, desc="Local CC shot-cube files", ncols=100, position=0):
                try:
                    result = process_file_for_local_shot_cubes(str(file_path), cc_params)
                    consume_one_local(*result)
                except Exception as exc:
                    skipped_for_stack += 1
                    print(f"[WARN] failed: {file_path.name}: {exc}")
                finally:
                    _maybe_clear_cuda_cache(cc_params)
        else:
            worker_fn = partial(process_file_for_local_shot_cubes, cc_params=cc_params)
            with ProcessPoolExecutor(max_workers=workers) as executor:
                mapped = executor.map(worker_fn, [str(p) for p in selected_files])
                for file_path, result in zip(
                    selected_files,
                    tqdm(mapped, total=len(selected_files), desc="Local CC shot-cube files", ncols=100, position=0),
                ):
                    try:
                        consume_one_local(*result)
                    except Exception as exc:
                        skipped_for_stack += 1
                        print(f"[WARN] failed: {file_path.name}: {exc}")

        if saved_group_count == 0:
            raise RuntimeError(
                f"No grouped stack was saved. Need at least {save_every} valid files with consistent segment geometry."
            )

        print(f"[OK] grouped output dir : {output_dir}")
        print(f"[OK] grouped saved      : {saved_group_count} groups, every {save_every} files/group.")
        if group_local_cubes:
            print(
                f"[INFO] remaining {len(group_local_cubes)} files (< {save_every}) "
                f"were not grouped into an extra stack."
            )
        print(f"[OK] skipped {skipped_for_stack} files.")
        return

    group_cubes: list[np.ndarray] = []
    group_file_names: list[str] = []
    saved_group_count = 0
    skipped_for_stack = 0
    final_once_grouped_cubes: list[np.ndarray] = []
    final_once_total_bytes = 0
    final_once_warned_memory = False

    base_channel_shape: tuple[int, int] | None = None
    base_dt: float | None = None
    base_dx: float | None = None
    base_segment_profile_x: np.ndarray | None = None

    def consume_one(
        file_name: str,
        cube: np.ndarray,
        dt_val: float,
        dx_val: float,
        segment_profile_x: np.ndarray,
    ) -> None:
        nonlocal base_channel_shape, base_dt, base_dx, base_segment_profile_x
        nonlocal saved_group_count, skipped_for_stack

        cube = np.asarray(cube, dtype=float)
        segment_profile_x = np.asarray(segment_profile_x, dtype=float)

        if base_channel_shape is None:
            base_channel_shape = cube.shape[:2]
            base_dt = float(dt_val)
            base_dx = float(dx_val)
            base_segment_profile_x = segment_profile_x.copy()
            i_dshot, i_offset, n_shots = build_shot_geometry(cube.shape[0], base_dx, cc_params)
            print(
                f"[INFO] first cube    : nch={cube.shape[0]}, ntlag={cube.shape[2]}, "
                f"dt={base_dt:.6g}, dx={base_dx:.6g}"
            )
            print(
                f"[INFO] shot geom     : dshot={float(cc_params.get('dshot', base_dx)):.6g}, "
                f"offset={float(cc_params.get('offset', base_dx)):.6g}, "
                f"i_dshot={i_dshot}, i_offset={i_offset}, n_shots={n_shots}"
            )

        if cube.shape[:2] != base_channel_shape:
            skipped_for_stack += 1
            print(
                f"[WARN] skip in stack (channel mismatch): {file_name}, "
                f"shape={cube.shape}, base={base_channel_shape}"
            )
            return
        if abs(float(dt_val) - float(base_dt)) > 1e-12 or abs(float(dx_val) - float(base_dx)) > 1e-9:
            print(
                f"[WARN] dt/dx mismatch ignored: {file_name}, "
                f"dt={dt_val}, dx={dx_val}, base_dt={base_dt}, base_dx={base_dx}"
            )
        if segment_profile_x.shape != base_segment_profile_x.shape or not np.allclose(segment_profile_x, base_segment_profile_x):
            skipped_for_stack += 1
            print(
                f"[WARN] skip in stack (segment profile mismatch): {file_name}, "
                f"profile_shape={segment_profile_x.shape}, base_profile_shape={base_segment_profile_x.shape}"
            )
            return

        group_cubes.append(cube)
        group_file_names.append(file_name)
        if len(group_cubes) != save_every:
            return

        stacker = CCProcessor({"dt": float(base_dt)})
        stacked_cube = stacker.stack_pairwise_cubes(
            group_cubes,
            smethod=str(cc_params.get("stacking_method", "pws")),
        )

        save_meta = dict(cc_meta_for_mat)
        if is_single_shot_final_once_mode(cc_params):
            final_once_grouped_cubes.append(np.asarray(stacked_cube, dtype=float))
            final_once_total_bytes += int(stacked_cube.nbytes)
            if (not final_once_warned_memory) and final_once_total_bytes > 2 * 1024 * 1024 * 1024:
                final_once_warned_memory = True
                print(
                    "[WARN] deferred final TSI is caching grouped cubes in memory; "
                    f"current cache ~{final_once_total_bytes / (1024**3):.2f} GiB."
                )
            raw_params = dict(cc_params)
            raw_params["cc_enhance_method"] = "raw"
            output_shots, diagnostics = build_output_shots_from_stacked_cube(
                stacked_cube,
                segment_profile_x=base_segment_profile_x,
                dt=float(base_dt),
                dx=float(base_dx),
                cc_params=raw_params,
            )
            save_meta["cc_enhance_method"] = "raw"
            save_meta["tsi_mode"] = TSI_MODE_SINGLE_SHOT_FINAL_ONCE
            save_meta["deferred_tsi"] = True
        else:
            output_shots, diagnostics = build_output_shots_from_stacked_cube(
                stacked_cube,
                segment_profile_x=base_segment_profile_x,
                dt=float(base_dt),
                dx=float(base_dx),
                cc_params=cc_params,
            )
        try:
            n_out_shots = int(np.asarray(output_shots).shape[0])
            i_dshot, i_offset, _ = build_shot_geometry(int(base_channel_shape[0]), float(base_dx), cc_params)
            starts = np.arange(0, int(base_channel_shape[0]) - int(i_offset) + 1, int(i_dshot), dtype=int)
            shot_meta = build_shot_position_meta(
                base_segment_profile_x,
                starts[:n_out_shots],
                source_local_indices=np.zeros(n_out_shots, dtype=int),
            )
            save_meta.update(shot_meta)
            diagnostics.update(shot_meta)
        except Exception as exc:
            print(f"[WARN] failed to append shot position metadata: {exc}")
        if cc_params["cc_enhance_method"] == "tsi":
            print(
                f"[INFO] tsi compute   : backend={diagnostics.get('compute_backend', 'unknown')}, "
                f"device={diagnostics.get('compute_device', 'unknown')}"
            )

        saved_group_count += 1
        start_name = group_file_names[0]
        end_name = group_file_names[-1]
        suffix = cc_params["cc_enhance_method"]
        if is_single_shot_final_once_mode(cc_params):
            suffix = "raw_pre_tsi_once"
        elif cc_params["cc_enhance_method"] == "tsi":
            suffix = f"tsi_{cc_params['tsi_mode']}"
        group_file = output_dir / (
            f"cc_stack_{suffix}_{saved_group_count:04d}_"
            f"n{save_every}_{Path(start_name).stem}_to_{Path(end_name).stem}.mat"
        )
        save_stack_shots_mat(
            group_file,
            output_shots,
            dt=float(base_dt),
            dx=float(base_dx),
            dshot=float(cc_params.get("dshot", base_dx)),
            cc_meta=save_meta,
        )
        if cc_params["cc_enhance_method"] == "tsi" and not is_single_shot_final_once_mode(cc_params):
            diag_file = group_file.with_name(f"{group_file.stem}_diag.json")
            save_diagnostics_json(diag_file, diagnostics)
            print(f"[OK] saved diagnostics : {diag_file.name}")
        print(f"[OK] saved grouped stack: {group_file.name}")
        group_cubes.clear()
        group_file_names.clear()

    if workers == 1:
        for file_path in tqdm(selected_files, desc="CC cube files", ncols=100, position=0):
            try:
                result = process_file_for_pairwise_cube(str(file_path), cc_params)
                consume_one(*result)
            except Exception as exc:
                skipped_for_stack += 1
                print(f"[WARN] failed: {file_path.name}: {exc}")
            finally:
                _maybe_clear_cuda_cache(cc_params)
    else:
        worker_fn = partial(process_file_for_pairwise_cube, cc_params=cc_params)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            mapped = executor.map(worker_fn, [str(p) for p in selected_files])
            for file_path, result in zip(
                selected_files,
                tqdm(mapped, total=len(selected_files), desc="CC cube files", ncols=100, position=0),
            ):
                try:
                    consume_one(*result)
                except Exception as exc:
                    skipped_for_stack += 1
                    print(f"[WARN] failed: {file_path.name}: {exc}")

    if saved_group_count == 0:
        raise RuntimeError(
            f"No grouped stack was saved. Need at least {save_every} valid files with consistent segment geometry."
        )

    if is_single_shot_final_once_mode(cc_params):
        if not final_once_grouped_cubes:
            raise RuntimeError(
                "Deferred final TSI requested, but no grouped cubes were cached for the final pass."
            )

        print(
            f"[INFO] deferred final TSI: stacking {len(final_once_grouped_cubes)} grouped cubes "
            f"with method={cc_params.get('stacking_method', 'pws')}"
        )
        final_stacker = CCProcessor({"dt": float(base_dt)})
        final_stacked_cube = final_stacker.stack_pairwise_cubes(
            final_once_grouped_cubes,
            smethod=str(cc_params.get("stacking_method", "pws")),
        )
        final_params = dict(cc_params)
        final_params["tsi_mode"] = TSI_MODE_SINGLE_SHOT
        final_output_shots, final_diagnostics = build_output_shots_from_stacked_cube(
            final_stacked_cube,
            segment_profile_x=base_segment_profile_x,
            dt=float(base_dt),
            dx=float(base_dx),
            cc_params=final_params,
        )
        final_suffix = f"tsi_{TSI_MODE_SINGLE_SHOT}_final_once"
        final_file = output_dir / (
            f"cc_stack_{final_suffix}_"
            f"ng{len(final_once_grouped_cubes)}_idx{start_index:04d}-{end_index:04d}.mat"
        )
        final_meta = dict(cc_meta_for_mat)
        final_meta["cc_enhance_method"] = "tsi"
        final_meta["tsi_mode"] = TSI_MODE_SINGLE_SHOT_FINAL_ONCE
        final_meta["final_tsi_applied_mode"] = TSI_MODE_SINGLE_SHOT
        final_meta["final_tsi_group_count"] = len(final_once_grouped_cubes)
        try:
            n_out_shots = int(np.asarray(final_output_shots).shape[0])
            i_dshot, i_offset, _ = build_shot_geometry(int(base_channel_shape[0]), float(base_dx), cc_params)
            starts = np.arange(0, int(base_channel_shape[0]) - int(i_offset) + 1, int(i_dshot), dtype=int)
            final_shot_meta = build_shot_position_meta(
                base_segment_profile_x,
                starts[:n_out_shots],
                source_local_indices=np.zeros(n_out_shots, dtype=int),
            )
            final_meta.update(final_shot_meta)
            final_diagnostics.update(final_shot_meta)
        except Exception as exc:
            print(f"[WARN] failed to append shot position metadata: {exc}")
        save_stack_shots_mat(
            final_file,
            final_output_shots,
            dt=float(base_dt),
            dx=float(base_dx),
            dshot=float(cc_params.get("dshot", base_dx)),
            cc_meta=final_meta,
        )
        final_diag_file = final_file.with_name(f"{final_file.stem}_diag.json")
        save_diagnostics_json(final_diag_file, final_diagnostics)
        print(f"[OK] saved deferred final TSI stack: {final_file.name}")
        print(f"[OK] saved deferred final diagnostics: {final_diag_file.name}")

    print(f"[OK] grouped output dir : {output_dir}")
    print(f"[OK] grouped saved      : {saved_group_count} groups, every {save_every} files/group.")
    if group_cubes:
        print(
            f"[INFO] remaining {len(group_cubes)} files (< {save_every}) "
            f"were not grouped into an extra stack."
        )
    print(f"[OK] skipped {skipped_for_stack} files.")


if __name__ == "__main__":
    main()
