#!/usr/bin/env python3
"""Resumable, position-aware raw-DAS and CC seasonal diagnostics.

The raw file is read once for all requested positions. All numerical images
remain in date-specific HDF5 files; CSV tables contain scalar measurements.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Any

import h5py
import numpy as np
from scipy import signal
from scipy.integrate import trapezoid
from scipy.io import loadmat

from dasqt.features.dispersion.backend.components.ccf.folder_pipeline import build_meta, normalize_orientation
from dasqt.features.dispersion.backend.components.utils.dispersion_utils import compute_dispersion_image
from dasqt.utils.config import load_jsonc_file
from dasqt.utils.io import read

PROJECT = Path(__file__).resolve().parents[1]
TIME_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})")
RAW_FIELDS = ("rms", "rms_cv", "invalid_channel_fraction", "fk_positive", "fk_negative", "fk_ratio", "target_background_power")
CC_FIELDS = ("ridge_coverage", "ridge_contrast", "unaliased_fraction", "aperture_resolved_fraction")
BRANCHES = ("positive", "negative")
PROCESSING_KEYS = (
    "freqmin", "freqmax", "freq_norm", "time_norm", "cc_method", "offset", "dshot",
    "xmin", "xmax", "space_downsample", "time_downsample", "cc_enhance_method",
    "cc_fk_selection_enabled", "cc_fk_selection_keep_fraction", "cc_fk_selection_aperture_m",
    "cc_fk_selection_stride_m", "cc_fk_selection_chunk_s", "cc_fk_selection_freqmin",
    "cc_fk_selection_freqmax", "cc_fk_selection_vmin", "cc_fk_selection_vmax",
    "cc_prewhiten_segment_once", "cc_prewhiten_mode", "cc_local_mode",
    "tsi_mode", "tsi_iterations", "tsi_stop_rel_l1", "tsi_local_expand_offsets",
    "tsi_files_per_group", "selection_mode",
)


def absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT / path).resolve()


def parse_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg = dict(load_jsonc_file(str(args.config)))
    cfg.setdefault("raw_workers", 4)
    for assignment in args.set or []:
        if "=" not in assignment:
            raise ValueError(f"--set requires KEY=VALUE, got {assignment!r}")
        key, value = assignment.split("=", 1)
        if key not in cfg:
            raise ValueError(f"Unknown config key in --set: {key}")
        try:
            cfg[key] = json.loads(value)
        except json.JSONDecodeError:
            cfg[key] = value
    for arg, key in (("raw_root", "raw_root"), ("cc_root", "cc_root"), ("output_dir", "output_dir"),
                     ("date_start", "date_start"), ("date_end", "date_end"),
                     ("time_start", "time_start"), ("time_end", "time_end"),
                     ("raw_step_minutes", "raw_step_minutes"), ("raw_workers", "raw_workers"),
                     ("tf_window_s", "tf_window_s"),
                     ("fk_aperture_m", "fk_aperture_m"), ("fk_chunk_s", "fk_chunk_s")):
        value = getattr(args, arg)
        if value is not None:
            cfg[key] = value
    if args.positions_m is not None:
        cfg["positions_m"] = args.positions_m
    for key in ("raw_root", "cc_root", "output_dir"):
        cfg[key] = absolute_path(str(cfg[key]))
    positions = np.asarray(cfg["positions_m"], dtype=float)
    if positions.ndim != 1 or not len(positions) or not np.all(np.isfinite(positions)) or len(set(positions)) != len(positions):
        raise ValueError("positions_m must contain distinct finite positions")
    cfg["positions_m"] = positions.tolist()
    if date.fromisoformat(cfg["date_start"]) > date.fromisoformat(cfg["date_end"]):
        raise ValueError("date_start must be <= date_end")
    if boundary_seconds(cfg["time_start"]) >= boundary_seconds(cfg["time_end"], allow_day_end=True):
        raise ValueError("time_start must be earlier than time_end")
    for key in ("raw_step_minutes", "raw_workers", "tf_window_s", "psd_window_s", "fk_chunk_s", "fk_time_decimation"):
        if float(cfg[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    if float(cfg["tf_window_s"]) not in (1, 10, 60):
        raise ValueError("tf_window_s currently supports 1, 10, or 60 seconds")
    if float(cfg["spectrum_fmax_hz"]) < max(cfg["background_high_hz"]):
        raise ValueError("spectrum_fmax_hz must cover background_high_hz")
    for lo, hi in (("target_fmin_hz", "target_fmax_hz"), ("disp_fmin_hz", "disp_fmax_hz"),
                   ("disp_vmin_mps", "disp_vmax_mps"), ("fk_vmin_mps", "fk_vmax_mps")):
        if float(cfg[lo]) >= float(cfg[hi]):
            raise ValueError(f"{lo} must be less than {hi}")
    return cfg


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def timestamp_from_name(path: Path) -> datetime:
    match = TIME_RE.search(path.name)
    if match is None:
        raise ValueError(f"No ChangBai timestamp in {path.name}")
    return datetime.strptime(match.group(0), "%Y-%m-%d-%H-%M-%S")


def boundary_seconds(value: str, *, allow_day_end: bool = False) -> float:
    if allow_day_end and value == "24:00":
        return 86400.0
    clock = time.fromisoformat(value)
    return clock.hour * 3600 + clock.minute * 60 + clock.second + clock.microsecond / 1e6


def dates_to_analyze(cfg: dict[str, Any], requested: list[str] | None) -> list[str]:
    if requested:
        dates = sorted(set(requested))
    else:
        dates = sorted({p.name for p in cfg["cc_root"].glob("20??-??-??") if p.is_dir()}
                       | {p.name for p in cfg["raw_root"].glob("20??-??-??") if p.is_dir()})
    lo, hi = cfg["date_start"], cfg["date_end"]
    return [day for day in dates if lo <= day <= hi]


def raw_files_for_day(cfg: dict[str, Any], day: str) -> list[Path]:
    folder = cfg["raw_root"] / day
    if not folder.is_dir():
        return []
    start = boundary_seconds(cfg["time_start"])
    end = boundary_seconds(cfg["time_end"], allow_day_end=True)
    interval = int(cfg["raw_step_minutes"])
    selected = []
    for path in folder.glob("*.dat"):
        try:
            stamp = timestamp_from_name(path)
        except ValueError:
            continue
        second_of_day = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
        elapsed = (stamp.hour * 60 + stamp.minute) - int(start // 60)
        if stamp.date().isoformat() == day and start <= second_of_day < end and elapsed % interval == 0:
            selected.append(path)
    return sorted(selected, key=timestamp_from_name)


def mat_files_for_day(cfg: dict[str, Any], day: str) -> list[Path]:
    folder = cfg["cc_root"] / day
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.mat"))


def parameter_subset(cfg: dict[str, Any], kind: str) -> dict[str, Any]:
    keys = ("positions_m", "time_start", "time_end", "raw_step_minutes", "tf_window_s",
            "psd_window_s", "spectrum_fmax_hz", "target_fmin_hz", "target_fmax_hz",
            "background_low_hz", "background_high_hz", "fk_aperture_m", "fk_chunk_s",
            "fk_time_decimation", "fk_fmax_hz", "fk_df_hz", "fk_vmin_mps", "fk_vmax_mps") if kind == "raw" else (
            "positions_m", "disp_fmin_hz", "disp_fmax_hz", "disp_df_hz", "disp_vmin_mps",
            "disp_vmax_mps", "disp_dv_mps", "disp_min_contrast", "disp_max_ridge_jump_mps")
    return {key: cfg[key] for key in keys}


def cache_path(cfg: dict[str, Any], kind: str, day: str, files: list[Path]) -> Path:
    fingerprint = digest({"schema": 3 if kind == "cc" else 1,
                          "config": parameter_subset(cfg, kind), "files": [file_identity(p) for p in files]})
    folder = cfg["output_dir"] / "cache" / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{day}_{fingerprint}.h5"


def h5_string_array(values: list[str]) -> np.ndarray:
    return np.asarray(values, dtype=object)


def compressed(h5: h5py.File, name: str, shape: tuple[int, ...], dtype: str = "f4") -> h5py.Dataset:
    chunks = (1,) + tuple(min(dim, 128) for dim in shape[1:])
    return h5.create_dataset(name, shape=shape, dtype=dtype, chunks=chunks,
                             compression="gzip", compression_opts=1, fillvalue=np.nan if dtype == "f4" else 0)


def band_power(freq: np.ndarray, power: np.ndarray, lo: float, hi: float) -> float:
    mask = (freq >= lo) & (freq <= hi)
    return float(trapezoid(power[mask], freq[mask])) if mask.sum() >= 2 else float("nan")


def fk_analysis(aperture: np.ndarray, dt: float, dx: float, cfg: dict[str, Any],
                fk_freq: np.ndarray, wave: np.ndarray) -> tuple[np.ndarray, float, float]:
    factor = int(cfg["fk_time_decimation"])
    decimated = signal.resample_poly(aperture, 1, factor, axis=0)
    fk_dt = dt * factor
    ntime = min(round(float(cfg["fk_chunk_s"]) / fk_dt), len(decimated))
    if ntime < 8:
        raise ValueError("FK time window has fewer than 8 samples")
    f_fft = np.fft.fftfreq(ntime, fk_dt)
    positive = np.flatnonzero(f_fft >= 0)
    index = positive[np.argmin(np.abs(f_fft[positive, None] - fk_freq[None, :]), axis=0)]
    taper = np.hanning(ntime)[:, None] * np.hanning(aperture.shape[1])[None, :]
    fft_power = []
    scores = [[], []]
    ff = fk_freq[:, None]
    kk = wave[None, :]
    velocity = np.divide(np.abs(ff), np.abs(kk), out=np.full((len(fk_freq), len(wave)), np.nan), where=np.abs(kk) > 0)
    frequency_mask = (ff >= cfg["target_fmin_hz"]) & (ff <= cfg["target_fmax_hz"])
    velocity_mask = (velocity >= cfg["fk_vmin_mps"]) & (velocity <= cfg["fk_vmax_mps"])
    for t0 in range(0, len(decimated) - ntime + 1, ntime):
        block = decimated[t0:t0 + ntime].astype(np.float32, copy=True)
        block -= block.mean(axis=0, keepdims=True)
        power = np.abs(np.fft.fftshift(np.fft.fft2(block * taper), axes=1)[index]) ** 2
        fft_power.append(power.astype(np.float32))
        for branch, sign in enumerate((kk > 0, kk < 0)):
            target = frequency_mask & velocity_mask & sign
            background = frequency_mask & ~velocity_mask & sign & np.isfinite(velocity)
            if target.any() and background.any():
                ratio = np.mean(power[target]) / max(np.mean(power[background]), 1e-20)
                scores[branch].append(float(ratio))
    image = np.median(fft_power, axis=0).astype(np.float32)
    return image, *(float(np.median(branch)) if branch else float("nan") for branch in scores)


def raw_axes(data: np.ndarray, metadata: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    norm = normalize_orientation(data, metadata)
    meta = build_meta(norm, metadata)
    dt, dx = float(meta["dt"]), float(meta["dx"])
    psd_nfft = max(round(float(cfg["psd_window_s"]) / dt), 8)
    freq_all = np.fft.rfftfreq(psd_nfft, dt)
    psd_freq = freq_all[freq_all <= float(cfg["spectrum_fmax_hz"])]
    fk_nx = min(max(round(float(cfg["fk_aperture_m"]) / dx), 4), norm.shape[1])
    fk_wave = np.fft.fftshift(np.fft.fftfreq(fk_nx, dx))
    fk_freq = np.arange(0, float(cfg["fk_fmax_hz"]) + 1e-9, float(cfg["fk_df_hz"]))
    # DAS minute files may contain one extra sample; do not create a nearly empty TF bin.
    n_tf = max(int(np.ceil(norm.shape[0] * dt / float(cfg["tf_window_s"]) - 1e-2)), 1)
    return dict(dt=dt, dx=dx, positions=np.asarray(meta["profileX"], float), psd_nfft=psd_nfft,
                psd_freq=psd_freq, fk_nx=fk_nx, fk_wave=fk_wave, fk_freq=fk_freq, n_tf=n_tf)


def create_raw_cache(path: Path, files: list[Path], cfg: dict[str, Any], axes: dict[str, Any]) -> None:
    n, p = len(files), len(cfg["positions_m"])
    with h5py.File(path, "w") as h5:
        h5.attrs["config"] = json.dumps(parameter_subset(cfg, "raw"), sort_keys=True)
        h5.attrs["dt"] = axes["dt"]
        h5.attrs["dx"] = axes["dx"]
        h5.attrs["tf_window_s"] = float(cfg["tf_window_s"])
        h5.attrs["psd_window_s"] = float(cfg["psd_window_s"])
        h5.create_dataset("files", data=h5_string_array([str(f) for f in files]), dtype=h5py.string_dtype())
        h5.create_dataset("timestamps", data=h5_string_array([timestamp_from_name(f).isoformat() for f in files]), dtype=h5py.string_dtype())
        h5.create_dataset("requested_x_m", data=np.asarray(cfg["positions_m"], float))
        h5.create_dataset("psd_frequency_hz", data=axes["psd_freq"])
        h5.create_dataset("fk_frequency_hz", data=axes["fk_freq"])
        h5.create_dataset("fk_wavenumber_cycles_per_m", data=axes["fk_wave"])
        h5.create_dataset("tf_time_offset_s", data=np.arange(axes["n_tf"]) * float(cfg["tf_window_s"]))
        h5.create_dataset("status", shape=(n,), dtype="i1", fillvalue=0)
        h5.create_dataset("error", shape=(n,), dtype=h5py.string_dtype())
        h5.create_dataset("tf_count", shape=(n,), dtype="i2")
        compressed(h5, "actual_x_m", (n, p))
        for field in RAW_FIELDS:
            compressed(h5, field, (n, p))
        compressed(h5, "psd_power", (n, p, len(axes["psd_freq"])))
        compressed(h5, "tf_power", (n, p, axes["n_tf"], len(axes["psd_freq"])))
        compressed(h5, "fk_power", (n, p, len(axes["fk_freq"]), len(axes["fk_wave"])))


def process_raw_file(data: np.ndarray, metadata: dict[str, Any], cfg: dict[str, Any],
                     h5: h5py.File, index: int) -> None:
    norm = normalize_orientation(data, metadata)
    meta = build_meta(norm, metadata)
    x = np.asarray(meta["profileX"], float)
    dt, dx = float(meta["dt"]), float(meta["dx"])
    if abs(dt - float(h5.attrs["dt"])) > 1e-6 or abs(dx - float(h5.attrs["dx"])) > 1e-3:
        raise ValueError("Sampling grid differs from the first file of this day")
    requested = np.asarray(cfg["positions_m"], float)
    if np.min(requested) < x[0] or np.max(requested) > x[-1]:
        raise ValueError(f"Requested position outside raw profile [{x[0]:.1f}, {x[-1]:.1f}] m")
    psd_freq = h5["psd_frequency_hz"][:]
    fk_freq = h5["fk_frequency_hz"][:]
    fk_wave = h5["fk_wavenumber_cycles_per_m"][:]
    nfft = round(float(cfg["psd_window_s"]) / dt)
    n_tf_samples = max(round(float(cfg["tf_window_s"]) / dt), 1)
    n_short_samples = max(round(10 / dt), 1)
    for j, position in enumerate(requested):
        channel = int(np.argmin(np.abs(x - position)))
        h5["actual_x_m"][index, j] = x[channel]
        center = norm[:, max(channel - 1, 0):min(channel + 2, norm.shape[1])].astype(np.float32, copy=True)
        finite = np.all(np.isfinite(center), axis=0)
        center[:, ~finite] = 0
        center -= center.mean(axis=0, keepdims=True)
        valid = finite & (np.std(center, axis=0) > 1e-12)
        if not valid.any():
            continue
        center = center[:, valid]
        rms_windows = [float(np.sqrt(np.mean(center[t:t+n_short_samples] ** 2)))
                       for t in range(0, len(center), n_short_samples) if len(center[t:t+n_short_samples]) > 0]
        h5["rms"][index, j] = np.median(rms_windows)
        h5["rms_cv"][index, j] = np.std(rms_windows) / max(np.mean(rms_windows), 1e-20)
        _, psd = signal.welch(center, fs=1 / dt, window="hann", nperseg=min(nfft, len(center)),
                              noverlap=min(nfft, len(center)) // 2, nfft=nfft, axis=0, detrend="constant")
        psd = np.median(psd[:len(psd_freq)], axis=1)
        h5["psd_power"][index, j] = psd.astype(np.float32)
        target = band_power(psd_freq, psd, cfg["target_fmin_hz"], cfg["target_fmax_hz"])
        low = band_power(psd_freq, psd, *cfg["background_low_hz"])
        high = band_power(psd_freq, psd, *cfg["background_high_hz"])
        h5["target_background_power"][index, j] = target / max(low + high, 1e-20)
        n_tf = min(int(np.ceil(len(center) / n_tf_samples)), h5["tf_power"].shape[2])
        h5["tf_count"][index] = n_tf
        for k in range(n_tf):
            segment = center[k*n_tf_samples:(k+1)*n_tf_samples]
            _, power = signal.welch(segment, fs=1 / dt, window="hann",
                                    nperseg=min(nfft, len(segment)),
                                    noverlap=min(nfft, len(segment)) // 2, nfft=nfft,
                                    axis=0, detrend="constant")
            h5["tf_power"][index, j, k] = np.median(power[:len(psd_freq)], axis=1).astype(np.float32)
        nx = len(fk_wave)
        start = max(0, min(channel - nx // 2, norm.shape[1] - nx))
        aperture = norm[:, start:start + nx].astype(np.float32, copy=True)
        aperture_valid = np.all(np.isfinite(aperture), axis=0) & (np.nanstd(aperture, axis=0) > 1e-12)
        h5["invalid_channel_fraction"][index, j] = 1 - float(np.mean(aperture_valid))
        if np.mean(aperture_valid) < 0.7:
            continue
        aperture[:, ~aperture_valid] = 0
        image, fk_positive, fk_negative = fk_analysis(aperture, dt, dx, cfg, fk_freq, fk_wave)
        h5["fk_power"][index, j] = image
        h5["fk_positive"][index, j] = fk_positive
        h5["fk_negative"][index, j] = fk_negative
        h5["fk_ratio"][index, j] = np.nanmedian((fk_positive, fk_negative))


def process_raw_day(cfg: dict[str, Any], day: str, files: list[Path]) -> Path | None:
    if not files:
        return None
    path = cache_path(cfg, "raw", day, files)
    first: tuple[np.ndarray, dict[str, Any]] | None = None
    first_index: int | None = None
    if not path.exists():
        for candidate_index, candidate in enumerate(files):
            try:
                first = read(str(candidate), output_type="array")
                axes = raw_axes(*first, cfg)
            except Exception as exc:
                print(f"[RAW ERROR] {day} {candidate.name}: {exc}", flush=True)
                continue
            first_index = candidate_index
            create_raw_cache(path, files, cfg, axes)
            break
        if first_index is None:
            print(f"[RAW] {day}: no readable raw files", flush=True)
            return None
    started = perf_counter()
    with h5py.File(path, "r+") as h5:
        for i, file in enumerate(files):
            if int(h5["status"][i]) == 1:
                continue
            try:
                for field in ("actual_x_m", *RAW_FIELDS, "psd_power", "tf_power", "fk_power"):
                    h5[field][i] = np.nan
                h5["tf_count"][i] = 0
                data, metadata = first if i == first_index and first is not None else read(str(file), output_type="array")
                process_raw_file(data, metadata, cfg, h5, i)
            except Exception as exc:  # A bad minute is reported without losing the date.
                h5["status"][i] = 2
                h5["error"][i] = str(exc)
                print(f"[RAW ERROR] {day} {file.name}: {exc}", flush=True)
            else:
                h5["status"][i] = 1
                h5["error"][i] = ""
            finally:
                if i == first_index:
                    first = None
                if (i + 1) % 10 == 0 or i + 1 == len(files):
                    h5.flush()
                    done = int(np.count_nonzero(h5["status"][:] == 1))
                    elapsed = perf_counter() - started
                    remaining = max(len(files) - done, 0) * elapsed / max(done, 1)
                    print(f"[RAW] {day} {done}/{len(files)} valid, {elapsed/60:.1f} min elapsed, ~{remaining/60:.1f} min remaining", flush=True)
    return path


def dispersion_axes(cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    frequency = np.arange(cfg["disp_fmin_hz"], cfg["disp_fmax_hz"] + 1e-9, cfg["disp_df_hz"])
    velocity = np.arange(cfg["disp_vmin_mps"], cfg["disp_vmax_mps"] + 1e-9, cfg["disp_dv_mps"])
    return frequency, velocity


def dispersion_quality(gather: np.ndarray, dx: float, dt: float, cfg: dict[str, Any],
                       frequency: np.ndarray, velocity: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    _, _, image = compute_dispersion_image(gather, dx, dt, frequency, velocity, "park")
    image = np.nan_to_num(np.abs(np.asarray(image, float)))
    if image.shape == (len(frequency), len(velocity)):
        image = image.T
    if image.shape != (len(velocity), len(frequency)):
        raise ValueError("Unexpected Park dispersion image shape")
    aperture_m = (gather.shape[1] - 1) * dx
    unaliased = velocity[:, None] >= 2 * dx * frequency[None, :]
    aperture_resolved = velocity[:, None] <= aperture_m * frequency[None, :]
    valid = unaliased & aperture_resolved
    valid_frequency = np.any(valid, axis=0)
    peak = np.max(np.where(valid, image, -np.inf), axis=0)
    background = np.asarray([np.median(image[valid[:, k], k]) if np.any(valid[:, k]) else np.nan
                             for k in range(len(frequency))])
    peak[~np.isfinite(peak)] = np.nan
    contrast = peak / np.maximum(background, 1e-20)
    ridge = velocity[np.argmax(np.where(valid, image, -np.inf), axis=0)]
    smooth = np.r_[True, np.abs(np.diff(ridge)) <= float(cfg["disp_max_ridge_jump_mps"])]
    usable = (contrast >= cfg["disp_min_contrast"]) & smooth & np.isfinite(contrast) & valid_frequency
    metrics = {
        "ridge_coverage": float(np.sum(usable) / np.sum(valid_frequency)) if valid_frequency.any() else float("nan"),
        "ridge_contrast": float(np.median(np.log(contrast[usable]))) if usable.any() else float("nan"),
        "unaliased_fraction": float(np.mean(np.any(unaliased, axis=0))),
        "aperture_resolved_fraction": float(np.mean(valid_frequency)),
    }
    return image.astype(np.float32), metrics


def mat_scalar(mat: dict[str, Any], name: str, default: Any = None) -> Any:
    value = mat.get(name, default)
    if isinstance(value, np.ndarray) and value.size == 1:
        return value.item()
    return value


def cc_signature(mat: dict[str, Any]) -> str:
    return digest({key: str(mat_scalar(mat, key, "missing")) for key in PROCESSING_KEYS})


def create_cc_cache(path: Path, files: list[Path], cfg: dict[str, Any]) -> None:
    n, p = len(files), len(cfg["positions_m"])
    frequency, velocity = dispersion_axes(cfg)
    with h5py.File(path, "w") as h5:
        h5.attrs["config"] = json.dumps(parameter_subset(cfg, "cc"), sort_keys=True)
        h5.create_dataset("files", data=h5_string_array([str(f) for f in files]), dtype=h5py.string_dtype())
        h5.create_dataset("requested_x_m", data=np.asarray(cfg["positions_m"], float))
        h5.create_dataset("frequency_hz", data=frequency)
        h5.create_dataset("velocity_mps", data=velocity)
        h5.create_dataset("branches", data=h5_string_array(list(BRANCHES)), dtype=h5py.string_dtype())
        h5.create_dataset("status", shape=(n,), dtype="i1", fillvalue=0)
        h5.create_dataset("error", shape=(n,), dtype=h5py.string_dtype())
        h5.create_dataset("processing_signature", shape=(n,), dtype=h5py.string_dtype())
        h5.create_dataset("processing_parameters", shape=(n,), dtype=h5py.string_dtype())
        h5.create_dataset("actual_file_count", shape=(n,), dtype="i4")
        compressed(h5, "actual_x_m", (n, p))
        for field in CC_FIELDS:
            compressed(h5, field, (n, p, 2))
        compressed(h5, "dispersion_power", (n, p, 2, len(velocity), len(frequency)))
        compressed(h5, "valid_velocity_mask", (n, p, len(velocity), len(frequency)), "u1")
        compressed(h5, "aperture_limited_frequency", (n, p, len(frequency)), "u1")
        compressed(h5, "alias_limited_frequency", (n, p, len(frequency)), "u1")


def process_cc_day(cfg: dict[str, Any], day: str, files: list[Path]) -> Path | None:
    if not files:
        return None
    path = cache_path(cfg, "cc", day, files)
    if not path.exists():
        create_cc_cache(path, files, cfg)
    with h5py.File(path, "r+") as h5:
        frequency = h5["frequency_hz"][:]
        velocity = h5["velocity_mps"][:]
        for i, file in enumerate(files):
            if int(h5["status"][i]) == 1:
                continue
            try:
                mat = loadmat(file, squeeze_me=True, variable_names=[
                    "data", "dt", "dx", "shot_source_x_m", "shot_start_x_m", "actual_file_count",
                    *PROCESSING_KEYS])
                cube = np.asarray(mat["data"], float)
                if cube.ndim != 3 or cube.shape[1] < 3:
                    raise ValueError("MAT data is not a CC (shot, lag, offset) cube")
                source_x = mat.get("shot_source_x_m", mat.get("shot_start_x_m"))
                if source_x is None:
                    raise ValueError("MAT has no shot coordinate array")
                source_x = np.asarray(source_x, float).reshape(-1)
                if len(source_x) != cube.shape[0]:
                    raise ValueError("MAT shot coordinates do not match cube")
                dt, dx = float(mat_scalar(mat, "dt")), float(mat_scalar(mat, "dx"))
                middle = cube.shape[1] // 2
                h5["processing_signature"][i] = cc_signature(mat)
                h5["processing_parameters"][i] = json.dumps(
                    {key: str(mat_scalar(mat, key, "missing")) for key in PROCESSING_KEYS}, sort_keys=True)
                h5["actual_file_count"][i] = int(mat_scalar(mat, "actual_file_count", 0))
                for j, position in enumerate(cfg["positions_m"]):
                    shot = int(np.argmin(np.abs(source_x - position)))
                    h5["actual_x_m"][i, j] = source_x[shot]
                    gather = cube[shot]
                    aperture_m = (gather.shape[1] - 1) * dx
                    valid = (velocity[:, None] >= 2 * dx * frequency[None, :]) & (
                        velocity[:, None] <= aperture_m * frequency[None, :])
                    h5["valid_velocity_mask"][i, j] = valid.astype(np.uint8)
                    h5["aperture_limited_frequency"][i, j] = np.any(
                        velocity[:, None] > aperture_m * frequency[None, :], axis=0).astype(np.uint8)
                    h5["alias_limited_frequency"][i, j] = np.any(
                        velocity[:, None] < 2 * dx * frequency[None, :], axis=0).astype(np.uint8)
                    for branch, panel in enumerate((gather[middle:], gather[middle::-1])):
                        image, metrics = dispersion_quality(panel, dx, dt, cfg, frequency, velocity)
                        h5["dispersion_power"][i, j, branch] = image
                        for key, value in metrics.items():
                            h5[key][i, j, branch] = value
            except Exception as exc:
                h5["status"][i] = 2
                h5["error"][i] = str(exc)
                print(f"[CC ERROR] {day} {file.name}: {exc}", flush=True)
            else:
                h5["status"][i] = 1
                h5["error"][i] = ""
            h5.flush()
            print(f"[CC] {day} {i+1}/{len(files)} {file.name}", flush=True)
    return path


def csv_write(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def finite_stats(values: list[float]) -> tuple[int, float, float, float]:
    data = np.asarray(values, float)
    data = data[np.isfinite(data)]
    if not len(data):
        return 0, float("nan"), float("nan"), float("nan")
    return len(data), float(np.median(data)), float(np.quantile(data, .25)), float(np.quantile(data, .75))


def collect_tables(cfg: dict[str, Any], manifest: dict[str, Any]) -> None:
    out = cfg["output_dir"]
    raw_rows: list[dict[str, Any]] = []
    cc_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    group_rows: dict[str, str] = {}
    for day, entry in manifest["days"].items():
        raw_path = entry.get("raw_cache")
        if raw_path and Path(raw_path).exists():
            with h5py.File(raw_path) as h5:
                files = h5["files"].asstr()[:]
                stamps = h5["timestamps"].asstr()[:]
                status_array = h5["status"][:]
                actual_x = h5["actual_x_m"][:]
                field_values = {field: h5[field][:] for field in RAW_FIELDS}
                for i, status in enumerate(status_array):
                    if status == 2:
                        failure_rows.append({"source": "raw", "day": day, "file": files[i], "error": h5["error"].asstr()[i]})
                    if status != 1:
                        continue
                    for j, position in enumerate(cfg["positions_m"]):
                        row = {"day": day, "month": day[:7], "timestamp": stamps[i], "file": files[i],
                               "requested_x_m": position, "actual_x_m": float(actual_x[i, j])}
                        row.update({field: float(field_values[field][i, j]) for field in RAW_FIELDS})
                        raw_rows.append(row)
        cc_path = entry.get("cc_cache")
        if cc_path and Path(cc_path).exists():
            with h5py.File(cc_path) as h5:
                files = h5["files"].asstr()[:]
                for i, status in enumerate(h5["status"][:]):
                    if status == 2:
                        failure_rows.append({"source": "cc", "day": day, "file": files[i], "error": h5["error"].asstr()[i]})
                    if status != 1:
                        continue
                    signature = h5["processing_signature"].asstr()[i]
                    group_rows[signature] = h5["processing_parameters"].asstr()[i]
                    for j, position in enumerate(cfg["positions_m"]):
                        for branch_id, branch in enumerate(BRANCHES):
                            row = {"day": day, "month": day[:7], "file": files[i],
                                   "requested_x_m": position, "actual_x_m": float(h5["actual_x_m"][i, j]),
                                   "branch": branch, "processing_signature": signature,
                                   "actual_file_count": int(h5["actual_file_count"][i])}
                            row.update({field: float(h5[field][i, j, branch_id]) for field in CC_FIELDS})
                            cc_rows.append(row)
    csv_write(out / "raw_per_file_position.csv", raw_rows,
              ["day", "month", "timestamp", "file", "requested_x_m", "actual_x_m", *RAW_FIELDS])
    csv_write(out / "cc_per_mat_position.csv", cc_rows,
              ["day", "month", "file", "requested_x_m", "actual_x_m", "branch", "processing_signature",
               "actual_file_count", *CC_FIELDS])
    csv_write(out / "failures.csv", failure_rows, ["source", "day", "file", "error"])
    csv_write(out / "cc_processing_groups.csv",
              [{"processing_signature": key, "parameters_json": value} for key, value in sorted(group_rows.items())],
              ["processing_signature", "parameters_json"])
    daily: list[dict[str, Any]] = []
    for source, rows, fields in (("raw", raw_rows, RAW_FIELDS), ("cc", cc_rows, CC_FIELDS)):
        buckets: dict[tuple[str, str, float, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            key = (source, row["day"], float(row["requested_x_m"]), row.get("branch", "both"),
                   row.get("processing_signature", "raw"))
            buckets.setdefault(key, []).append(row)
        for (source_name, day, position, branch, signature), items in buckets.items():
            for metric in fields:
                count, median, q25, q75 = finite_stats([float(item[metric]) for item in items])
                daily.append({"source": source_name, "day": day, "month": day[:7], "requested_x_m": position,
                              "branch": branch, "processing_signature": signature, "metric": metric,
                              "n_units": count, "median": median, "q25": q25, "q75": q75})
    daily_fields = ["source", "day", "month", "requested_x_m", "branch", "processing_signature", "metric",
                    "n_units", "median", "q25", "q75"]
    csv_write(out / "daily_summary.csv", daily, daily_fields)
    monthly: list[dict[str, Any]] = []
    buckets = {}
    for row in daily:
        key = (row["source"], row["month"], row["requested_x_m"], row["branch"],
               row["processing_signature"], row["metric"])
        buckets.setdefault(key, []).append(row)
    for (source, month, position, branch, signature, metric), items in buckets.items():
        count, median, q25, q75 = finite_stats([float(item["median"]) for item in items])
        monthly.append({"source": source, "month": month, "requested_x_m": position, "branch": branch,
                        "processing_signature": signature, "metric": metric, "n_days": count,
                        "n_units": sum(int(item["n_units"]) for item in items),
                        "median": median, "q25": q25, "q75": q75})
    csv_write(out / "monthly_summary.csv", monthly,
              ["source", "month", "requested_x_m", "branch", "processing_signature", "metric",
               "n_days", "n_units", "median", "q25", "q75"])
    print(f"[TABLES] raw={len(raw_rows)} position-minutes, CC={len(cc_rows)} position-MAT branches, failures={len(failure_rows)}", flush=True)


def run(args: argparse.Namespace) -> None:
    cfg = parse_config(args)
    out = cfg["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "run_manifest.json"
    if args.phase == "status":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for kind in ("raw", "cc"):
            expected = sum(entry[f"{kind}_count"] for entry in manifest["days"].values())
            valid = failed = busy = 0
            complete_days = 0
            for entry in manifest["days"].values():
                path = entry.get(f"{kind}_cache")
                if not path or not Path(path).exists():
                    continue
                try:
                    with h5py.File(path) as h5:
                        status = h5["status"][:]
                except OSError:
                    busy += 1
                    continue
                valid += int(np.count_nonzero(status == 1))
                failed += int(np.count_nonzero(status == 2))
                complete_days += int(np.all(status == 1))
            print(f"[{kind.upper()} STATUS] valid={valid}/{expected}, failed={failed}, "
                  f"complete_days={complete_days}, busy_day_files={busy}")
        return
    if args.phase == "plot":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["config_hash"] != digest(cfg):
            raise ValueError("The current config differs from the saved run; use the saved config or recompute")
        collect_tables(cfg, manifest)
    else:
        days = dates_to_analyze(cfg, args.dates)
        if not days:
            raise FileNotFoundError("No date folders matched the requested range")
        cfg_snapshot = {k: str(v) if isinstance(v, Path) else v for k, v in cfg.items()}
        (out / "config_snapshot.json").write_text(json.dumps(cfg_snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        first_started_at = previous.get("first_started_at") if previous.get("config_hash") == digest(cfg) else None
        manifest = {"config_hash": digest(cfg),
                    "first_started_at": first_started_at or datetime.now().astimezone().isoformat(),
                    "last_started_at": datetime.now().astimezone().isoformat(), "days": {}}
        input_rows = []
        for day in days:
            raw_files, mat_files = raw_files_for_day(cfg, day), mat_files_for_day(cfg, day)
            entry = {"raw_count": len(raw_files), "cc_count": len(mat_files),
                     "raw_cache": str(cache_path(cfg, "raw", day, raw_files)) if raw_files else None,
                     "cc_cache": str(cache_path(cfg, "cc", day, mat_files)) if mat_files else None}
            manifest["days"][day] = entry
            for kind, files in (("raw", raw_files), ("cc", mat_files)):
                for file in files:
                    input_rows.append({"kind": kind, "day": day, **file_identity(file)})
        csv_write(out / "input_manifest.csv", input_rows, ["kind", "day", "path", "size", "mtime_ns"])
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[RUN] {len(days)} dates, {sum(v['raw_count'] for v in manifest['days'].values())} raw DAT, "
              f"{sum(v['cc_count'] for v in manifest['days'].values())} CC MAT", flush=True)
        if args.phase in ("all", "raw"):
            pending = [(day, raw_files_for_day(cfg, day)) for day in days]
            pending = [(day, files) for day, files in pending if files]
            workers = min(int(cfg["raw_workers"]), len(pending))
            if workers > 1:
                started = perf_counter()
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(process_raw_day, cfg, day, files): day for day, files in pending}
                    for done_count, future in enumerate(as_completed(futures), start=1):
                        future.result()
                        elapsed = perf_counter() - started
                        remaining = (len(pending) - done_count) * elapsed / max(done_count * workers, 1)
                        print(f"[RAW TOTAL] {done_count}/{len(pending)} dates complete, "
                              f"{elapsed/3600:.2f} h elapsed, ~{remaining/3600:.2f} h remaining "
                              f"at {workers} workers (early estimate)", flush=True)
            else:
                for day, files in pending:
                    process_raw_day(cfg, day, files)
        if args.phase in ("all", "cc"):
            for day in days:
                process_cc_day(cfg, day, mat_files_for_day(cfg, day))
        collect_tables(cfg, manifest)
        manifest["last_finished_at"] = datetime.now().astimezone().isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.phase in ("all", "plot"):
        from plot_seasonal_diagnosis_detailed import make_plots
        from seasonal_diagnosis_report import write_report
        make_plots(cfg, manifest)
        report = write_report(cfg, manifest)
        print(f"[REPORT] {report}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT / "config/seasonal_diagnosis.jsonc")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Override any config key; use JSON syntax for arrays/numbers/booleans")
    parser.add_argument("--phase", choices=("all", "raw", "cc", "plot", "status"), default="all")
    parser.add_argument("--dates", nargs="+", help="Exact dates; default uses the configured inclusive date range")
    parser.add_argument("--raw-root")
    parser.add_argument("--cc-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--date-start")
    parser.add_argument("--date-end")
    parser.add_argument("--time-start")
    parser.add_argument("--time-end")
    parser.add_argument("--positions-m", type=float, nargs="+")
    parser.add_argument("--raw-step-minutes", type=int)
    parser.add_argument("--raw-workers", type=int)
    parser.add_argument("--tf-window-s", type=float)
    parser.add_argument("--fk-aperture-m", type=float)
    parser.add_argument("--fk-chunk-s", type=float)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
