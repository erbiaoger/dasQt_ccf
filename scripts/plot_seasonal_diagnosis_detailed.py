#!/usr/bin/env python3
"""Draw seasonal and date-specific figures from cached numeric diagnostics."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import h5py
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import loadmat

mpl.use("Agg")
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "pdf.fonttype": 42,
})
BRANCHES = ("positive", "negative")


def day_boundary(day: datetime, value: str) -> datetime:
    if value == "24:00":
        return datetime.combine(day.date(), time.min) + timedelta(days=1)
    return datetime.combine(day.date(), time.fromisoformat(value))


def rows_from(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def export(fig: Any, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def monthly_value(rows: list[dict[str, str]], source: str, metric: str, position: float,
                  branch: str = "both", signature: str = "raw") -> dict[str, dict[str, float]]:
    selected = [row for row in rows if row["source"] == source and row["metric"] == metric
                and float(row["requested_x_m"]) == position and row["branch"] == branch
                and row["processing_signature"] == signature]
    return {row["month"]: {key: float(row[key]) for key in ("median", "q25", "q75", "n_days", "n_units")}
            for row in selected}


def seasonal_pages(cfg: dict[str, Any], monthly: list[dict[str, str]], output: Path) -> None:
    positions = [float(x) for x in cfg["positions_m"]]
    months = sorted({row["month"] for row in monthly})
    signatures = sorted({row["processing_signature"] for row in monthly if row["source"] == "cc"}) or ["raw_only"]
    panels = [
        ("cc", "positive", "ridge_coverage", "a  Positive-lag dispersion coverage"),
        ("cc", "negative", "ridge_coverage", "b  Negative-lag dispersion coverage"),
        ("cc", "positive", "ridge_contrast", "c  Positive-lag ridge contrast"),
        ("cc", "negative", "ridge_contrast", "d  Negative-lag ridge contrast"),
        ("raw", "both", "fk_ratio", "e  Local target-velocity FK ratio"),
        ("raw", "both", "rms", "f  Raw signal RMS"),
        ("raw", "both", "target_background_power", "g  Target/outside-band power"),
        ("raw", "both", "invalid_channel_fraction", "h  Invalid-channel fraction"),
    ]
    for signature in signatures:
        for page, start in enumerate(range(0, len(positions), 5), start=1):
            subset = positions[start:start + 5]
            colors = plt.cm.tab10(np.linspace(0, .8, len(subset)))
            fig, axes = plt.subplots(4, 2, figsize=(10.6, 10.5), constrained_layout=True)
            for ax, (source, branch, metric, title) in zip(axes.ravel(), panels):
                for position, color in zip(subset, colors):
                    values = monthly_value(monthly, source, metric, position, branch,
                                           signature if source == "cc" else "raw")
                    y = np.array([values[m]["median"] if m in values else np.nan for m in months])
                    q25 = np.array([values[m]["q25"] if m in values else np.nan for m in months])
                    q75 = np.array([values[m]["q75"] if m in values else np.nan for m in months])
                    ax.plot(range(len(months)), y, "o-", linewidth=1.4, markersize=3.5,
                            color=color, label=f"{position/1000:g} km")
                    ax.fill_between(range(len(months)), q25, q75, color=color, alpha=.12, linewidth=0)
                ax.set_title(title, loc="left", weight="bold")
                counts = monthly_value(monthly, source, metric, subset[0], branch,
                                       signature if source == "cc" else "raw")
                ax.set_xticks(range(len(months)),
                              [f"{month[5:]}\n{int(counts[month]['n_days'])} d" if month in counts else month[5:]
                               for month in months])
                ax.set_xlabel("Month of 2025")
                ax.grid(axis="y", color="0.9", linewidth=.6)
                if metric in {"ridge_coverage", "invalid_channel_fraction"}:
                    ax.set_ylim(0, 1)
            axes[0, 0].legend(loc="best", ncol=min(len(subset), 3), fontsize=7)
            fig.suptitle(f"Seasonal DAS diagnostics | CC processing group {signature} | positions {start+1}–{start+len(subset)}\n"
                         "Lines show monthly medians of daily medians; bands show daily IQR", fontsize=10)
            export(fig, output / f"seasonal_summary_{signature}_page{page}")


def daily_heatmaps(cfg: dict[str, Any], daily: list[dict[str, str]], output: Path) -> None:
    positions = np.asarray(cfg["positions_m"], float)
    days = sorted({row["day"] for row in daily})
    signatures = sorted({row["processing_signature"] for row in daily if row["source"] == "cc"}) or ["raw_only"]
    specs = [("cc", "positive", "ridge_coverage", "Positive-lag coverage"),
             ("cc", "negative", "ridge_coverage", "Negative-lag coverage"),
             ("raw", "both", "fk_ratio", "Local FK ratio"),
             ("raw", "both", "rms", "Raw RMS")]
    lookup = {(row["source"], row["branch"], row["processing_signature"], row["metric"],
               row["day"], float(row["requested_x_m"])): float(row["median"]) for row in daily}
    for signature in signatures:
        fig, axes = plt.subplots(2, 2, figsize=(11, 5.6), constrained_layout=True)
        for ax, (source, branch, metric, title) in zip(axes.ravel(), specs):
            key_sig = signature if source == "cc" else "raw"
            values = np.array([[lookup.get((source, branch, key_sig, metric, day, position), np.nan)
                                for day in days] for position in positions], float)
            cmap = plt.cm.viridis.copy()
            cmap.set_bad("#eeeeee")
            image = ax.imshow(np.ma.masked_invalid(values), aspect="auto", origin="lower", cmap=cmap,
                              interpolation="nearest", vmin=0)
            ax.set_title(title, loc="left", weight="bold")
            ax.set_yticks(range(len(positions)), [f"{p/1000:g}" for p in positions])
            ax.set_ylabel("Position (km)")
            ticks = np.unique(np.linspace(0, len(days)-1, min(6, len(days))).round().astype(int))
            ax.set_xticks(ticks, [days[i][5:] for i in ticks], rotation=30, ha="right",
                          rotation_mode="anchor")
            fig.colorbar(image, ax=ax, shrink=.8, pad=.01)
        fig.suptitle(f"Daily location map | CC processing group {signature} | grey = no observation", fontsize=10)
        export(fig, output / f"daily_position_heatmap_{signature}")


def shared_color_limits(cfg: dict[str, Any], manifest: dict[str, Any]) -> dict[str, list[float]]:
    """Estimate fixed display limits from evenly sampled completed minutes."""
    samples: dict[str, list[np.ndarray]] = {"tf": [], "fk": []}
    for entry in manifest["days"].values():
        path = entry.get("raw_cache")
        if not path or not Path(path).exists():
            continue
        with h5py.File(path) as h5:
            good = np.flatnonzero(h5["status"][:] == 1)
            if not len(good):
                continue
            for index in good[np.linspace(0, len(good)-1, min(len(good), 4)).round().astype(int)]:
                for key, dataset in (("tf", "tf_power"), ("fk", "fk_power")):
                    power = np.asarray(h5[dataset][index], float)
                    finite = power[np.isfinite(power) & (power > 0)]
                    if finite.size:
                        samples[key].append(10*np.log10(finite[::max(1, finite.size//500)]))
    limits = {}
    for key, blocks in samples.items():
        if blocks:
            values = np.concatenate(blocks)
            limits[key] = [float(x) for x in np.quantile(values, [.02, .98])]
    return limits


def date_detail(cfg: dict[str, Any], day: str, raw_path: Path, output: Path,
                color_limits: dict[str, list[float]]) -> None:
    with h5py.File(raw_path) as h5:
        good = np.flatnonzero(h5["status"][:] == 1)
        if not len(good):
            return
        stamps = [datetime.fromisoformat(s) for s in h5["timestamps"].asstr()[:]]
        freq = h5["psd_frequency_hz"][:]
        fk_freq = h5["fk_frequency_hz"][:]
        wave = h5["fk_wavenumber_cycles_per_m"][:]
        start = day_boundary(stamps[0], cfg["time_start"])
        end = day_boundary(stamps[0], cfg["time_end"])
        step = float(cfg["tf_window_s"])
        nslots = int(np.ceil((end - start).total_seconds() / step))
        for j, position in enumerate(cfg["positions_m"]):
            tf = np.full((len(freq), nslots), np.nan, dtype=np.float32)
            for i in good:
                base = int(round((stamps[i] - start).total_seconds() / step))
                for k in range(int(h5["tf_count"][i])):
                    slot = base + k
                    if 0 <= slot < nslots:
                        tf[:, slot] = h5["tf_power"][i, j, k]
            psd = np.nanmedian(h5["psd_power"][good, j], axis=0)
            fk = np.nanmedian(h5["fk_power"][good, j], axis=0)
            rms = h5["rms"][good, j]
            ratio = h5["fk_ratio"][good, j]
            start_hour = start.hour + start.minute/60
            end_hour = (end-start).total_seconds()/3600 + start_hour
            hours = np.array([(stamps[i] - start).total_seconds()/3600 for i in good]) + start_hour
            fig, axes = plt.subplots(2, 2, figsize=(10, 6.5), constrained_layout=True)
            tf_db = 10*np.log10(np.maximum(tf, 1e-20))
            sparse = int(np.sum(h5["tf_count"][good])) / max(nslots, 1) < .2
            if sparse:
                # Show only measured windows at legible width; explicit labels avoid implying filled gaps.
                windows = [(i, k) for i in good for k in range(int(h5["tf_count"][i]))]
                display_tf = np.stack([10*np.log10(np.maximum(h5["tf_power"][i, j, k], 1e-20))
                                       for i, k in windows], axis=1)
                image = axes[0, 0].imshow(display_tf, origin="lower", aspect="auto", cmap="magma",
                                           vmin=color_limits.get("tf", [None, None])[0],
                                           vmax=color_limits.get("tf", [None, None])[1],
                                           extent=[0, len(windows), freq[0], freq[-1]])
                ticks = np.unique(np.linspace(0, len(windows)-1, min(6, len(windows))).round().astype(int))
                axes[0, 0].set_xticks(ticks + .5, [stamps[windows[t][0]].strftime("%H:%M") for t in ticks])
                axes[0, 0].set(xlabel="Measured windows (gaps omitted)", ylabel="Frequency (Hz)",
                               title="a  Time-frequency power")
            else:
                image = axes[0, 0].imshow(np.ma.masked_invalid(tf_db), origin="lower", aspect="auto", cmap="magma",
                                           vmin=color_limits.get("tf", [None, None])[0],
                                           vmax=color_limits.get("tf", [None, None])[1],
                                           extent=[start_hour, end_hour, freq[0], freq[-1]])
                axes[0, 0].set(xlabel="Clock time (h)", ylabel="Frequency (Hz)", title="a  Time-frequency power")
            fig.colorbar(image, ax=axes[0, 0], label="Power (dB, raw units²/Hz)")
            axes[0, 1].semilogy(freq, np.maximum(psd, 1e-20), color="#4477AA")
            axes[0, 1].axvspan(cfg["target_fmin_hz"], cfg["target_fmax_hz"], color="#4477AA", alpha=.1)
            axes[0, 1].set(xlabel="Frequency (Hz)", ylabel="PSD (raw units²/Hz)", title="b  Median minute spectrum")
            fk_db = 10*np.log10(np.maximum(fk, 1e-20))
            image = axes[1, 0].imshow(fk_db, origin="lower", aspect="auto", cmap="viridis",
                                       vmin=color_limits.get("fk", [None, None])[0],
                                       vmax=color_limits.get("fk", [None, None])[1],
                                       extent=[wave[0], wave[-1], fk_freq[0], fk_freq[-1]])
            axes[1, 0].set(xlabel="Wavenumber (cycles/m)", ylabel="Frequency (Hz)", title="c  Median local FK power")
            fig.colorbar(image, ax=axes[1, 0], label="FK power (dB)")
            axes[1, 1].plot(hours, rms, ".-", markersize=2, color="#CC6677", label="RMS")
            twin = axes[1, 1].twinx()
            twin.plot(hours, ratio, ".-", markersize=2, color="#4477AA", label="FK ratio")
            axes[1, 1].set(xlabel="Clock time (h)", ylabel="RMS (raw units)", title="d  Minute-by-minute quality")
            twin.set_ylabel("FK target/background ratio")
            fig.suptitle(f"{day} | requested {position:g} m | actual median {np.nanmedian(h5['actual_x_m'][good, j]):.1f} m | "
                         f"n={len(good)} minutes | TF step={step:g} s", fontsize=10)
            export(fig, output / day / f"raw_position_{position:g}m")


def monthly_position_details(cfg: dict[str, Any], manifest: dict[str, Any], output: Path) -> None:
    by_month: dict[str, list[Path]] = {}
    for day, entry in manifest["days"].items():
        path = entry.get("raw_cache")
        if path and Path(path).exists():
            by_month.setdefault(day[:7], []).append(Path(path))
    if not by_month:
        return
    for j, position in enumerate(cfg["positions_m"]):
        psd_by_month = {}
        fk_by_month = {}
        frequency = fk_frequency = wave = None
        for month, files in by_month.items():
            daily_psd, daily_fk = [], []
            for path in files:
                with h5py.File(path) as h5:
                    good = np.flatnonzero(h5["status"][:] == 1)
                    if not len(good):
                        continue
                    frequency = h5["psd_frequency_hz"][:]
                    fk_frequency = h5["fk_frequency_hz"][:]
                    wave = h5["fk_wavenumber_cycles_per_m"][:]
                    daily_psd.append(np.nanmedian(h5["psd_power"][good, j], axis=0))
                    daily_fk.append(np.nanmedian(h5["fk_power"][good, j], axis=0))
            if daily_psd:
                psd_by_month[month] = np.asarray(daily_psd)
                fk_by_month[month] = np.asarray(daily_fk)
        if not psd_by_month:
            continue
        fig, ax = plt.subplots(figsize=(6.2, 3.5), constrained_layout=True)
        colors = plt.cm.tab10(np.linspace(0, .8, len(psd_by_month)))
        for (month, spectra), color in zip(sorted(psd_by_month.items()), colors):
            median = np.nanmedian(spectra, axis=0)
            ax.semilogy(frequency, np.maximum(median, 1e-20), color=color, label=f"{month} (n={len(spectra)} days)")
            lo, hi = np.nanquantile(spectra, [.25, .75], axis=0)
            ax.fill_between(frequency, np.maximum(lo, 1e-20), np.maximum(hi, 1e-20), color=color, alpha=.12)
        ax.set(xlabel="Frequency (Hz)", ylabel="PSD (raw units²/Hz)",
               title=f"Position {position:g} m | monthly spectra (daily medians and IQR)")
        ax.legend()
        ax.grid(axis="y", color="0.9")
        export(fig, output / f"position_{position:g}m_monthly_psd")
        months = sorted(fk_by_month)
        columns = min(len(months), 2)
        fig, axes = plt.subplots(int(np.ceil(len(months)/columns)), columns, figsize=(8, 2.9*np.ceil(len(months)/columns)),
                                 constrained_layout=True, squeeze=False)
        images = [np.nanmedian(fk_by_month[month], axis=0) for month in months]
        levels = [10*np.log10(np.maximum(im, 1e-20)) for im in images]
        low = float(np.nanquantile(np.stack(levels), .05))
        high = float(np.nanquantile(np.stack(levels), .99))
        for ax, month, matrix, days in zip(axes.ravel(), months, levels, [len(fk_by_month[m]) for m in months]):
            ax.imshow(matrix, origin="lower", aspect="auto", cmap="viridis", vmin=low, vmax=high,
                      extent=[wave[0], wave[-1], fk_frequency[0], fk_frequency[-1]])
            ax.set(xlabel="Wavenumber (cycles/m)", ylabel="Frequency (Hz)", title=f"{month} | n={days} days")
        for ax in axes.ravel()[len(months):]:
            ax.axis("off")
        fig.suptitle(f"Position {position:g} m | monthly local FK power, shared color scale")
        export(fig, output / f"position_{position:g}m_monthly_fk")


def compare_raw_dates(cfg: dict[str, Any], manifest: dict[str, Any], output: Path,
                      color_limits: dict[str, list[float]]) -> None:
    days = [day for day in cfg.get("comparison_dates", []) if day in manifest["days"]]
    if len(days) != 2:
        return
    paths = [manifest["days"][day].get("raw_cache") for day in days]
    if any(not path or not Path(path).exists() for path in paths):
        return
    with h5py.File(paths[0]) as first, h5py.File(paths[1]) as second:
        files = (first, second)
        for position in cfg.get("comparison_positions_m", []):
            if float(position) not in [float(x) for x in cfg["positions_m"]]:
                continue
            prepared = []
            for day, h5 in zip(days, files):
                matches = np.flatnonzero(np.isclose(h5["requested_x_m"][:], float(position), atol=.01))
                if not len(matches):
                    break
                j = int(matches[0])
                good = np.flatnonzero(h5["status"][:] == 1)
                if not len(good):
                    break
                stamps = [datetime.fromisoformat(s) for s in h5["timestamps"].asstr()[:]]
                start = day_boundary(stamps[0], cfg["time_start"])
                end = day_boundary(stamps[0], cfg["time_end"])
                step = float(cfg["tf_window_s"])
                nslots = int(np.ceil((end-start).total_seconds()/step))
                tf = np.full((len(h5["psd_frequency_hz"]), nslots), np.nan, np.float32)
                for i in good:
                    base = int(round((stamps[i]-start).total_seconds()/step))
                    for k in range(int(h5["tf_count"][i])):
                        if 0 <= base+k < nslots:
                            tf[:, base+k] = h5["tf_power"][i, j, k]
                prepared.append((day, h5, good, tf, start, end, j))
            if len(prepared) != 2:
                continue
            if any(int(np.sum(h5["tf_count"][good])) / max(tf.shape[1], 1) < .2
                   for _, h5, good, tf, _, _, _ in prepared):
                continue
            psd = [np.nanmedian(h5["psd_power"][good, j], axis=0) for _, h5, good, *_, j in prepared]
            fk = [np.nanmedian(h5["fk_power"][good, j], axis=0) for _, h5, good, *_, j in prepared]
            lower = min(float(np.nanmin(p[p > 0])) for p in psd)
            upper = max(float(np.nanmax(p)) for p in psd)
            fig, axes = plt.subplots(3, 2, figsize=(9.4, 8.4), constrained_layout=True)
            for col, ((day, h5, good, tf, start, end, j), spectrum, fk_image) in enumerate(zip(prepared, psd, fk)):
                frequency = h5["psd_frequency_hz"][:]
                fk_freq = h5["fk_frequency_hz"][:]
                wave = h5["fk_wavenumber_cycles_per_m"][:]
                tf_db = 10*np.log10(np.maximum(tf, 1e-20))
                image = axes[0, col].imshow(np.ma.masked_invalid(tf_db), origin="lower", aspect="auto",
                                               cmap="magma", vmin=color_limits.get("tf", [None, None])[0],
                                               vmax=color_limits.get("tf", [None, None])[1],
                                               extent=[start.hour+start.minute/60,
                                                       start.hour+start.minute/60+(end-start).total_seconds()/3600,
                                                       frequency[0], frequency[-1]])
                axes[0, col].set(xlabel="Clock time (h)", ylabel="Frequency (Hz)",
                                 title=f"{day} | time-frequency | n={len(good)} min")
                axes[1, col].semilogy(frequency, np.maximum(spectrum, 1e-20), color="#4477AA")
                axes[1, col].set_ylim(lower*.8, upper*1.2)
                axes[1, col].set(xlabel="Frequency (Hz)", ylabel="PSD (raw units²/Hz)",
                                 title="Daily median power spectrum")
                axes[1, col].axvspan(cfg["target_fmin_hz"], cfg["target_fmax_hz"], color="#4477AA", alpha=.1)
                fk_db = 10*np.log10(np.maximum(fk_image, 1e-20))
                axes[2, col].imshow(fk_db, origin="lower", aspect="auto", cmap="viridis",
                                     vmin=color_limits.get("fk", [None, None])[0],
                                     vmax=color_limits.get("fk", [None, None])[1],
                                     extent=[wave[0], wave[-1], fk_freq[0], fk_freq[-1]])
                axes[2, col].set(xlabel="Wavenumber (cycles/m)", ylabel="Frequency (Hz)",
                                 title=f"Local FK | RMS={np.nanmedian(h5['rms'][good,j]):.3g} | "
                                       f"FK ratio={np.nanmedian(h5['fk_ratio'][good,j]):.2f}")
            fig.colorbar(image, ax=axes[0, :], shrink=.75, label="Power (dB)")
            fig.suptitle(f"Same-scale full-day raw comparison | requested {position:g} m | actual "
                         f"{np.nanmedian(first['actual_x_m'][:,prepared[0][-1]]):.1f}/"
                         f"{np.nanmedian(second['actual_x_m'][:,prepared[1][-1]]):.1f} m", fontsize=10)
            export(fig, output / f"raw_comparison_{days[0]}_{days[1]}_{float(position):g}m")


def compare_cc_dates(cfg: dict[str, Any], manifest: dict[str, Any], output: Path) -> None:
    days = [day for day in cfg.get("comparison_dates", []) if day in manifest["days"]]
    if len(days) != 2:
        return
    opened = []
    for day in days:
        path = manifest["days"][day].get("cc_cache")
        if not path or not Path(path).exists():
            return
        h5 = h5py.File(path)
        good = np.flatnonzero(h5["status"][:] == 1)
        if not len(good):
            h5.close()
            return
        chosen = int(good[len(good)//2])
        mat_path = Path(h5["files"].asstr()[chosen])
        mat = loadmat(mat_path, variable_names=["data", "dt", "dx", "shot_source_x_m"], squeeze_me=True)
        opened.append((day, h5, chosen, mat_path, mat))
    try:
        for position in cfg.get("comparison_positions_m", []):
            if float(position) not in [float(x) for x in cfg["positions_m"]]:
                continue
            j = [float(x) for x in cfg["positions_m"]].index(float(position))
            gathers = []
            images = []
            for _, h5, index, _, mat in opened:
                coords = np.asarray(mat["shot_source_x_m"], float).reshape(-1)
                shot = int(np.argmin(np.abs(coords - float(position))))
                gathers.append(np.asarray(mat["data"][shot], float))
                images.append(h5["dispersion_power"][index, j])
            cc_scale = max(float(np.nanquantile(np.abs(g), .99)) for g in gathers)
            disp_scale = max(float(np.nanquantile(im, .99)) for im in images)
            fig, axes = plt.subplots(3, 2, figsize=(8.6, 7.6), constrained_layout=True)
            for col, ((day, h5, index, mat_path, mat), gather, dispersion) in enumerate(zip(opened, gathers, images)):
                dt, dx = float(mat["dt"]), float(mat["dx"])
                lag = (np.arange(gather.shape[0]) - gather.shape[0]//2) * dt
                offset = (gather.shape[1] - 1) * dx
                axes[0, col].imshow(gather, origin="lower", aspect="auto", cmap="RdBu_r", interpolation="nearest",
                                    vmin=-cc_scale, vmax=cc_scale, extent=[0, offset, lag[0], lag[-1]])
                axes[0, col].set(xlabel="Offset (m)", ylabel="CC lag (s)",
                                 title=f"{day} | CC waveform | {mat_path.name}")
                frequency = h5["frequency_hz"][:]
                velocity = h5["velocity_mps"][:]
                for branch in range(2):
                    axes[branch+1, col].imshow(dispersion[branch], origin="lower", aspect="auto", cmap="viridis",
                                                  interpolation="nearest", vmin=0, vmax=disp_scale,
                                                  extent=[frequency[0], frequency[-1], velocity[0], velocity[-1]])
                    axes[branch+1, col].plot(frequency, 2*dx*frequency, "--", color="white", linewidth=.7,
                                              label="Spatial Nyquist")
                    axes[branch+1, col].plot(frequency, offset*frequency, ":", color="white", linewidth=.8,
                                              label="Aperture limit")
                    axes[branch+1, col].set(xlabel="Frequency (Hz)", ylabel="Velocity (m/s)",
                                             title=f"{BRANCHES[branch]} lag | coverage="
                                                   f"{h5['ridge_coverage'][index, j, branch]:.2f}")
                    axes[branch+1, col].set_ylim(velocity[0], velocity[-1])
            fig.suptitle(f"Same-scale spring/summer comparison | requested {position:g} m | "
                         f"actual {opened[0][1]['actual_x_m'][opened[0][2], j]:.1f}/"
                         f"{opened[1][1]['actual_x_m'][opened[1][2], j]:.1f} m", fontsize=10)
            export(fig, output / f"cc_comparison_{days[0]}_{days[1]}_{float(position):g}m")
    finally:
        for _, h5, _, _, _ in opened:
            h5.close()


def make_plots(cfg: dict[str, Any], manifest: dict[str, Any]) -> None:
    out = Path(cfg["output_dir"])
    figures = out / "figures"
    if figures.exists():
        shutil.rmtree(figures)
    monthly = rows_from(out / "monthly_summary.csv")
    daily = rows_from(out / "daily_summary.csv")
    if not monthly:
        print("[PLOT] No completed measurements to plot")
        return
    seasonal_pages(cfg, monthly, figures)
    daily_heatmaps(cfg, daily, figures)
    color_limits = shared_color_limits(cfg, manifest)
    (out / "plot_color_scales.json").write_text(json.dumps(color_limits, indent=2) + "\n", encoding="utf-8")
    if cfg.get("plot_daily_details", True):
        for day, entry in manifest["days"].items():
            path = entry.get("raw_cache")
            if path and Path(path).exists():
                print(f"[PLOT] Daily details {day}", flush=True)
                date_detail(cfg, day, Path(path), figures / "daily", color_limits)
    if cfg.get("plot_monthly_details", True):
        monthly_position_details(cfg, manifest, figures / "monthly")
    compare_raw_dates(cfg, manifest, figures / "comparison", color_limits)
    compare_cc_dates(cfg, manifest, figures / "comparison")
    print(f"[PLOT] Figures saved under {figures}", flush=True)
