#!/usr/bin/env python3
"""
Batch plot per-shot CC and pick_disper m3 energy panels from restacked CC MAT files.

Top panel: full-lag cross-correlation gather
Bottom panel: dispersion energy map (m3^4) loaded from a saved dispersion .mat file
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import loadmat

from dasQt.utils.imshow import my_cmap, setAxis


plt.rcParams.update(
    {
        "font.family": "Times New Roman",
        "font.size": 14,
    }
)


def centers_to_edges(values: np.ndarray) -> np.ndarray:
    centers = np.asarray(values, dtype=float).ravel()
    if centers.size == 0:
        return np.array([0.0, 1.0], dtype=float)
    if centers.size == 1:
        return np.array([centers[0] - 0.5, centers[0] + 0.5], dtype=float)
    mids = 0.5 * (centers[:-1] + centers[1:])
    left = centers[0] - 0.5 * (centers[1] - centers[0])
    right = centers[-1] + 0.5 * (centers[-1] - centers[-2])
    return np.concatenate(([left], mids, [right]))


def _mat_scalar(mat: dict[str, Any], key: str, default: float | None = None) -> float | None:
    if key not in mat:
        return default
    value = np.asarray(mat[key]).squeeze()
    if np.size(value) == 0:
        return default
    return float(np.asarray(value).reshape(-1)[0])


def load_restack_mat(path: Path) -> tuple[np.ndarray, float, float, float | None, float]:
    mat = loadmat(path)
    if "data" not in mat:
        raise ValueError(f"{path.name} missing key 'data'")
    data = np.asarray(mat["data"], dtype=float).squeeze()
    if data.ndim == 2:
        data = data[np.newaxis, :, :]
    if data.ndim != 3:
        raise ValueError(f"{path.name} invalid data shape: {data.shape}")

    dt = _mat_scalar(mat, "dt")
    dx = _mat_scalar(mat, "dx")
    dshot = _mat_scalar(mat, "dshot", default=None)
    xmin = _mat_scalar(mat, "xmin", default=0.0) or 0.0
    if dt is None or dx is None:
        raise ValueError(f"{path.name} missing dt/dx")
    return data, dt, dx, dshot, xmin


def load_dispersion_mat(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mat = loadmat(path, simplify_cells=True)
    if "m3_list" not in mat:
        raise ValueError(f"{path.name} missing key 'm3_list'")
    if "f" not in mat:
        raise ValueError(f"{path.name} missing key 'f'")

    m3 = np.asarray(mat["m3_list"], dtype=float)
    if m3.ndim != 3:
        raise ValueError(f"{path.name} invalid m3_list shape: {m3.shape}")

    f_axis = np.asarray(mat["f"], dtype=float).ravel()
    if f_axis.size != m3.shape[0]:
        raise ValueError(
            f"{path.name} frequency length {f_axis.size} does not match m3_list shape {m3.shape}"
        )

    vmin = _mat_scalar(mat, "vmin")
    dv = _mat_scalar(mat, "dv")
    if vmin is None or dv is None:
        raise ValueError(f"{path.name} missing vmin/dv")

    c_axis = float(vmin) + float(dv) * np.arange(m3.shape[1], dtype=float)
    return m3, f_axis, c_axis


def plot_shot_panel(
    shot_cc: np.ndarray,
    shot_idx: int,
    dx: float,
    dt: float,
    dshot: float | None,
    x_origin: float,
    m3: np.ndarray,
    f_axis: np.ndarray,
    c_axis: np.ndarray,
    out_file: Path,
    title_prefix: str,
) -> None:
    nt, nx = shot_cc.shape
    x_start = float(x_origin) + shot_idx * (float(dshot) if dshot is not None else float(dx))
    x_centers = x_start + np.arange(nx, dtype=float) * float(dx)
    t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)

    cc_vmax = float(np.percentile(np.abs(shot_cc), 99.0))
    if not np.isfinite(cc_vmax) or cc_vmax <= 0:
        cc_vmax = 1.0

    m3_power = np.asarray(m3, dtype=float) ** 4
    m3_vmax = float(np.percentile(m3_power, 99.5))
    if not np.isfinite(m3_vmax) or m3_vmax <= 0:
        m3_vmax = float(np.max(m3_power)) if np.max(m3_power) > 0 else 1.0

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(7.8, 8.4),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 1.15]},
    )

    ax_cc = axes[0]
    im_cc = ax_cc.pcolormesh(
        centers_to_edges(x_centers),
        centers_to_edges(t_centers),
        shot_cc,
        cmap="RdBu_r",
        shading="auto",
        vmin=-cc_vmax,
        vmax=cc_vmax,
        rasterized=False,
    )
    ax_cc.set_aspect("auto")
    ax_cc.set_title(f"{title_prefix} Shot {shot_idx:04d}: CC")
    ax_cc.set_xlabel("Distance (m)")
    ax_cc.set_ylabel("Time (s)")
    setAxis(ax_cc)
    fig.colorbar(im_cc, ax=ax_cc, fraction=0.035, pad=0.02, label="Amplitude")

    ax_m3 = axes[1]
    im_m3 = ax_m3.imshow(
        m3_power.T,
        extent=[float(f_axis[0]), float(f_axis[-1]), float(c_axis[0]), float(c_axis[-1])],
        aspect="auto",
        origin="lower",
        cmap=my_cmap,
        vmin=0.0,
        vmax=m3_vmax,
    )
    ax_m3.set_title(f"{title_prefix} Shot {shot_idx:04d}: m3")
    ax_m3.set_xlabel("Frequency (Hz)")
    ax_m3.set_ylabel("Phase velocity (m/s)")
    setAxis(ax_m3)
    fig.colorbar(im_m3, ax=ax_m3, fraction=0.035, pad=0.02, label="Normalized energy")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, dpi=180, bbox_inches="tight")
    plt.close(fig)


def process_restack_file(
    mat_file: Path,
    output_dir: Path,
    m3_all: np.ndarray,
    f_axis: np.ndarray,
    c_axis: np.ndarray,
    overwrite: bool,
    shot_start: int,
    shot_stop: int | None,
) -> int:
    data, dt, dx, dshot, xmin = load_restack_mat(mat_file)
    nshot = int(data.shape[0])
    if m3_all.shape[2] != nshot:
        print(
            f"[SKIP] {mat_file.name}: shot count {nshot} does not match "
            f"dispersion_data shots {m3_all.shape[2]}"
        )
        return 0
    start = max(0, int(shot_start))
    stop = nshot if shot_stop is None else min(nshot, int(shot_stop))
    if start >= stop:
        return 0
    count = 0
    for ishot in range(start, stop):
        out_file = output_dir / f"{mat_file.stem}_shot_{ishot:04d}_cc_m3.png"
        if out_file.exists() and not overwrite:
            continue
        shot_cc = np.asarray(data[ishot], dtype=float)
        plot_shot_panel(
            shot_cc=shot_cc,
            shot_idx=ishot,
            dx=dx,
            dt=dt,
            dshot=dshot,
            x_origin=xmin,
            m3=np.asarray(m3_all[:, :, ishot], dtype=float),
            f_axis=f_axis,
            c_axis=c_axis,
            out_file=out_file,
            title_prefix=mat_file.stem,
        )
        count += 1
        print(f"[OK] {out_file.name}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch plot CC + m3 shot figures from restack MAT files.")
    parser.add_argument(
        "--cc-mat",
        "--cc_mat",
        dest="cc_mat",
        type=Path,
        default=None,
        help="Single restack MAT file to plot",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "cc_restack_outputs",
        help="Directory containing restack_*.mat files",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="restack_*.mat",
        help="MAT filename pattern",
    )
    parser.add_argument(
        "--dispersion-mat",
        type=Path,
        default=Path(__file__).resolve().parent / "dispersion_data.mat",
        help="Saved dispersion MAT containing m3_list/f/vmin/dv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for combined shot figures",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing images",
    )
    parser.add_argument("--shot-start", type=int, default=0, help="Start shot index (inclusive)")
    parser.add_argument("--shot-stop", type=int, default=None, help="Stop shot index (exclusive)")
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser().resolve()
    dispersion_mat = args.dispersion_mat.expanduser().resolve()
    output_dir = (
        args.output_dir or (input_dir / f"cc_m3_shots_from_{dispersion_mat.stem}")
    ).expanduser().resolve()
    m3_all, f_axis, c_axis = load_dispersion_mat(dispersion_mat)

    if args.cc_mat is not None:
        cc_mat = args.cc_mat.expanduser().resolve()
        if not cc_mat.exists() or not cc_mat.is_file():
            raise FileNotFoundError(f"CC MAT not found: {cc_mat}")
        mat_files = [cc_mat]
    else:
        mat_files = sorted(
            [
                p for p in input_dir.glob(args.glob)
                if p.is_file() and p.name != "restack_preview.mat" and not p.name.startswith("disp-")
            ]
        )
    if not mat_files:
        raise FileNotFoundError(f"No restack mat files matched {input_dir / args.glob}")

    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for mat_file in mat_files:
        print(f"[INFO] processing {mat_file.name}")
        total += process_restack_file(
            mat_file,
            output_dir,
            m3_all,
            f_axis,
            c_axis,
            overwrite=bool(args.overwrite),
            shot_start=int(args.shot_start),
            shot_stop=args.shot_stop,
        )

    print(f"[DONE] generated {total} figures in {output_dir}")


if __name__ == "__main__":
    main()
