#!/usr/bin/env python3
"""
批量读取按子目录组织的 CC `.mat` 文件，逐炮提取频散并保存横向双图结果（CC + 频散）。

用途:
1) 递归扫描输入根目录下所有包含 `.mat` 文件的子目录，或只处理单个指定目录。
2) 逐个 `.mat` 读取 `data/dt/dx/dshot`，支持 `data` 为 `(nshot, nt, noffset)` 或 `(nt, noffset)`。
3) 对每一炮调用 dasQt 现有的 `compute_dispersion_image()` 提取频散能量图。
4) 将每一炮保存为横向双图 PNG：左侧 CC，右侧频散；可选把整份 `.mat` 的频散数据立方保存为 `npz`。
5) 输出目录保持与输入目录相同的相对层级，便于按日期/批次回查。

适用场景:
- 输入目录类似 `outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27/*.mat`
- 每个 `.mat` 中存的是多炮 CC gather，希望批量得到所有炮的 CC+频散对照图。

运行方式:
1) 使用 ChangBai 频散参数批量处理全部子目录:
   `uv run python tools/extract_dispersion_from_cc_mat.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --output-root ./outputs/dispersion_outputs_ChangBai \
      --config ./config/disp_config.jsonc \
      --workers 8`

2) 只处理前 20 炮，并额外保存频散数据立方:
   `uv run python tools/extract_dispersion_from_cc_mat.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --output-root ./outputs/dispersion_outputs_ChangBai \
      --config ./config/disp_config.jsonc \
      --shot-stop 20 \
      --save-npz`

3) 只预览命中的目录和文件，不实际计算:
   `uv run python tools/extract_dispersion_from_cc_mat.py \
      --input-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --dry-run`

4) 只处理单个文件夹:
   `uv run python tools/extract_dispersion_from_cc_mat.py \
      --input-dir ./outputs/cc_tsi_plot_outputs_ChangBai/2025-03-27 \
      --output-root ./outputs/dispersion_outputs_ChangBai_single \
      --config ./config/disp_config.jsonc`

输入:
- 一个目录树，或单个目录；每个有效目录至少有一个匹配 `--glob` 的 `.mat` 文件。
- 配置文件可以是:
  - legacy `dispersion.jsonc`，读取 `disp.par`
  - flat `disp_config.jsonc`，读取扁平的 `cmin/cmax/dc/fmin/fmax/df/method`

输出:
- 每个输入 `.mat` 对应一个输出目录:
  `<output-root>/<相对输入目录>/<mat文件名去后缀>/`
- 该目录下包含:
  - `shot_0000_dispersion.png` 等每炮横向双图（左 CC，右频散）
  - 可选 `dispersion_cube.npz`，包含 `images/f/c/shot_indices`
- `<output-root>/dispersion_index.csv` 汇总全部处理文件与参数

参数说明:
- `--chunk` 不涉及；本脚本直接逐炮计算，不建立 HDF5。
- `--input-dir` 若提供，则只处理这一个目录，优先级高于 `--input-root`。
- `--method` 若提供，则覆盖配置文件里的方法。
- `--shot-start/--shot-stop` 控制处理炮号范围。
- `--save-npz` 会把每个 `.mat` 的频散图堆叠为 `(nshot, nc, nf)` 保存，便于后续复用。
- `--workers` 控制 CPU 多进程并行度，并行粒度为“每个 `.mat` 文件一个任务”，适合同时处理多个日期/子目录。

结果说明:
- 绘图字体统一使用 `Times New Roman`。
- 若某个 `.mat` 只有二维 `data`，会按单炮处理。
- 若某一炮频散计算失败，默认记录错误并继续后续炮；整文件不会中断。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import loadmat

from dasQt.features.dispersion.backend.components.utils.dispersion_utils import (
    compute_dispersion_image,
)
from dasQt.utils.config import load_jsonc_file


plt.rcParams.update(
    {
        "font.family": "Times New Roman",
        "font.sans-serif": ["Times New Roman"],
        "font.size": 12,
    }
)


@dataclass(frozen=True)
class MatJob:
    mat_path: Path
    output_dir: Path
    relative_dir: Path


@dataclass(frozen=True)
class MatJobResult:
    job: MatJob
    saved_png: int
    failed_shots: int
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract per-shot dispersion images from saved CC MAT files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="单目录模式：只处理这个目录中的 .mat 文件，优先级高于 --input-root。",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/cc_tsi_plot_outputs_ChangBai",
        help="输入根目录，递归扫描其下所有包含 .mat 的子目录。",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/dispersion_outputs_ChangBai",
        help="输出根目录，保持与输入相同的相对层级。",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/disp_config.jsonc",
        help="频散参数配置文件；默认使用 config/disp_config.jsonc，也兼容 legacy dispersion.jsonc。",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="*.mat",
        help="每个子目录内匹配 .mat 文件的模式。",
    )
    parser.add_argument(
        "--method",
        type=str,
        default=None,
        help="频散方法；若提供则覆盖配置文件中的方法。",
    )
    parser.add_argument(
        "--shot-start",
        type=int,
        default=0,
        help="起始炮号（包含）。",
    )
    parser.add_argument(
        "--shot-stop",
        type=int,
        default=None,
        help="结束炮号（不包含）。",
    )
    parser.add_argument(
        "--save-npz",
        action="store_true",
        help="为每个 .mat 额外保存一个 dispersion_cube.npz。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已有输出图与 npz。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印匹配结果，不实际计算。",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="最多处理前 N 个匹配文件，调试用。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="CPU 并行进程数。1 表示串行；大于 1 时按 .mat 文件并行处理多个子目录。",
    )
    return parser.parse_args()


def _mat_scalar(mat: dict[str, Any], key: str, default: float | None = None) -> float | None:
    if key not in mat:
        return default
    value = np.asarray(mat[key]).squeeze()
    if np.size(value) == 0:
        return default
    return float(np.asarray(value).reshape(-1)[0])


def load_cc_mat(path: Path) -> tuple[np.ndarray, float, float, float | None]:
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
    if dt is None or dx is None:
        raise ValueError(f"{path.name} missing dt/dx")
    return np.asarray(data, dtype=np.float32), float(dt), float(dx), dshot


def load_dispersion_scan_params(config_path: Path, method_override: str | None) -> dict[str, Any]:
    cfg = load_jsonc_file(str(config_path))
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config content: {config_path}")

    # flat disp_config.jsonc
    if "cmin" in cfg and "cmax" in cfg and "fmin" in cfg and "fmax" in cfg:
        out = {
            "cmin": float(cfg["cmin"]),
            "cmax": float(cfg["cmax"]),
            "dc": float(cfg["dc"]),
            "fmin": float(cfg["fmin"]),
            "fmax": float(cfg["fmax"]),
            "df": float(cfg["df"]),
            "method": str(cfg.get("method", "park")),
            "mfj_kernel": str(cfg.get("mfj_kernel", "h0")),
            "mmfj_kernel": str(cfg.get("mmfj_kernel", "h0")),
        }
    else:
        disp = cfg.get("disp", {})
        par = disp.get("par", {}) if isinstance(disp, dict) else {}
        out = {
            "cmin": float(par.get("Cmin", 10.0)),
            "cmax": float(par.get("Cmax", 800.0)),
            "dc": float(par.get("dc", 1.0)),
            "fmin": float(par.get("fmin", 3.0)),
            "fmax": float(par.get("fmax", 30.0)),
            "df": float(par.get("df", 0.1)),
            "method": str(cfg.get("method", "park")),
            "mfj_kernel": str(cfg.get("mfj_kernel", "h0")),
            "mmfj_kernel": str(cfg.get("mmfj_kernel", "h0")),
        }

    if out["cmax"] <= out["cmin"]:
        raise ValueError("Require cmax > cmin")
    if out["fmax"] <= out["fmin"]:
        raise ValueError("Require fmax > fmin")
    if out["dc"] <= 0 or out["df"] <= 0:
        raise ValueError("Require dc > 0 and df > 0")

    if method_override is not None:
        out["method"] = str(method_override)
    return out


def discover_jobs(
    *,
    input_root: Path | None,
    input_dir: Path | None,
    output_root: Path,
    mat_glob: str,
) -> list[MatJob]:
    if input_dir is not None:
        files = sorted(p for p in input_dir.glob(mat_glob) if p.is_file())
        jobs: list[MatJob] = []
        for mat_path in files:
            out_dir = output_root / mat_path.stem
            jobs.append(MatJob(mat_path=mat_path, output_dir=out_dir, relative_dir=Path(".")))
        return jobs

    if input_root is None:
        raise ValueError("input_root must not be None when input_dir is not provided")
    files = sorted(p for p in input_root.rglob(mat_glob) if p.is_file())
    jobs: list[MatJob] = []
    for mat_path in files:
        relative_parent = mat_path.parent.relative_to(input_root)
        out_dir = output_root / relative_parent / mat_path.stem
        jobs.append(MatJob(mat_path=mat_path, output_dir=out_dir, relative_dir=relative_parent))
    return jobs


def build_axes(params: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    f = np.arange(
        float(params["fmin"]),
        float(params["fmax"]) + 0.5 * float(params["df"]),
        float(params["df"]),
        dtype=float,
    )
    c = np.arange(
        float(params["cmin"]),
        float(params["cmax"]) + 0.5 * float(params["dc"]),
        float(params["dc"]),
        dtype=float,
    )
    if f.size < 2:
        raise ValueError("Frequency axis is too short. Check fmin/fmax/df.")
    if c.size < 2:
        raise ValueError("Velocity axis is too short. Check cmin/cmax/dc.")
    return f, c


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


def plot_cc_and_dispersion(
    shot_cc: np.ndarray,
    img: np.ndarray,
    *,
    shot_idx: int,
    dx: float,
    dt: float,
    dshot: float | None,
    f_axis: np.ndarray,
    c_axis: np.ndarray,
    out_file: Path,
    mat_stem: str,
    title: str,
) -> None:
    shot_cc = np.asarray(shot_cc, dtype=float)
    img = np.asarray(img, dtype=float)
    nt, nx = shot_cc.shape
    x_start = float(shot_idx) * float(dshot if dshot is not None else dx)
    x_centers = x_start + np.arange(nx, dtype=float) * float(dx)
    t_centers = (np.arange(nt, dtype=float) - float(nt // 2)) * float(dt)

    cc_vmax = float(np.percentile(np.abs(shot_cc), 99.0))
    if not np.isfinite(cc_vmax) or cc_vmax <= 0:
        cc_vmax = 1.0
    vmax = float(np.percentile(img, 99.5))
    if not np.isfinite(vmax) or vmax <= 0:
        vmax = float(np.max(img)) if np.max(img) > 0 else 1.0

    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.4), constrained_layout=True)

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
    ax_cc.set_title(f"{mat_stem} | Shot {shot_idx:04d}: CC")
    ax_cc.set_xlabel("Distance (m)")
    ax_cc.set_ylabel("Time (s)")
    fig.colorbar(im_cc, ax=ax_cc, fraction=0.046, pad=0.04, label="Amplitude")

    ax_disp = axes[1]
    im = ax_disp.imshow(
        img,
        extent=[float(f_axis[0]), float(f_axis[-1]), float(c_axis[0]), float(c_axis[-1])],
        aspect="auto",
        origin="lower",
        cmap="jet",
        interpolation="bilinear",
        vmin=0.0,
        vmax=vmax,
    )
    ax_disp.set_title(title)
    ax_disp.set_xlabel("Frequency (Hz)")
    ax_disp.set_ylabel("Phase Velocity (m/s)")
    fig.colorbar(im, ax=ax_disp, fraction=0.046, pad=0.04, label="Normalized energy")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_file, dpi=180, bbox_inches="tight")
    plt.close(fig)


def process_one_mat(
    job: MatJob,
    *,
    params: dict[str, Any],
    shot_start: int,
    shot_stop: int | None,
    save_npz: bool,
    overwrite: bool,
) -> tuple[int, int]:
    data, dt, dx, dshot = load_cc_mat(job.mat_path)
    nshot = int(data.shape[0])
    start = max(0, int(shot_start))
    stop = nshot if shot_stop is None else min(int(shot_stop), nshot)
    if start >= stop:
        return 0, 0

    f, c = build_axes(params)
    saved = 0
    failed = 0
    cube_list: list[np.ndarray] = []
    shot_indices: list[int] = []

    for ishot in range(start, stop):
        out_png = job.output_dir / f"shot_{ishot:04d}_dispersion.png"
        if out_png.exists() and not overwrite:
            continue
        try:
            shot_cc = np.nan_to_num(np.asarray(data[ishot], dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
            f_out, c_out, img = compute_dispersion_image(
                shot_cc,
                dx,
                dt,
                f,
                c,
                str(params["method"]),
                mfj_kernel=str(params.get("mfj_kernel", "h0")),
                mmfj_kernel=str(params.get("mmfj_kernel", "h0")),
            )
            plot_cc_and_dispersion(
                shot_cc=shot_cc,
                img=np.asarray(img, dtype=float),
                shot_idx=ishot,
                dx=dx,
                dt=dt,
                dshot=dshot,
                f_axis=np.asarray(f_out, dtype=float),
                c_axis=np.asarray(c_out, dtype=float),
                out_file=out_png,
                mat_stem=job.mat_path.stem,
                title=f"{job.mat_path.stem} | Shot {ishot:04d} | method={params['method']}",
            )
            if save_npz:
                cube_list.append(np.asarray(img, dtype=np.float32))
                shot_indices.append(int(ishot))
            saved += 1
            print(f"[OK] {out_png}")
        except Exception as exc:
            failed += 1
            print(f"[FAILED] {job.mat_path.name} shot={ishot}: {exc}")

    if save_npz and cube_list:
        npz_path = job.output_dir / "dispersion_cube.npz"
        if overwrite or not npz_path.exists():
            np.savez_compressed(
                npz_path,
                images=np.stack(cube_list, axis=0),
                f=np.asarray(f, dtype=np.float32),
                c=np.asarray(c, dtype=np.float32),
                shot_indices=np.asarray(shot_indices, dtype=np.int32),
                dt=np.float32(dt),
                dx=np.float32(dx),
                dshot=np.float32(dshot if dshot is not None else dx),
            )
            print(f"[OK] {npz_path}")

    return saved, failed


def process_one_job(
    job: MatJob,
    *,
    params: dict[str, Any],
    shot_start: int,
    shot_stop: int | None,
    save_npz: bool,
    overwrite: bool,
) -> MatJobResult:
    try:
        saved, failed = process_one_mat(
            job,
            params=params,
            shot_start=shot_start,
            shot_stop=shot_stop,
            save_npz=save_npz,
            overwrite=overwrite,
        )
        return MatJobResult(job=job, saved_png=saved, failed_shots=failed, error=None)
    except Exception as exc:
        return MatJobResult(job=job, saved_png=0, failed_shots=0, error=str(exc))


def resolve_workers(requested_workers: int, num_jobs: int) -> int:
    if requested_workers < 1:
        raise ValueError("--workers must be >= 1")
    if num_jobs <= 0:
        return 1
    return min(int(requested_workers), int(num_jobs), os.cpu_count() or 1)


def run_jobs(
    jobs: list[MatJob],
    *,
    params: dict[str, Any],
    shot_start: int,
    shot_stop: int | None,
    save_npz: bool,
    overwrite: bool,
    workers: int,
) -> list[MatJobResult]:
    if workers <= 1 or len(jobs) <= 1:
        return [
            process_one_job(
                job,
                params=params,
                shot_start=shot_start,
                shot_stop=shot_stop,
                save_npz=save_npz,
                overwrite=overwrite,
            )
            for job in jobs
        ]

    results: list[MatJobResult] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                process_one_job,
                job,
                params=params,
                shot_start=shot_start,
                shot_stop=shot_stop,
                save_npz=save_npz,
                overwrite=overwrite,
            )
            for job in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def write_index_csv(index_path: Path, rows: list[dict[str, Any]]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "relative_dir",
        "mat_path",
        "output_dir",
        "method",
        "fmin",
        "fmax",
        "df",
        "cmin",
        "cmax",
        "dc",
        "saved_png",
        "failed_shots",
        "error",
    ]
    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve() if args.input_dir is not None else None
    input_root = args.input_root.expanduser().resolve() if input_dir is None else None
    output_root = args.output_root.expanduser().resolve()
    config_path = args.config.expanduser().resolve()

    if input_dir is not None:
        if not input_dir.exists() or not input_dir.is_dir():
            raise ValueError(f"input-dir not found: {input_dir}")
    else:
        assert input_root is not None
        if not input_root.exists() or not input_root.is_dir():
            raise ValueError(f"input-root not found: {input_root}")
    if not config_path.exists() or not config_path.is_file():
        raise ValueError(f"config not found: {config_path}")
    if args.shot_start < 0:
        raise ValueError("--shot-start must be >= 0")
    if args.shot_stop is not None and args.shot_stop < 0:
        raise ValueError("--shot-stop must be >= 0")

    params = load_dispersion_scan_params(config_path, args.method)
    jobs = discover_jobs(
        input_root=input_root,
        input_dir=input_dir,
        output_root=output_root,
        mat_glob=args.glob,
    )
    if args.max_files is not None:
        jobs = jobs[: max(0, int(args.max_files))]
    workers = resolve_workers(int(args.workers), len(jobs))

    if input_dir is not None:
        print(f"[INFO] input dir  : {input_dir}")
    else:
        print(f"[INFO] input root : {input_root}")
    print(f"[INFO] output root: {output_root}")
    print(f"[INFO] config     : {config_path}")
    print(f"[INFO] method     : {params['method']}")
    print(
        "[INFO] scan grid  : "
        f"f=[{params['fmin']}, {params['fmax']}], df={params['df']}; "
        f"c=[{params['cmin']}, {params['cmax']}], dc={params['dc']}"
    )
    print(f"[INFO] matched mat: {len(jobs)}")
    print(f"[INFO] workers    : {workers}")

    if not jobs:
        print("[DONE] no .mat files matched.")
        return

    if args.dry_run:
        for idx, job in enumerate(jobs, start=1):
            print(f"[{idx}/{len(jobs)}] {job.mat_path} -> {job.output_dir}")
        print("[DONE] dry-run only.")
        return

    index_rows: list[dict[str, Any]] = []
    total_png = 0
    total_failed = 0
    results = run_jobs(
        jobs,
        params=params,
        shot_start=int(args.shot_start),
        shot_stop=args.shot_stop,
        save_npz=bool(args.save_npz),
        overwrite=bool(args.overwrite),
        workers=workers,
    )

    for idx, result in enumerate(sorted(results, key=lambda item: item.job.mat_path), start=1):
        job = result.job
        print(f"[{idx}/{len(jobs)}] done {job.mat_path}")
        if result.error is not None:
            total_failed += 1
            print(f"[FAILED] {job.mat_path}: {result.error}")
            index_rows.append(
                {
                    "relative_dir": str(job.relative_dir),
                    "mat_path": str(job.mat_path),
                    "output_dir": str(job.output_dir),
                    "method": str(params["method"]),
                    "fmin": float(params["fmin"]),
                    "fmax": float(params["fmax"]),
                    "df": float(params["df"]),
                    "cmin": float(params["cmin"]),
                    "cmax": float(params["cmax"]),
                    "dc": float(params["dc"]),
                    "saved_png": 0,
                    "failed_shots": 0,
                    "error": str(result.error),
                }
            )
            continue
        saved = int(result.saved_png)
        failed = int(result.failed_shots)
        total_png += saved
        total_failed += failed
        index_rows.append(
            {
                "relative_dir": str(job.relative_dir),
                "mat_path": str(job.mat_path),
                "output_dir": str(job.output_dir),
                "method": str(params["method"]),
                "fmin": float(params["fmin"]),
                "fmax": float(params["fmax"]),
                "df": float(params["df"]),
                "cmin": float(params["cmin"]),
                "cmax": float(params["cmax"]),
                "dc": float(params["dc"]),
                "saved_png": int(saved),
                "failed_shots": int(failed),
                "error": "",
            }
        )

    index_csv = output_root / "dispersion_index.csv"
    write_index_csv(index_csv, index_rows)
    print(f"[DONE] total_png={total_png} failed_shots={total_failed}")
    print(f"[DONE] index_csv={index_csv}")


if __name__ == "__main__":
    main()
