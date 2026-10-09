#!/usr/bin/env python3
"""
读取已保存的 CC .mat 文件并做二次叠加与绘图。

用途:
1) 从单个目录读取旧版 `data` MAT 或双侧 `data_left`/`data_right` MAT。
2) 按给定窗口执行二次叠加，输出 `restack_*.mat`。
3) 生成汇总预览图、逐 shot 图和按 shot 位置拼接图。
4) 支持显式指定叠加后端，便于手动选择 CPU 或 GPU。

输入文件格式兼容 save_stack_shots 的 MAT 结构：
- `data`: `(nshot, nt, nx)` 或 `(nt, nx)`
- 双侧文件使用 `data_left`、`data_right`；叠加后仍存于同一个 MAT 文件。
- `dt`: 标量
- `dx`: 标量
- `dshot`: 可选标量

运行示例:
1) CPU 叠加:
   `uv run python scripts/stack_saved_cc_mat.py \
      --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-27 \
      --glob "cc_stack_tsi_*.mat" \
      --stack-size None \
      --stack-method robust \
      --cc-backend cpu \
      --cc-device cpu`

2) 自动选择可用设备:
   `uv run python scripts/stack_saved_cc_mat.py \
      --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-27 \
      --glob "cc_stack_tsi_*.mat" \
      --stack-method pws \
      --cc-backend auto \
      --cc-device auto`

3) 强制 torch + CUDA:
   `uv run python scripts/stack_saved_cc_mat.py \
      --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-27 \
      --glob "cc_stack_tsi_*.mat" \
      --stack-method pws \
      --cc-backend torch \
      --cc-device cuda \
      --cc-dtype float32`

4) 只输出 `.mat`，不生成任何图片:
   `uv run python scripts/stack_saved_cc_mat.py \
      --input-dir ./outputs/cc_tsi_outputs_by_folder/2025-03-27 \
      --glob "cc_stack_tsi_*.mat" \
      --stack-method robust \
      --no-save-summary-plot \
      --no-save-shot-figures`
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
from scipy.io import loadmat, savemat, whosmat

from dasqt.utils.imshow import (
    configure_publication_style,
    figure_size,
    get_colormap,
    style_axis,
)

configure_publication_style()
for font_family in ("Times New Roman", "Times", "DejaVu Serif"):
    try:
        font_manager.findfont(
            font_manager.FontProperties(family=font_family),
            fallback_to_default=False,
        )
    except ValueError:
        continue
    plt.rcParams["font.family"] = [font_family]
    plt.rcParams["font.serif"] = [font_family]
    break

PROJECT_DIR = Path(__file__).resolve().parents[1]

from dasqt.features.dispersion.backend.components.ccf import stack_cc_chunks


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
    cc_list: list[np.ndarray],
    dt: float,
    stacking_method: str,
    *,
    cc_backend: str,
    cc_device: str,
    cc_dtype: str,
) -> np.ndarray:
    if stacking_method == "mean":
        return np.mean(np.asarray(cc_list), axis=0)

    data_array = np.asarray(cc_list)
    return stack_cc_chunks(
        data_array,
        dt=float(dt),
        smethod=str(stacking_method),
        params={
            "cc_backend": str(cc_backend),
            "cc_device": str(cc_device),
            "cc_dtype": str(cc_dtype),
        },
    )


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
    *,
    receiver_side: str | None = None,
) -> tuple[np.ndarray, float, float, float | None, dict[str, Any]]:
    mat = loadmat(mat_file)
    data_key = "data" if receiver_side is None else f"data_{receiver_side}"
    if data_key not in mat:
        raise ValueError(f"{mat_file.name} missing key: {data_key}")
    if "dt" not in mat or "dx" not in mat:
        raise ValueError(f"{mat_file.name} missing key: dt/dx")

    data = np.asarray(mat[data_key])
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
    if receiver_side is not None:
        extra_meta["receiver_side"] = receiver_side
        for key in (
            "shot_index", "shot_source_channel", "shot_source_x_m", "lag_axis_s",
            "receiver_order", "input_lag_mode", "format_version",
        ):
            if key in mat:
                extra_meta[key] = mat[key]
        for source_key, output_key in (
            (f"valid_mask_{receiver_side}", "valid_mask"),
            (f"offsets_{receiver_side}_m", "offsets_m"),
            (f"receiver_x_{receiver_side}_m", "receiver_x_m"),
            (f"status_{receiver_side}", "status"),
        ):
            if source_key in mat:
                extra_meta[output_key] = mat[source_key]
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


def save_branch_stack_mat(
    output_file: Path,
    stacks: dict[str, np.ndarray],
    metadata: dict[str, dict[str, Any]],
    *,
    dt: float,
    dx: float,
    dshot: float | None,
    source_files: list[Path],
) -> None:
    first_meta = next(iter(metadata.values()))
    payload: dict[str, Any] = {
        "format": "dasqt_receiver_branches",
        "receiver_side": "both" if len(stacks) == 2 else next(iter(stacks)),
        "dt": float(dt),
        "dx": float(dx),
        "source_files": np.array([p.name for p in source_files], dtype=object),
    }
    if dshot is not None:
        payload["dshot"] = float(dshot)
    side_fields = {"receiver_side", "valid_mask", "offsets_m", "receiver_x_m", "status"}
    for key, value in first_meta.items():
        if key not in side_fields and value is not None:
            payload[key] = value
    for side, data in stacks.items():
        payload[f"data_{side}"] = data
        side_meta = metadata[side]
        for key, output_key in (
            ("valid_mask", f"valid_mask_{side}"),
            ("offsets_m", f"offsets_{side}_m"),
            ("receiver_x_m", f"receiver_x_{side}_m"),
            ("status", f"status_{side}"),
        ):
            if key in side_meta:
                payload[output_key] = side_meta[key]
    output_file.parent.mkdir(parents=True, exist_ok=True)
    savemat(output_file, payload)


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


def stack_window_side(
    files: list[Path],
    side: str | None,
    *,
    stacking_method: str,
    cc_backend: str,
    cc_device: str,
    cc_dtype: str,
) -> tuple[np.ndarray, float, float, float | None, dict[str, Any]]:
    cc_list: list[np.ndarray] = []
    dt_ref: float | None = None
    dx_ref: float | None = None
    dshot_ref: float | None = None
    meta_ref: dict[str, Any] | None = None
    shape_ref: tuple[int, int, int] | None = None

    for path in files:
        cc, dt, dx, dshot, metadata = load_cc_mat(path, receiver_side=side)
        if dt_ref is None:
            dt_ref, dx_ref, dshot_ref = dt, dx, dshot
            meta_ref, shape_ref = metadata, cc.shape
        else:
            if cc.shape != shape_ref:
                raise ValueError(f"Shape mismatch: {path.name}={cc.shape}, ref={shape_ref}")
            if not np.isclose(dt, dt_ref) or not np.isclose(dx, dx_ref):
                raise ValueError(f"dt/dx mismatch: {path.name}")
            if (dshot is None) != (dshot_ref is None) or (
                dshot is not None and not np.isclose(dshot, dshot_ref)
            ):
                raise ValueError(f"dshot mismatch: {path.name}")
            if side is not None:
                for key in ("shot_index", "shot_source_x_m", "valid_mask", "receiver_x_m"):
                    if key not in meta_ref or key not in metadata:
                        raise ValueError(f"{path.name} missing branch geometry: {key}")
                    expected = np.asarray(meta_ref[key])
                    actual = np.asarray(metadata[key])
                    if expected.shape != actual.shape or not np.array_equal(
                        expected, actual, equal_nan=True
                    ):
                        raise ValueError(f"{key} mismatch: {path.name}")
        cc_list.append(cc)

    cc_array = np.asarray(cc_list)  # (nfile, nshot, nt, nx)
    _, nshot, nt, nx = cc_array.shape
    stacked = np.zeros((nshot, nt, nx), dtype=cc_array.dtype)
    for shot in range(nshot):
        stacked[shot] = stack_cc_results(
            [cc_array[file_index, shot] for file_index in range(cc_array.shape[0])],
            dt=float(dt_ref),
            stacking_method=stacking_method,
            cc_backend=cc_backend,
            cc_device=cc_device,
            cc_dtype=cc_dtype,
        )
    return stacked, float(dt_ref), float(dx_ref), dshot_ref, meta_ref


def combine_branches_by_position(
    stacks: dict[str, np.ndarray],
    metadata: dict[str, dict[str, Any]],
    dx: float,
    *,
    shot_index: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Average valid left/right traces on one physical receiver coordinate grid."""
    positions_list: list[np.ndarray] = []
    traces_list: list[np.ndarray] = []
    nt: int | None = None
    nshot: int | None = None
    for side, stack in stacks.items():
        data = np.asarray(stack)
        if data.ndim != 3:
            raise ValueError(f"data_{side} must have shape (nshot, nt, nx)")
        if nt is None:
            nshot, nt = data.shape[:2]
        elif data.shape[:2] != (nshot, nt):
            raise ValueError("Left and right shot/time dimensions differ")
        coords = np.asarray(metadata[side]["receiver_x_m"], dtype=float)
        mask = np.asarray(metadata[side]["valid_mask"], dtype=bool)
        if coords.shape != (data.shape[0], data.shape[2]) or mask.shape != coords.shape:
            raise ValueError(f"data_{side} receiver geometry shape mismatch")
        if shot_index is not None:
            selection = slice(shot_index, shot_index + 1)
            data, coords, mask = data[selection], coords[selection], mask[selection]
        keep = mask & np.isfinite(coords)
        if np.any(keep):
            positions_list.append(coords[keep])
            traces_list.append(np.moveaxis(data, 1, -1)[keep])
    if not positions_list:
        raise ValueError("No valid receiver traces to plot")
    positions = np.concatenate(positions_list)
    traces = np.concatenate(traces_list)
    origin = float(positions.min())
    indices = np.rint((positions - origin) / float(dx)).astype(int)
    acc = np.zeros((nt, int(indices.max()) + 1), dtype=np.float64)
    counts = np.zeros(indices.max() + 1, dtype=np.int64)
    for index, trace in zip(indices, traces):
        acc[:, index] += trace
        counts[index] += 1
    combined = np.zeros_like(acc, dtype=np.float32)
    occupied = counts > 0
    combined[:, occupied] = (acc[:, occupied] / counts[occupied]).astype(np.float32)
    return combined, origin + np.arange(combined.shape[1], dtype=float) * float(dx)


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
        nrows,
        ncols,
        figsize=figure_size("double", aspect=max(0.55, 0.55 * nrows)),
        squeeze=False,
    )
    axes_flat = axes.flatten()

    for i, item in enumerate(stacks):
        ax = axes_flat[i]
        source_x = None
        if "branches" in item:
            branches = item["branches"]
            first_side = next(iter(branches))
            shot_index = int(branches[first_side].shape[0] // 2)
            cc, x_centers = combine_branches_by_position(
                branches, item["metadata"], dx, shot_index=shot_index
            )
            nshot = int(branches[first_side].shape[0])
            source_x = float(np.asarray(
                item["metadata"][first_side]["shot_source_x_m"]
            ).ravel()[shot_index])
        else:
            cc_data = np.asarray(item["data"])
            shot_index = int(cc_data.shape[0] // 2) if item.get("receiver_x_m") is not None else 0
            cc = cc_data[shot_index] if cc_data.ndim == 3 else cc_data
            nt, nx = cc.shape
            receiver_x_m = item.get("receiver_x_m")
            if receiver_x_m is not None:
                x_centers = np.asarray(receiver_x_m)[shot_index].ravel()
                keep = np.isfinite(x_centers)
                if item.get("valid_mask") is not None:
                    keep &= np.asarray(item["valid_mask"])[shot_index].ravel().astype(bool)
                x_centers = x_centers[keep]
                cc = cc[:, keep]
            else:
                x_centers = np.arange(nx, dtype=float) * float(dx)
            nshot = int(cc_data.shape[0]) if cc_data.ndim == 3 else 1
        nt = cc.shape[0]
        t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)
        vmax = float(np.percentile(np.abs(cc), 99))
        if vmax == 0:
            vmax = 1.0

        im = ax.pcolormesh(
            centers_to_edges(x_centers),
            centers_to_edges(t_centers),
            cc,
            cmap=get_colormap("RdBu_r"),
            shading="auto",
            vmin=-vmax,
            vmax=vmax,
            rasterized=False,
        )
        ax.set_aspect("auto")
        if source_x is not None:
            ax.axvline(source_x, color="black", linewidth=0.6, linestyle="--")
        ax.set_title(
            f"Stack {i + 1}: idx[{item['start']}, {item['end']}], nshot={nshot} (show shot {shot_index})"
        )
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("Time (s)")
        style_axis(ax)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    for j in range(n, len(axes_flat)):
        axes_flat[j].axis("off")

    fig.tight_layout()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, bbox_inches="tight", dpi=600)
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
    x_centers: np.ndarray | None = None,
    source_x: float | None = None,
) -> None:
    nt, nx = cc.shape
    if x_centers is None:
        x_centers = float(x_start) + np.arange(nx, dtype=float) * float(dx)
    else:
        x_centers = np.asarray(x_centers, dtype=float).ravel()
        if x_centers.size != nx:
            raise ValueError("x_centers length must match CC receiver axis")
    t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)
    vmax = float(np.percentile(np.abs(cc), 99))
    if vmax == 0:
        vmax = 1.0

    fig, ax = plt.subplots(figsize=figure_size("double", aspect=0.45))
    im = ax.pcolormesh(
        centers_to_edges(x_centers),
        centers_to_edges(t_centers),
        cc,
        cmap=get_colormap("RdBu_r"),
        shading="auto",
        vmin=-vmax,
        vmax=vmax,
        rasterized=False,
    )
    ax.set_aspect("auto")
    if source_x is not None:
        ax.axvline(source_x, color="black", linewidth=0.6, linestyle="--")
    ax.set_title(title)
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Time (s)")
    style_axis(ax)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, bbox_inches="tight", dpi=600)
    plt.close(fig)


def save_branch_shot_figures(
    stacks: dict[str, np.ndarray],
    metadata: dict[str, dict[str, Any]],
    *,
    dt: float,
    dx: float,
    shots_dir: Path,
    prefix: str,
) -> None:
    first_side = next(iter(stacks))
    nshot = int(stacks[first_side].shape[0])
    source_x = np.asarray(metadata[first_side]["shot_source_x_m"]).ravel()
    for shot in range(nshot):
        try:
            cc, x_centers = combine_branches_by_position(
                stacks, metadata, dx, shot_index=shot
            )
        except ValueError as exc:
            if str(exc) == "No valid receiver traces to plot":
                continue
            raise
        _plot_cc_image(
            cc, dt, dx, shots_dir / f"{prefix}_shot_{shot:04d}.png",
            title=f"{prefix} | shot {shot} | {' + '.join(stacks)}",
            x_centers=x_centers,
            source_x=float(source_x[shot]),
        )


def save_merged_branch_figure(
    stacks: dict[str, np.ndarray],
    metadata: dict[str, dict[str, Any]],
    *,
    dt: float,
    dx: float,
    out_file: Path,
    title: str,
) -> None:
    merged, x_centers = combine_branches_by_position(stacks, metadata, dx)
    _plot_cc_image(merged, dt, dx, out_file, title=title, x_centers=x_centers)


def save_all_shot_figures(
    stack_data: np.ndarray,
    dt: float,
    dx: float,
    shots_dir: Path,
    *,
    prefix: str,
    x_origin: float = 0.0,
    dshot: float | None = None,
    receiver_x_m: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
) -> None:
    nshot = int(stack_data.shape[0])
    dshot_use = float(dshot) if dshot is not None else float(dx)
    for ishot in range(nshot):
        x_start = float(x_origin) + ishot * dshot_use
        out_file = shots_dir / f"{prefix}_shot_{ishot:04d}.png"
        cc = np.asarray(stack_data[ishot])
        shot_x = None
        if receiver_x_m is not None:
            shot_x = np.asarray(receiver_x_m)[ishot].ravel()
            keep = np.isfinite(shot_x)
            if valid_mask is not None:
                keep &= np.asarray(valid_mask)[ishot].ravel().astype(bool)
            if not np.any(keep):
                continue
            cc = cc[:, keep]
            shot_x = shot_x[keep]
        _plot_cc_image(
            cc,
            dt,
            dx,
            out_file,
            title=f"{prefix} | shot {ishot}",
            x_start=x_start,
            x_centers=shot_x,
        )


def merge_shots_by_position(
    stack_data: np.ndarray,
    dx: float,
    dshot: float | None,
    *,
    receiver_x_m: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """Place shots on a distance grid and average overlapping valid traces."""
    nshot, nt, nx = stack_data.shape
    if receiver_x_m is not None:
        positions = np.asarray(receiver_x_m, dtype=float)
        if positions.shape != (nshot, nx):
            raise ValueError("receiver_x_m shape must match shot and receiver axes")
        keep = np.isfinite(positions)
        if valid_mask is not None:
            keep &= np.asarray(valid_mask, dtype=bool)
        if not np.any(keep):
            raise ValueError("No valid receiver positions to merge")
        origin = float(np.min(positions[keep]))
        indices = np.rint((positions[keep] - origin) / float(dx)).astype(int)
        acc = np.zeros((nt, int(indices.max()) + 1), dtype=np.float64)
        wgt = np.zeros(indices.max() + 1, dtype=np.int64)
        for trace, index in zip(np.moveaxis(stack_data, 1, -1)[keep], indices):
            acc[:, index] += trace
            wgt[index] += 1
        merged = np.zeros_like(acc, dtype=np.float32)
        nonzero = wgt > 0
        merged[:, nonzero] = (acc[:, nonzero] / wgt[nonzero]).astype(np.float32)
        return merged, 0
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
    receiver_x_m: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    title: str = "Merged CC by shot positions",
) -> None:
    merged, shot_step = merge_shots_by_position(
        stack_data, dx=dx, dshot=dshot,
        receiver_x_m=receiver_x_m, valid_mask=valid_mask,
    )
    if receiver_x_m is not None:
        positions = np.asarray(receiver_x_m, dtype=float)
        keep = np.isfinite(positions)
        if valid_mask is not None:
            keep &= np.asarray(valid_mask, dtype=bool)
        _plot_cc_image(
            merged, dt, dx, out_file, title=title,
            x_start=float(np.min(positions[keep])),
        )
        return
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
        default=PROJECT_DIR / "outputs/cc_outputs",
        help="保存后 .mat 文件所在目录。",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="cc_stack_group_*.mat",
        help="输入文件匹配模式。",
    )
    parser.add_argument(
        "--receiver-side",
        choices=("auto", "left", "right", "both"),
        default="auto",
        help="双侧 MAT 默认将左右支叠加后保存到同一个 MAT；可只选择一侧。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "outputs/cc_restack_outputs",
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
    parser.add_argument(
        "--cc-backend",
        type=str,
        default="auto",
        choices=["auto", "cpu", "torch"],
        help="叠加后端。`cpu` 强制 CPU，`torch` 强制 torch，`auto` 自动选择。",
    )
    parser.add_argument(
        "--cc-device",
        type=str,
        default="auto",
        choices=["auto", "cpu", "mps", "cuda"],
        help="叠加设备。`cpu` 强制 CPU，`mps/cuda` 尝试 GPU，`auto` 自动选择。",
    )
    parser.add_argument(
        "--cc-dtype",
        type=str,
        default="float32",
        choices=["float32", "float64"],
        help="叠加计算 dtype。",
    )
    parser.add_argument(
        "--save-summary-plot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否保存汇总预览图。默认开启。",
    )
    parser.add_argument(
        "--save-shot-figures",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否保存逐 shot 图片和 merged-by-position 图片。默认开启。",
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
    mat_keys = {name for name, _, _ in whosmat(files[0])}
    branch_sides = [side for side in ("left", "right") if f"data_{side}" in mat_keys]
    if "data" in mat_keys:
        if args.receiver_side != "auto":
            raise ValueError("--receiver-side requires a branch-format MAT input")
        sides: list[str | None] = [None]
    elif branch_sides:
        if args.receiver_side in ("auto", "both"):
            sides = branch_sides
            if args.receiver_side == "both" and len(sides) != 2:
                raise ValueError("--receiver-side both requires data_left and data_right")
        elif args.receiver_side in branch_sides:
            sides = [args.receiver_side]
        else:
            raise ValueError(f"{files[0].name} has no data_{args.receiver_side}")
    else:
        raise ValueError(f"{files[0].name} has no data, data_left, or data_right")

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
    print(f"[INFO] cc backend  : {args.cc_backend}")
    print(f"[INFO] cc device   : {args.cc_device}")
    print(f"[INFO] cc dtype    : {args.cc_dtype}")
    print(f"[INFO] receiver side: {', '.join(side or 'legacy' for side in sides)}")
    print(f"[INFO] summary plot: {bool(args.save_summary_plot)}")
    print(f"[INFO] shot figures: {bool(args.save_shot_figures)}")

    saved_items: list[dict[str, Any]] = []
    for i, (w_start, w_end, w_files) in enumerate(windows, start=1):
        stacks: dict[str, np.ndarray] = {}
        metadata: dict[str, dict[str, Any]] = {}
        dt_ref: float | None = None
        dx_ref: float | None = None
        dshot_ref: float | None = None
        for side in sides:
            data, dt, dx, dshot, side_meta = stack_window_side(
                w_files, side,
                stacking_method=args.stack_method,
                cc_backend=args.cc_backend,
                cc_device=args.cc_device,
                cc_dtype=args.cc_dtype,
            )
            if dt_ref is None:
                dt_ref, dx_ref, dshot_ref = dt, dx, dshot
            elif not np.isclose(dt, dt_ref) or not np.isclose(dx, dx_ref):
                raise ValueError(f"Left/right dt or dx mismatch in window {i}")
            elif (dshot is None) != (dshot_ref is None) or (
                dshot is not None and not np.isclose(dshot, dshot_ref)
            ):
                raise ValueError(f"Left/right dshot mismatch in window {i}")
            key = side or "legacy"
            stacks[key] = data
            metadata[key] = side_meta

        if len(sides) == 2:
            left_meta, right_meta = metadata["left"], metadata["right"]
            for key in ("shot_index", "shot_source_x_m"):
                if not np.array_equal(left_meta[key], right_meta[key], equal_nan=True):
                    raise ValueError(f"Left/right {key} mismatch in window {i}")

        stack_size_label = "all" if stack_size is None else str(stack_size)
        out_mat = output_dir / (
            f"restack_{i:04d}_n{stack_size_label}_idx{w_start:04d}-{w_end:04d}.mat"
        )
        item: dict[str, Any] = {"start": w_start, "end": w_end, "mat": out_mat}
        if sides == [None]:
            save_stack_shots_mat(
                out_mat, stacks["legacy"], dt=float(dt_ref), dx=float(dx_ref),
                dshot=dshot_ref, source_files=w_files, extra_meta=metadata["legacy"],
            )
            item["data"] = stacks["legacy"]
        else:
            save_branch_stack_mat(
                out_mat, stacks, metadata, dt=float(dt_ref), dx=float(dx_ref),
                dshot=dshot_ref, source_files=w_files,
            )
            item["branches"] = stacks
            item["metadata"] = metadata
        saved_items.append(item)
        print(f"[OK] saved: {out_mat.name}")

        if args.save_shot_figures:
            shots_dir = output_dir / "shots_fig" / out_mat.stem
            merged_png = shots_dir / f"{out_mat.stem}_merged_by_position.png"
            if sides == [None]:
                legacy_meta = metadata["legacy"]
                x_origin = float(legacy_meta.get("xmin") or 0.0)
                save_all_shot_figures(
                    stacks["legacy"], dt=float(dt_ref), dx=float(dx_ref),
                    shots_dir=shots_dir, prefix=out_mat.stem, x_origin=x_origin,
                    dshot=dshot_ref,
                )
                save_merged_shot_figure(
                    stacks["legacy"], dt=float(dt_ref), dx=float(dx_ref),
                    out_file=merged_png, x_origin=x_origin, dshot=dshot_ref,
                    title=f"{out_mat.stem} merged CC by shot positions",
                )
            else:
                save_branch_shot_figures(
                    stacks, metadata, dt=float(dt_ref), dx=float(dx_ref),
                    shots_dir=shots_dir, prefix=out_mat.stem,
                )
                save_merged_branch_figure(
                    stacks, metadata, dt=float(dt_ref), dx=float(dx_ref),
                    out_file=merged_png,
                    title=f"{out_mat.stem} merged CC by receiver positions",
                )
            print(f"[OK] shots figs: {shots_dir}")

    if args.save_summary_plot:
        plot_file = output_dir / args.plot_name
        plot_stacks(
            saved_items, dt=float(dt_ref), dx=float(dx_ref),
            out_file=plot_file, show=bool(args.show),
        )
        print(f"[OK] plot : {plot_file}")


if __name__ == "__main__":
    main()
