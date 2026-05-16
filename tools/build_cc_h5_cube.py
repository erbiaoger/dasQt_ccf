#!/usr/bin/env python3
"""
将按日期目录组织的 CC `.mat` 文件整理为一个磁盘后端 HDF5 4D 立方体。

用途:
1) 递归扫描输入根目录下所有包含 `.mat` 文件的子目录。
2) 每个子目录只选一个 `.mat` 文件，作为一个 date 样本。
3) 先用 `scipy.io.whosmat()` 做轻量元数据扫描，避免一开始把所有文件都读进内存。
4) 严格校验所有选中文件的 `data` shape 一致，目标 shape 为 `(ndate, nshot, nt, noffset)`。
5) 每次只读取一个 `.mat` 文件，再写入 HDF5 的一个 date slice，峰值内存约等于单个 `.mat` 文件大小。

适用场景:
- 输入目录类似 `outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27/*.mat`
- 后续主要想做固定某一炮、跨日期读取，例如 `[:, ishot, :, :]`

运行方式:
1) ChangBai 默认示例:
   `uv run python tools/build_cc_h5_cube.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
      --meta-csv ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_index.csv`

2) 覆盖已有输出，并使用单炮友好的 chunk 布局:
   `uv run python tools/build_cc_h5_cube.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --output-h5 ./outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5 \
      --overwrite \
      --chunk-mode single_shot_across_dates`

3) 仅预览会选择哪些目录和文件，不实际写输出:
   `uv run python tools/build_cc_h5_cube.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --dry-run`

输入:
- 一个目录树；每个有效子目录至少有一个匹配 `--glob` 的 `.mat` 文件。
- 每个 `.mat` 文件需包含 `data` 变量，shape 为 `(nshot, nt, noffset)` 或 `(nt, noffset)`。

输出:
- 一个 HDF5 文件:
  - `/cc`: `(ndate, nshot, nt, noffset)`，`float32`
  - `/date_labels`
  - `/source_paths`
  - `/dt`, `/dx`, `/dshot`
- 一个 CSV 索引文件，记录每个 date 样本对应的源目录、源文件、候选文件列表、shape 和文件大小。

结果说明:
- 若某个目录下有多个 `.mat`，脚本按文件名字典序选第一个，并把其余文件写入 CSV 的 `ignored_paths`。
- 若不同目录间 `data` shape 不一致，脚本直接报错退出，不会自动截断或补 NaN。
"""

from __future__ import annotations

import argparse
import gc
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd
from scipy.io import loadmat, whosmat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a disk-backed HDF5 CC cube from per-folder MATLAB files."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/cc_tsi_plot_outputs_ChangBai",
        help="输入根目录，递归扫描其下所有包含 .mat 的子目录。",
    )
    parser.add_argument(
        "--output-h5",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/cc_tsi_plot_outputs_ChangBai/cc_cube_by_date.h5",
        help="输出 HDF5 文件路径。",
    )
    parser.add_argument(
        "--meta-csv",
        type=Path,
        default=None,
        help="输出索引 CSV 路径。默认使用 <output-h5>.with_suffix('.csv')。",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="*.mat",
        help="每个子目录内匹配 .mat 文件的模式。",
    )
    parser.add_argument(
        "--chunk-mode",
        type=str,
        choices=["single_shot_across_dates", "date_slice"],
        default="single_shot_across_dates",
        help="HDF5 chunk 布局策略。",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="对 HDF5 主数据集启用 gzip 压缩。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="若输出文件已存在则覆盖。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描与打印元数据，不创建输出文件。",
    )
    return parser.parse_args()


def _mat_entries(mat_path: Path) -> dict[str, tuple[tuple[int, ...], str]]:
    entries: dict[str, tuple[tuple[int, ...], str]] = {}
    for name, shape, dtype in whosmat(str(mat_path)):
        entries[str(name)] = (tuple(int(x) for x in shape), str(dtype))
    return entries


def _normalize_data_shape(shape: tuple[int, ...]) -> tuple[int, int, int]:
    if len(shape) == 2:
        nt, noffset = shape
        return 1, int(nt), int(noffset)
    if len(shape) == 3:
        nshot, nt, noffset = shape
        return int(nshot), int(nt), int(noffset)
    raise ValueError(f"Unsupported data shape: {shape}")


def _safe_scalar(mat: dict[str, Any], key: str) -> float | str | None:
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
        return None


def discover_selected_files(root_dir: Path, mat_glob: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    folders = sorted({p.parent for p in root_dir.rglob(mat_glob) if p.is_file()})
    for folder in folders:
        candidates = sorted(p for p in folder.glob(mat_glob) if p.is_file())
        if not candidates:
            continue
        selected = candidates[0]
        rel_folder = folder.relative_to(root_dir)
        rows.append(
            {
                "date_label": str(rel_folder),
                "folder_path": str(folder),
                "mat_path": str(selected),
                "candidate_count": len(candidates),
                "candidate_paths": json.dumps([str(p) for p in candidates], ensure_ascii=False),
                "ignored_paths": json.dumps([str(p) for p in candidates[1:]], ensure_ascii=False),
                "selected_name": selected.name,
            }
        )
    return pd.DataFrame(rows)


def scan_metadata(index_df: pd.DataFrame) -> pd.DataFrame:
    if index_df.empty:
        return index_df.copy()

    rows: list[dict[str, Any]] = []
    for row in index_df.itertuples(index=False):
        mat_path = Path(row.mat_path)
        entries = _mat_entries(mat_path)
        if "data" not in entries:
            raise KeyError(f"{mat_path} missing variable 'data'")
        raw_shape, raw_dtype = entries["data"]
        nshot, nt, noffset = _normalize_data_shape(raw_shape)
        rows.append(
            {
                **row._asdict(),
                "file_size_mb": mat_path.stat().st_size / (1024**2),
                "raw_data_shape": str(raw_shape),
                "data_dtype": raw_dtype,
                "nshot": nshot,
                "nt": nt,
                "noffset": noffset,
            }
        )
    return pd.DataFrame(rows)


def validate_shapes(meta_df: pd.DataFrame) -> tuple[int, int, int]:
    if meta_df.empty:
        raise ValueError("No .mat files matched under the input root.")

    shape_df = meta_df[["date_label", "mat_path", "nshot", "nt", "noffset"]].copy()
    unique_shapes = shape_df[["nshot", "nt", "noffset"]].drop_duplicates().reset_index(drop=True)
    if len(unique_shapes) != 1:
        mismatch_text = shape_df.sort_values(
            ["nshot", "nt", "noffset", "date_label"]
        ).to_string(index=False)
        raise ValueError(f"Shape mismatch detected across selected .mat files:\n{mismatch_text}")
    first_shape = unique_shapes.iloc[0]
    return int(first_shape.nshot), int(first_shape.nt), int(first_shape.noffset)


def load_mat_data(mat_path: Path) -> tuple[np.ndarray, dict[str, float | None]]:
    mat = loadmat(mat_path)
    if "data" not in mat:
        raise KeyError(f"{mat_path} missing variable 'data'")
    data = np.asarray(mat["data"])
    data = np.squeeze(data)
    if data.ndim == 2:
        data = data[None, :, :]
    elif data.ndim != 3:
        raise ValueError(f"{mat_path} has unsupported data shape {data.shape}")
    data = np.asarray(data, dtype=np.float32)
    meta = {
        "dt": _safe_scalar(mat, "dt"),
        "dx": _safe_scalar(mat, "dx"),
        "dshot": _safe_scalar(mat, "dshot"),
    }
    return data, meta


def choose_chunk_shape(
    ndate: int,
    nshot: int,
    nt: int,
    noffset: int,
    mode: str,
) -> tuple[int, int, int, int]:
    _ = ndate
    if mode == "single_shot_across_dates":
        return 1, 1, min(nt, 256), min(noffset, 256)
    if mode == "date_slice":
        return 1, nshot, min(nt, 256), min(noffset, 256)
    raise ValueError(f"Unsupported chunk mode: {mode}")


def build_hdf5_cube(
    meta_df: pd.DataFrame,
    *,
    input_root: Path,
    output_h5: Path,
    chunk_mode: str,
    compress: bool,
    overwrite: bool,
    mat_glob: str,
) -> None:
    ndate = len(meta_df)
    nshot, nt, noffset = validate_shapes(meta_df)
    if output_h5.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_h5}")
    output_h5.parent.mkdir(parents=True, exist_ok=True)
    if output_h5.exists():
        output_h5.unlink()

    chunk_shape = choose_chunk_shape(ndate, nshot, nt, noffset, chunk_mode)
    compression = "gzip" if compress else None

    with h5py.File(output_h5, "w") as h5f:
        cc_ds = h5f.create_dataset(
            "cc",
            shape=(ndate, nshot, nt, noffset),
            dtype=np.float32,
            chunks=chunk_shape,
            compression=compression,
        )
        dt_ds = h5f.create_dataset("dt", shape=(ndate,), dtype=np.float64)
        dx_ds = h5f.create_dataset("dx", shape=(ndate,), dtype=np.float64)
        dshot_ds = h5f.create_dataset("dshot", shape=(ndate,), dtype=np.float64)

        str_dtype = h5py.string_dtype(encoding="utf-8")
        date_ds = h5f.create_dataset("date_labels", shape=(ndate,), dtype=str_dtype)
        path_ds = h5f.create_dataset("source_paths", shape=(ndate,), dtype=str_dtype)

        h5f.attrs["root_dir"] = str(input_root)
        h5f.attrs["mat_glob"] = mat_glob
        h5f.attrs["shape_policy"] = "strict"
        h5f.attrs["created_at"] = datetime.now().isoformat(timespec="seconds")
        h5f.attrs["file_select_mode"] = "single_per_folder"
        h5f.attrs["chunk_mode"] = chunk_mode

        for idx, row in enumerate(meta_df.itertuples(index=False), start=1):
            print(f"[{idx}/{ndate}] load -> {row.mat_path}")
            data, meta = load_mat_data(Path(row.mat_path))
            expected_shape = (nshot, nt, noffset)
            if data.shape != expected_shape:
                raise ValueError(
                    f"Loaded shape mismatch for {row.mat_path}: {data.shape} != {expected_shape}"
                )
            cc_ds[idx - 1, :, :, :] = data
            date_ds[idx - 1] = row.date_label
            path_ds[idx - 1] = row.mat_path
            dt_ds[idx - 1] = np.nan if meta["dt"] is None else float(meta["dt"])
            dx_ds[idx - 1] = np.nan if meta["dx"] is None else float(meta["dx"])
            dshot_ds[idx - 1] = np.nan if meta["dshot"] is None else float(meta["dshot"])
            del data
            gc.collect()


def main() -> None:
    args = parse_args()
    input_root = args.input_root.expanduser().resolve()
    output_h5 = args.output_h5.expanduser().resolve()
    meta_csv = (
        args.meta_csv.expanduser().resolve()
        if args.meta_csv is not None
        else output_h5.with_suffix(".csv")
    )

    if not input_root.exists() or not input_root.is_dir():
        raise ValueError(f"input-root not found: {input_root}")

    index_df = discover_selected_files(input_root, args.glob)
    meta_df = scan_metadata(index_df)
    nshot, nt, noffset = validate_shapes(meta_df)

    print(f"[INFO] input root    : {input_root}")
    print(f"[INFO] output h5     : {output_h5}")
    print(f"[INFO] meta csv      : {meta_csv}")
    print(f"[INFO] matched dates : {len(meta_df)}")
    print(
        "[INFO] data shape    : "
        f"(ndate, nshot, nt, noffset) = ({len(meta_df)}, {nshot}, {nt}, {noffset})"
    )
    print(f"[INFO] total size MB : {meta_df['file_size_mb'].sum():.2f}")
    print(f"[INFO] chunk mode    : {args.chunk_mode}")
    if not meta_df.empty:
        preview = meta_df[
            ["date_label", "selected_name", "candidate_count", "raw_data_shape", "file_size_mb"]
        ].sort_values("date_label")
        print(preview.to_string(index=False))

    if args.dry_run:
        print("[DONE] dry-run only, no files written.")
        return

    meta_csv.parent.mkdir(parents=True, exist_ok=True)
    meta_df.to_csv(meta_csv, index=False, encoding="utf-8")
    build_hdf5_cube(
        meta_df,
        input_root=input_root,
        output_h5=output_h5,
        chunk_mode=args.chunk_mode,
        compress=bool(args.compress),
        overwrite=bool(args.overwrite),
        mat_glob=args.glob,
    )
    print(f"[DONE] wrote HDF5: {output_h5}")
    print(f"[DONE] wrote CSV : {meta_csv}")


if __name__ == "__main__":
    main()
