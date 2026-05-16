#!/usr/bin/env python3
"""
读取已保存的 CC .mat 文件并做二次叠加与绘图。

输入文件格式兼容 save_stack_shots 的 MAT 结构：
- data: (nshot, nt, nx) 或 (nt, nx)
- dt: 标量
- dx: 标量
- dshot: 可选标量
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import loadmat, savemat

plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["font.sans-serif"] = ["Times New Roman"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dasQt.dispersion import DAS_module


def parse_optional_int(value: str | None, *, name: str = "value") -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return None
    out = int(text)
    if out <= 0:
        raise ValueError(f"{name} must be > 0 or None")
    return out


def normalize_data(data: np.ndarray) -> np.ndarray:
    max_values = np.max(np.abs(data), axis=0)
    max_values = np.where(max_values == 0, 1.0, max_values)
    return data / max_values[np.newaxis, :]


def centers_to_edges(values: np.ndarray) -> np.ndarray:
    centers = np.asarray(values, dtype=float).ravel()
    if centers.size == 0:
        return np.array([0.0, 1.0], dtype=float)
    if centers.size == 1:
        step = 1.0
        return np.array([centers[0] - 0.5 * step, centers[0] + 0.5 * step], dtype=float)
    mids = 0.5 * (centers[:-1] + centers[1:])
    left = centers[0] - 0.5 * (centers[1] - centers[0])
    right = centers[-1] + 0.5 * (centers[-1] - centers[-2])
    return np.concatenate(([left], mids, [right]))


def stack_cc_results(
    cc_list: list[np.ndarray], dt: float, stacking_method: str
) -> np.ndarray:
    if stacking_method == "mean":
        return np.mean(np.asarray(cc_list), axis=0)

    data_array = np.asarray(cc_list)
    n, nt, nx = data_array.shape
    data_array = data_array.reshape(n, -1)
    stack_par = {
        "samp_freq": 1.0 / dt,
        "npts_chunk": nt,
        "nsta": nx,
        "stack_method": stacking_method,
    }
    stacked_data = DAS_module.stacking(data_array, stack_par)
    stacked_data = normalize_data(stacked_data)
    return stacked_data - np.mean(stacked_data, axis=0)


def _to_mat_scalar(mat: dict[str, Any], key: str) -> Any:
    if key not in mat:
        return None
    value = np.squeeze(mat[key])
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return None
        value = value.reshape(-1)[0]
    if isinstance(value, (np.floating, np.integer)):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        return value
    try:
        return float(value)
    except Exception:
        return value


def load_cc_mat(
    mat_file: Path,
) -> tuple[np.ndarray, float, float, float | None, dict[str, Any]]:
    mat = loadmat(mat_file)
    if "data" not in mat:
        raise ValueError(f"{mat_file.name} missing key: data")
    if "dt" not in mat or "dx" not in mat:
        raise ValueError(f"{mat_file.name} missing key: dt/dx")

    data = np.asarray(mat["data"])
    data = np.squeeze(data)
    if data.ndim == 2:
        cc = data[None, :, :]
    elif data.ndim == 3:
        cc = data
    else:
        raise ValueError(f"{mat_file.name} invalid data shape: {data.shape}")

    dt = float(np.squeeze(mat["dt"]))
    dx = float(np.squeeze(mat["dx"]))
    dshot = float(np.squeeze(mat["dshot"])) if "dshot" in mat else None
    extra_meta = {
        "xmin": _to_mat_scalar(mat, "xmin"),
        "xmax": _to_mat_scalar(mat, "xmax"),
        "offset": _to_mat_scalar(mat, "offset"),
        "freqmin": _to_mat_scalar(mat, "freqmin"),
        "freqmax": _to_mat_scalar(mat, "freqmax"),
        "freq_norm": _to_mat_scalar(mat, "freq_norm"),
        "time_norm": _to_mat_scalar(mat, "time_norm"),
        "cc_method": _to_mat_scalar(mat, "cc_method"),
    }
    return np.asarray(cc, dtype=np.float32), dt, dx, dshot, extra_meta


def save_stack_shots_mat(
    output_file: Path,
    stack_data: np.ndarray,
    dt: float,
    dx: float,
    dshot: float | None,
    source_files: list[Path],
    extra_meta: dict[str, Any] | None = None,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    data_arr = np.asarray(stack_data)
    if data_arr.ndim == 2:
        data_arr = data_arr[None, :, :]

    mat_dict: dict[str, Any] = {
        "data": data_arr,
        "dt": float(dt),
        "dx": float(dx),
        "source_files": np.array([p.name for p in source_files], dtype=object),
    }
    if dshot is not None:
        mat_dict["dshot"] = float(dshot)
    if extra_meta:
        for key, value in extra_meta.items():
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


def build_windows(
    files: list[Path], stack_size: int | None, interval: int | None
) -> list[tuple[int, int, list[Path]]]:
    if stack_size is None:
        stack_size = len(files)
    if stack_size <= 0:
        raise ValueError("stack_size must be > 0 or None")
    if len(files) < stack_size:
        raise ValueError(
            f"Not enough files: selected={len(files)}, stack_size={stack_size}"
        )

    if interval is None:
        return [(0, stack_size - 1, files[:stack_size])]

    windows: list[tuple[int, int, list[Path]]] = []
    start = 0
    while start + stack_size <= len(files):
        end = start + stack_size - 1
        windows.append((start, end, files[start : end + 1]))
        start += interval
    return windows


def plot_stacks(
    stacks: list[dict[str, Any]],
    dt: float,
    dx: float,
    out_file: Path,
    show: bool,
) -> None:
    n = len(stacks)
    ncols = min(3, n)
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(6 * ncols, 4 * nrows), squeeze=False
    )
    axes_flat = axes.flatten()

    for i, item in enumerate(stacks):
        ax = axes_flat[i]
        cc_data = np.asarray(item["data"])
        cc = cc_data[0] if cc_data.ndim == 3 else cc_data
        nt, nx = cc.shape
        x_centers = np.arange(nx, dtype=float) * float(dx)
        t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)
        vmax = float(np.percentile(np.abs(cc), 99))
        if vmax == 0:
            vmax = 1.0

        im = ax.pcolormesh(
            centers_to_edges(x_centers),
            centers_to_edges(t_centers),
            cc,
            cmap="RdBu_r",
            shading="auto",
            vmin=-vmax,
            vmax=vmax,
            rasterized=False,
        )
        ax.set_aspect("auto")
        nshot = int(cc_data.shape[0]) if cc_data.ndim == 3 else 1
        ax.set_title(
            f"Stack {i + 1}: idx[{item['start']}, {item['end']}], nshot={nshot} (show shot 0)"
        )
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("Time (s)")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    for j in range(n, len(axes_flat)):
        axes_flat[j].axis("off")

    fig.tight_layout()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


def _plot_cc_image(
    cc: np.ndarray,
    dt: float,
    dx: float,
    out_file: Path,
    *,
    title: str,
    x_start: float = 0.0,
) -> None:
    nt, nx = cc.shape
    x_centers = float(x_start) + np.arange(nx, dtype=float) * float(dx)
    t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)
    vmax = float(np.percentile(np.abs(cc), 99))
    if vmax == 0:
        vmax = 1.0

    fig, ax = plt.subplots(figsize=(9, 4))
    im = ax.pcolormesh(
        centers_to_edges(x_centers),
        centers_to_edges(t_centers),
        cc,
        cmap="RdBu_r",
        shading="auto",
        vmin=-vmax,
        vmax=vmax,
        rasterized=False,
    )
    ax.set_aspect("auto")
    ax.set_title(title)
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Time (s)")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, bbox_inches="tight")
    plt.close(fig)


def save_all_shot_figures(
    stack_data: np.ndarray,
    dt: float,
    dx: float,
    shots_dir: Path,
    *,
    prefix: str,
    x_origin: float = 0.0,
    dshot: float | None = None,
) -> None:
    nshot = int(stack_data.shape[0])
    dshot_use = float(dshot) if dshot is not None else float(dx)
    for ishot in range(nshot):
        x_start = float(x_origin) + ishot * dshot_use
        out_file = shots_dir / f"{prefix}_shot_{ishot:04d}.png"
        _plot_cc_image(
            np.asarray(stack_data[ishot]),
            dt,
            dx,
            out_file,
            title=f"{prefix} | shot {ishot}",
            x_start=x_start,
        )


def merge_shots_by_position(
    stack_data: np.ndarray,
    dx: float,
    dshot: float | None,
) -> tuple[np.ndarray, int]:
    """
    Place each shot by physical shot spacing and average overlaps.
    Returns merged_cc (nt, nx_merged) and shot_step_samples.
    """
    nshot, nt, nx = stack_data.shape
    dshot_use = float(dshot) if dshot is not None else float(dx)
    shot_step = max(1, int(round(dshot_use / float(dx))))

    nx_merged = shot_step * (nshot - 1) + nx
    acc = np.zeros((nt, nx_merged), dtype=np.float64)
    wgt = np.zeros((nt, nx_merged), dtype=np.float64)
    for ishot in range(nshot):
        i0 = ishot * shot_step
        i1 = i0 + nx
        acc[:, i0:i1] += stack_data[ishot]
        wgt[:, i0:i1] += 1.0

    merged = np.zeros_like(acc, dtype=np.float32)
    mask = wgt > 0
    merged[mask] = (acc[mask] / wgt[mask]).astype(np.float32)
    return merged, shot_step


def save_merged_shot_figure(
    stack_data: np.ndarray,
    dt: float,
    dx: float,
    out_file: Path,
    *,
    x_origin: float = 0.0,
    dshot: float | None = None,
    title: str = "Merged CC by shot positions",
) -> None:
    merged, shot_step = merge_shots_by_position(stack_data, dx=dx, dshot=dshot)
    dshot_use = float(dshot) if dshot is not None else float(dx)
    effective_dshot = shot_step * float(dx)
    full_title = f"{title} (dshot={dshot_use:.6g}m, effective={effective_dshot:.6g}m)"
    _plot_cc_image(
        merged,
        dt,
        dx,
        out_file,
        title=full_title,
        x_start=x_origin,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Restack saved CC MAT files and plot.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "cc_outputs",
        help="保存后 .mat 文件所在目录。",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="cc_stack_group_*.mat",
        help="输入文件匹配模式。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "cc_restack_outputs",
        help="叠加结果输出目录。",
    )
    parser.add_argument(
        "--stack-size",
        type=str,
        default="5",
        help="每次叠加使用的文件数；传 None 表示使用全部选中文件。",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="None",
        help="叠加窗口间隔（文件数）；传 None 表示关闭该功能，仅叠加第一组。",
    )
    parser.add_argument(
        "--start-index", type=int, default=0, help="起始文件索引（包含）。"
    )
    parser.add_argument(
        "--stack-method",
        type=str,
        default="pws",
        choices=["pws", "robust", "linear", "mean"],
        help="叠加方法。",
    )
    parser.add_argument("--show", action="store_true", help="保存图后同时弹窗显示。")
    parser.add_argument(
        "--plot-name", type=str, default="restack_preview.png", help="汇总图文件名。"
    )
    args = parser.parse_args()

    interval = parse_optional_int(args.interval, name="interval")
    stack_size = parse_optional_int(args.stack_size, name="stack-size")
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input dir not found: {input_dir}")

    files = sorted([p for p in input_dir.glob(args.glob) if p.is_file()])
    if not files:
        raise FileNotFoundError(f"No files matched: {input_dir}/{args.glob}")

    total = len(files)
    start = int(args.start_index)
    if start < 0:
        raise ValueError("start-index must be >= 0")
    if start >= total:
        raise ValueError(f"start-index out of range: total={total}, start={start}")

    end = total - 1
    selected = files[start:]
    windows = build_windows(selected, stack_size=stack_size, interval=interval)

    print(f"[INFO] input dir   : {input_dir}")
    print(f"[INFO] glob        : {args.glob}")
    print(f"[INFO] total files : {total}")
    print(f"[INFO] selected    : {len(selected)} (idx {start}..{end})")
    print(f"[INFO] stack size  : {stack_size if stack_size is not None else 'ALL'}")
    print(f"[INFO] interval    : {args.interval}")
    print(f"[INFO] windows     : {len(windows)}")

    saved_items: list[dict[str, Any]] = []
    for i, (w_start, w_end, w_files) in enumerate(windows, start=1):
        cc_list: list[np.ndarray] = []
        dt_ref: float | None = None
        dx_ref: float | None = None
        dshot_ref: float | None = None
        meta_ref: dict[str, Any] | None = None
        shape_ref: tuple[int, int, int] | None = None

        for f in w_files:
            cc, dt, dx, dshot, extra_meta = load_cc_mat(f)
            if dt_ref is None:
                dt_ref = dt
                dx_ref = dx
                dshot_ref = dshot
                meta_ref = extra_meta
                shape_ref = cc.shape
            else:
                if cc.shape != shape_ref:
                    raise ValueError(
                        f"Shape mismatch in window {i}: {f.name}={cc.shape}, ref={shape_ref}"
                    )
                if not np.isclose(dt, dt_ref):
                    raise ValueError(
                        f"dt mismatch in window {i}: {f.name}={dt}, ref={dt_ref}"
                    )
                if not np.isclose(dx, dx_ref):
                    raise ValueError(
                        f"dx mismatch in window {i}: {f.name}={dx}, ref={dx_ref}"
                    )
            cc_list.append(cc)

        # Stack every shot independently across files.
        cc_array = np.asarray(cc_list)  # (nfile, nshot, nt, nx)
        _, nshot, nt, nx = cc_array.shape
        stack_data = np.zeros((nshot, nt, nx), dtype=cc_array.dtype)
        for ishot in range(nshot):
            stack_data[ishot, :, :] = stack_cc_results(
                [cc_array[k, ishot, :, :] for k in range(cc_array.shape[0])],
                dt=float(dt_ref),
                stacking_method=args.stack_method,
            )

        stack_size_label = "all" if stack_size is None else str(stack_size)
        out_mat = (
            output_dir
            / f"restack_{i:04d}_n{stack_size_label}_idx{w_start:04d}-{w_end:04d}.mat"
        )
        save_stack_shots_mat(
            out_mat,
            stack_data,
            dt=float(dt_ref),
            dx=float(dx_ref),
            dshot=dshot_ref,
            source_files=w_files,
            extra_meta=meta_ref,
        )
        saved_items.append(
            {"data": stack_data, "start": w_start, "end": w_end, "mat": out_mat}
        )
        print(f"[OK] saved: {out_mat.name}")

        # Save shot-wise figures and merged-by-position figure
        shots_root = output_dir / "shots_fig"
        shots_dir = shots_root / out_mat.stem
        x_origin = float(meta_ref.get("xmin", 0.0)) if meta_ref else 0.0
        save_all_shot_figures(
            stack_data,
            dt=float(dt_ref),
            dx=float(dx_ref),
            shots_dir=shots_dir,
            prefix=out_mat.stem,
            x_origin=x_origin,
            dshot=dshot_ref,
        )
        merged_png = shots_dir / f"{out_mat.stem}_merged_by_position.png"
        save_merged_shot_figure(
            stack_data,
            dt=float(dt_ref),
            dx=float(dx_ref),
            out_file=merged_png,
            x_origin=x_origin,
            dshot=dshot_ref,
            title=f"{out_mat.stem} merged CC by shot positions",
        )
        print(f"[OK] shots figs: {shots_dir}")

    plot_file = output_dir / args.plot_name
    plot_stacks(
        saved_items,
        dt=float(dt_ref),
        dx=float(dx_ref),
        out_file=plot_file,
        show=bool(args.show),
    )
    print(f"[OK] plot : {plot_file}")


if __name__ == "__main__":
    main()
