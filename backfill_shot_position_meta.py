#!/usr/bin/env python3
"""
Batch backfill shot-position metadata for existing CC stack MAT files.

Purpose:
1) Add missing per-shot spatial metadata to existing `cc_stack_*.mat` files
   that were produced before shot-position fields were introduced.
2) Optionally write the same shot-position metadata into the matching
   diagnostics JSON file (`<mat_stem>_diag.json`).

Typical use cases:
- Old outputs only contain `data/dt/dx/dshot/xmin/...` and do not contain
  `shot_start_x_m` / `shot_source_x_m`.
- You need absolute/relative shot position arrays for plotting, indexing,
  or downstream inversion/dispersion workflows without re-running CC/TSI.

How shot positions are inferred:
- `n_shots` is inferred from MAT `data` shape:
  - `(n_shots, nt, nx)` -> use first dimension
  - `(nt, nx)` -> `n_shots = 1`
- `dx` is read from MAT (`dx` key), required.
- `dshot` is read from MAT (`dshot` key); if missing, falls back to `dx`.
- `shot_start_channel[i] = i * round(dshot / dx)`.
- Absolute position origin uses MAT `xmin` when available, otherwise
  `--default-xmin` (default `0.0`).
- Source-local index is read from diagnostics JSON when available:
  `shot_diagnostics[*].source_local_index`; otherwise defaults to 0.

Fields written into MAT:
- `shot_index`
- `shot_start_channel`
- `shot_source_channel`
- `shot_source_local_index`
- `shot_start_x_m`
- `shot_source_x_m`
- `shot_start_x_rel_m`
- `shot_source_x_rel_m`
- `shot_position_unit` = "m"

How to run:
1) In-place update (recommended for one directory):
   `uv run python examples/03_ccf/backfill_shot_position_meta.py \
      --input-dir /Users/zhangzhiyu/Downloads/cc_tsi_outputs`
2) Preview only (no file write):
   `uv run python examples/03_ccf/backfill_shot_position_meta.py \
      --input-dir /Users/zhangzhiyu/Downloads/cc_tsi_outputs --dry-run`
3) Write to a separate output directory:
   `uv run python examples/03_ccf/backfill_shot_position_meta.py \
      --input-dir /path/to/old_mats --output-dir /path/to/new_mats`

Outputs:
- Updated MAT files (in-place by default).
- Updated diagnostics JSON files (unless `--no-update-diag`).
- Terminal summary: processed / updated / skipped / failed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat, savemat
from tqdm import tqdm


SHOT_POS_KEYS = (
    "shot_index",
    "shot_start_channel",
    "shot_source_channel",
    "shot_source_local_index",
    "shot_start_x_m",
    "shot_source_x_m",
    "shot_start_x_rel_m",
    "shot_source_x_rel_m",
    "shot_position_unit",
)


def _scalar_from_mat(mat: dict[str, Any], key: str, default: float | None = None) -> float | None:
    if key not in mat:
        return default
    try:
        value = np.asarray(mat[key]).reshape(-1)
        if value.size == 0:
            return default
        return float(value[0])
    except Exception:
        return default


def _n_shots_from_data(mat: dict[str, Any]) -> int:
    if "data" not in mat:
        raise ValueError("missing key: data")
    data = np.squeeze(np.asarray(mat["data"]))
    if data.ndim == 2:
        return 1
    if data.ndim == 3:
        return int(data.shape[0])
    raise ValueError(f"invalid data shape: {tuple(data.shape)}")


def _load_source_local_indices(diag_path: Path, n_shots: int) -> np.ndarray:
    out = np.zeros(int(n_shots), dtype=int)
    if not diag_path.exists() or not diag_path.is_file():
        return out
    try:
        diag = json.loads(diag_path.read_text(encoding="utf-8"))
    except Exception:
        return out

    items = diag.get("shot_diagnostics")
    if not isinstance(items, list):
        return out
    for item in items:
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
    *,
    n_shots: int,
    dx: float,
    dshot: float,
    xmin: float,
    source_local_indices: np.ndarray,
) -> dict[str, Any]:
    if n_shots <= 0:
        raise ValueError(f"n_shots must be > 0, got {n_shots}")
    if not np.isfinite(dx) or dx <= 0:
        raise ValueError(f"dx must be > 0, got {dx}")
    if not np.isfinite(dshot) or dshot <= 0:
        raise ValueError(f"dshot must be > 0, got {dshot}")

    step_channels = max(int(round(float(dshot) / float(dx))), 1)
    shot_index = np.arange(int(n_shots), dtype=int)
    shot_start_channel = shot_index * step_channels

    source_local = np.asarray(source_local_indices, dtype=int).ravel()
    if source_local.size != int(n_shots):
        raise ValueError(
            f"source_local_indices size {source_local.size} != n_shots {n_shots}"
        )
    shot_source_channel = shot_start_channel + source_local

    shot_start_x = float(xmin) + shot_start_channel.astype(float) * float(dx)
    shot_source_x = float(xmin) + shot_source_channel.astype(float) * float(dx)

    return {
        "shot_index": shot_index,
        "shot_start_channel": shot_start_channel,
        "shot_source_channel": shot_source_channel,
        "shot_source_local_index": source_local,
        "shot_start_x_m": shot_start_x,
        "shot_source_x_m": shot_source_x,
        "shot_start_x_rel_m": shot_start_x - float(shot_start_x[0]),
        "shot_source_x_rel_m": shot_source_x - float(shot_source_x[0]),
        "shot_position_unit": "m",
    }


def _merge_jsonable(diag: dict[str, Any], shot_meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(diag)
    for key, value in shot_meta.items():
        if isinstance(value, np.ndarray):
            out[key] = value.tolist()
        elif isinstance(value, (np.integer,)):
            out[key] = int(value)
        elif isinstance(value, (np.floating,)):
            out[key] = float(value)
        else:
            out[key] = value
    return out


def process_one_mat(
    mat_path: Path,
    *,
    output_dir: Path | None,
    force: bool,
    update_diag: bool,
    default_xmin: float,
    dry_run: bool,
) -> tuple[str, str]:
    mat = loadmat(mat_path)
    has_all_keys = all(k in mat for k in SHOT_POS_KEYS)
    if has_all_keys and not force:
        return "skipped", f"{mat_path.name}: shot metadata already present"

    n_shots = _n_shots_from_data(mat)
    dx = _scalar_from_mat(mat, "dx", default=None)
    if dx is None:
        raise ValueError("missing key: dx")
    dshot = _scalar_from_mat(mat, "dshot", default=float(dx))
    xmin = _scalar_from_mat(mat, "xmin", default=float(default_xmin))

    diag_path = mat_path.with_name(f"{mat_path.stem}_diag.json")
    source_local = _load_source_local_indices(diag_path, n_shots=n_shots)
    shot_meta = build_shot_position_meta(
        n_shots=n_shots,
        dx=float(dx),
        dshot=float(dshot),
        xmin=float(xmin),
        source_local_indices=source_local,
    )

    if dry_run:
        return "updated", (
            f"{mat_path.name}: n_shots={n_shots}, dx={dx}, dshot={dshot}, xmin={xmin}, "
            f"start=[{float(shot_meta['shot_start_x_m'][0]):.3f}m -> {float(shot_meta['shot_start_x_m'][-1]):.3f}m]"
        )

    out_path = mat_path if output_dir is None else (output_dir / mat_path.name)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {k: v for k, v in mat.items() if not k.startswith("__")}
    payload.update(shot_meta)
    savemat(out_path, payload)

    if update_diag and diag_path.exists() and diag_path.is_file():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
            merged = _merge_jsonable(diag, shot_meta)
            diag_out = diag_path if output_dir is None else (output_dir / diag_path.name)
            diag_out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return "updated", (
        f"{mat_path.name}: n_shots={n_shots}, dx={dx}, dshot={dshot}, xmin={xmin}, "
        f"start=[{float(shot_meta['shot_start_x_m'][0]):.3f}m -> {float(shot_meta['shot_start_x_m'][-1]):.3f}m]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch backfill shot-position metadata for existing CC stack MAT files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing existing MAT files.",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="cc_stack_*.mat",
        help="MAT filename pattern under --input-dir.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory. If omitted, update MAT files in-place.",
    )
    parser.add_argument(
        "--default-xmin",
        type=float,
        default=0.0,
        help="Fallback x-origin (m) when MAT does not contain xmin.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite shot-position fields even if they already exist.",
    )
    parser.add_argument(
        "--no-update-diag",
        action="store_true",
        help="Do not update matching *_diag.json files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be updated without writing files.",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input dir not found: {input_dir}")

    mats = sorted([p for p in input_dir.glob(args.glob) if p.is_file()])
    if not mats:
        raise FileNotFoundError(f"No files matched: {input_dir / args.glob}")

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir is not None else None
    update_diag = not bool(args.no_update_diag)

    print(f"[INFO] input dir     : {input_dir}")
    print(f"[INFO] glob          : {args.glob}")
    print(f"[INFO] total mat     : {len(mats)}")
    print(f"[INFO] inplace       : {output_dir is None}")
    if output_dir is not None:
        print(f"[INFO] output dir    : {output_dir}")
    print(f"[INFO] update diag   : {update_diag}")
    print(f"[INFO] force         : {bool(args.force)}")
    print(f"[INFO] dry run       : {bool(args.dry_run)}")

    counts = {"updated": 0, "skipped": 0, "failed": 0}
    for mat_path in tqdm(mats, desc="Backfill shot meta", ncols=100):
        try:
            status, msg = process_one_mat(
                mat_path,
                output_dir=output_dir,
                force=bool(args.force),
                update_diag=update_diag,
                default_xmin=float(args.default_xmin),
                dry_run=bool(args.dry_run),
            )
            counts[status] += 1
            print(f"[{status.upper()}] {msg}")
        except Exception as exc:
            counts["failed"] += 1
            print(f"[FAILED] {mat_path.name}: {exc}")

    print(
        f"[DONE] updated={counts['updated']}, skipped={counts['skipped']}, failed={counts['failed']}"
    )


if __name__ == "__main__":
    main()
