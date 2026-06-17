#!/usr/bin/env python3
"""
批量对 `batch_caculate_cc_tsi_from_subfolders.py` 生成的多文件夹 CC/TSI `.mat`
结果执行二次叠加与出图。

用途:
1) 扫描一个输出根目录下所有包含 `cc_stack_tsi_*.mat`（或自定义 glob）的文件夹。
2) 对每个匹配文件夹调用现有 `scripts/stack_saved_cc_mat.py`。
3) 将每个数据文件夹的叠加结果输出到独立的目标子目录，保持与输入目录相同的相对层级。
4) 适合处理类似:
   `./outputs/cc_tsi_outputs_by_folder/2025-03-27/`
   `./outputs/cc_tsi_outputs_by_folder/2025-03-28/`
   这样的批量日目录结果。

如何运行:
1) 批量处理所有日期目录:
   `uv run python scripts/batch_stack_saved_cc_mat.py \
      --input-root ./outputs/cc_tsi_outputs_by_folder \
      --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --glob "cc_stack_tsi_*.mat" \
      --stack-size None \
      --interval None \
      --stack-method robust \
      --cc-backend cpu \
      --cc-device cpu \
      --jobs 8 \
      --plot-name local_cc_preview.png`

5) 只做叠加保存 `.mat`，不生成任何图片:
   `uv run python scripts/batch_stack_saved_cc_mat.py \
      --input-root ./outputs/cc_tsi_outputs_by_folder \
      --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --glob "cc_stack_tsi_*.mat" \
      --no-save-summary-plot \
      --no-save-shot-figures`

2) 只预览命令，不实际执行:
   `uv run python scripts/batch_stack_saved_cc_mat.py \
      --input-root ./outputs/cc_tsi_outputs_by_folder \
      --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --dry-run`

3) 只处理某一天:
   `uv run python scripts/batch_stack_saved_cc_mat.py \
      --input-root ./outputs/cc_tsi_outputs_by_folder \
      --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --folder-glob "2025-03-27"`

4) 跳过已经生成过目标图的目录:
   `uv run python scripts/batch_stack_saved_cc_mat.py \
      --input-root ./outputs/cc_tsi_outputs_by_folder \
      --output-root ./outputs/cc_tsi_plot_outputs_ChangBai \
      --skip-existing`

参数说明:
- `--input-root`: `batch_caculate_cc_tsi_from_subfolders.py` 的输出根目录。
- `--output-root`: 批量叠加和图片输出根目录。
- `--glob`: 每个输入子目录中用于查找 `.mat` 文件的模式。
- `--folder-glob`: 限制只处理相对路径匹配该 glob 的目录。
- `--stack-size`, `--interval`, `--stack-method`, `--plot-name`:
  透传给 `scripts/stack_saved_cc_mat.py`。
- `--cc-backend`, `--cc-device`, `--cc-dtype`:
  透传给 `scripts/stack_saved_cc_mat.py`，用于显式指定 CPU/GPU 叠加。
- `--start-index`: 透传给 `scripts/stack_saved_cc_mat.py`，控制从第几个 `.mat` 文件开始参与二次叠加。
- `--jobs`: 多文件夹并行度；并行单位是子目录。
- `--skip-existing`: 若目标子目录中已存在 `--plot-name`，则跳过该目录。
- `--recursive`: 递归扫描输入根目录；默认开启。

输出结果:
- 对于输入目录:
  `<input-root>/<rel-folder>/`
- 输出目录将写到:
  `<output-root>/<rel-folder>/`
- 每个输出子目录中包含:
  - `restack_*.mat`
  - `shots_fig/`
  - `<plot-name>`
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import fnmatch
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROJECT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class StackFolderJob:
    input_dir: Path
    output_dir: Path
    matched_files: int
    relative_dir: Path


@dataclass(frozen=True)
class JobRunResult:
    job: StackFolderJob
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


def discover_input_dirs(
    *,
    input_root: Path,
    output_root: Path,
    mat_glob: str,
    recursive: bool,
    folder_glob: str | None,
) -> list[StackFolderJob]:
    matched_dirs: dict[Path, int] = {}
    pattern = f"**/{mat_glob}" if recursive else mat_glob
    for mat_file in input_root.glob(pattern):
        if not mat_file.is_file():
            continue
        parent = mat_file.parent
        matched_dirs[parent] = matched_dirs.get(parent, 0) + 1

    jobs: list[StackFolderJob] = []
    for folder in sorted(matched_dirs):
        relative_dir = folder.relative_to(input_root)
        rel_text = str(relative_dir)
        if folder_glob and not fnmatch.fnmatch(rel_text, folder_glob):
            continue
        jobs.append(
            StackFolderJob(
                input_dir=folder,
                output_dir=output_root / relative_dir,
                matched_files=matched_dirs[folder],
                relative_dir=relative_dir,
            )
        )
    return jobs


def build_single_command(
    *,
    single_script: Path,
    input_dir: Path,
    output_dir: Path,
    mat_glob: str,
    stack_size: str,
    interval: str,
    start_index: int,
    stack_method: str,
    plot_name: str,
    show: bool,
    forward_args: Sequence[str],
) -> list[str]:
    cmd = [
        sys.executable,
        str(single_script),
        "--input-dir",
        str(input_dir),
        "--glob",
        str(mat_glob),
        "--output-dir",
        str(output_dir),
        "--stack-size",
        str(stack_size),
        "--interval",
        str(interval),
        "--start-index",
        str(int(start_index)),
        "--stack-method",
        str(stack_method),
        "--plot-name",
        str(plot_name),
    ]
    if show:
        cmd.append("--show")
    cmd.extend(list(forward_args))
    return cmd


def run_single_job(
    *,
    job: StackFolderJob,
    cmd: list[str],
) -> JobRunResult:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return JobRunResult(
        job=job,
        cmd=cmd,
        returncode=int(proc.returncode),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def emit_job_result(result: JobRunResult) -> None:
    if result.stdout.strip():
        print(f"[STDOUT] {result.job.relative_dir}")
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(f"[STDERR] {result.job.relative_dir}")
        print(result.stderr.rstrip())
    if result.returncode == 0:
        print(f"[OK] {result.job.relative_dir}")
    else:
        print(f"[FAILED] {result.job.relative_dir}, returncode={result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-run scripts/stack_saved_cc_mat.py for all matching output folders."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=PROJECT_DIR / "outputs/cc_tsi_outputs_by_folder",
        help="包含多个已保存 CC/TSI .mat 子目录的输入根目录。",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_DIR / "outputs/cc_tsi_plot_outputs_by_folder",
        help="批量叠加结果输出根目录。",
    )
    parser.add_argument(
        "--single-script",
        type=Path,
        default=PROJECT_DIR / "scripts/stack_saved_cc_mat.py",
        help="单目录叠加脚本路径。",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="cc_stack_tsi_*.mat",
        help="每个输入子目录内匹配 .mat 文件的模式。",
    )
    parser.add_argument(
        "--folder-glob",
        type=str,
        default=None,
        help="按相对 input-root 的目录路径过滤，例如 '2025-03-*' 或 '2025-03-27/*'。",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否递归扫描 input-root。默认开启。",
    )
    parser.add_argument(
        "--stack-size",
        type=str,
        default="None",
        help="透传给单目录脚本；传 None 表示使用全部文件。",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="None",
        help="透传给单目录脚本；传 None 表示只做一组叠加。",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="透传给单目录脚本。",
    )
    parser.add_argument(
        "--stack-method",
        type=str,
        default="robust",
        choices=["pws", "robust", "linear", "mean"],
        help="透传给单目录脚本。",
    )
    parser.add_argument(
        "--plot-name",
        type=str,
        default="local_cc_preview.png",
        help="每个输出子目录中的汇总图文件名。",
    )
    parser.add_argument(
        "--cc-backend",
        type=str,
        default="auto",
        choices=["auto", "cpu", "torch"],
        help="透传给单目录脚本；显式指定叠加后端。",
    )
    parser.add_argument(
        "--cc-device",
        type=str,
        default="auto",
        choices=["auto", "cpu", "mps", "cuda"],
        help="透传给单目录脚本；显式指定叠加设备。",
    )
    parser.add_argument(
        "--cc-dtype",
        type=str,
        default="float32",
        choices=["float32", "float64"],
        help="透传给单目录脚本；指定叠加计算 dtype。",
    )
    parser.add_argument(
        "--save-summary-plot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="透传给单目录脚本；是否保存汇总预览图。",
    )
    parser.add_argument(
        "--save-shot-figures",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="透传给单目录脚本；是否保存逐 shot 和 merged 图片。",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="若目标子目录里已存在 plot-name，则跳过该目录。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印命令，不执行。",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="透传给单目录脚本，保存图后同时弹窗显示。",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="任一目录失败即停止。",
    )
    parser.add_argument(
        "--max-folders",
        type=int,
        default=None,
        help="最多处理前 N 个匹配目录。",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="并行处理的文件夹数。1 表示串行；>1 表示多文件夹 CPU 并行叠加。",
    )

    args, forward_args = parser.parse_known_args()

    input_root = args.input_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    single_script = args.single_script.expanduser().resolve()

    if not input_root.exists() or not input_root.is_dir():
        raise ValueError(f"input-root not found: {input_root}")
    if not single_script.exists() or not single_script.is_file():
        raise ValueError(f"single-script not found: {single_script}")
    if args.start_index < 0:
        raise ValueError("--start-index must be >= 0")
    if args.max_folders is not None and args.max_folders < 0:
        raise ValueError("--max-folders must be >= 0")
    if args.jobs <= 0:
        raise ValueError("--jobs must be > 0")

    jobs = discover_input_dirs(
        input_root=input_root,
        output_root=output_root,
        mat_glob=args.glob,
        recursive=bool(args.recursive),
        folder_glob=args.folder_glob,
    )
    if args.max_folders is not None:
        jobs = jobs[: args.max_folders]

    print(f"[INFO] input root   : {input_root}")
    print(f"[INFO] output root  : {output_root}")
    print(f"[INFO] single script: {single_script}")
    print(f"[INFO] mat glob     : {args.glob}")
    print(f"[INFO] recursive    : {bool(args.recursive)}")
    print(f"[INFO] folder glob  : {args.folder_glob}")
    print(f"[INFO] matched dirs : {len(jobs)}")
    print(f"[INFO] parallel jobs: {args.jobs}")
    print(f"[INFO] cc backend   : {args.cc_backend}")
    print(f"[INFO] cc device    : {args.cc_device}")
    print(f"[INFO] cc dtype     : {args.cc_dtype}")
    print(f"[INFO] summary plot : {bool(args.save_summary_plot)}")
    print(f"[INFO] shot figures : {bool(args.save_shot_figures)}")
    if forward_args:
        print(f"[INFO] forwarded args: {' '.join(shlex.quote(a) for a in forward_args)}")

    if not jobs:
        print("[DONE] no folder contains matched MAT files.")
        return

    success = 0
    failed = 0
    skipped = 0
    runnable_jobs: list[tuple[int, StackFolderJob, list[str]]] = []

    for idx, job in enumerate(jobs, start=1):
        target_plot = job.output_dir / args.plot_name
        if args.skip_existing and target_plot.exists():
            skipped += 1
            print(f"[SKIP] {job.relative_dir} -> existing plot: {target_plot}")
            continue

        cmd = build_single_command(
            single_script=single_script,
            input_dir=job.input_dir,
            output_dir=job.output_dir,
            mat_glob=args.glob,
            stack_size=args.stack_size,
            interval=args.interval,
            start_index=args.start_index,
            stack_method=args.stack_method,
            plot_name=args.plot_name,
            show=bool(args.show),
            forward_args=[
                "--cc-backend",
                args.cc_backend,
                "--cc-device",
                args.cc_device,
                "--cc-dtype",
                args.cc_dtype,
                "--save-summary-plot" if args.save_summary_plot else "--no-save-summary-plot",
                "--save-shot-figures" if args.save_shot_figures else "--no-save-shot-figures",
                *list(forward_args),
            ],
        )
        print(
            f"[{idx}/{len(jobs)}] folder={job.relative_dir} matched_files={job.matched_files} "
            f"-> output={job.output_dir}"
        )
        print(f"[CMD] {' '.join(shlex.quote(x) for x in cmd)}")

        if args.dry_run:
            skipped += 1
            continue
        runnable_jobs.append((idx, job, cmd))

    if not args.dry_run and runnable_jobs:
        max_workers = min(args.jobs, len(runnable_jobs))
        pending_iter = iter(runnable_jobs)
        running: dict[Future[JobRunResult], tuple[int, StackFolderJob]] = {}
        stop_submit = False

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while len(running) < max_workers:
                try:
                    next_idx, next_job, next_cmd = next(pending_iter)
                except StopIteration:
                    break
                future = executor.submit(run_single_job, job=next_job, cmd=next_cmd)
                running[future] = (next_idx, next_job)

            while running:
                done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    _, done_job = running.pop(future)
                    result = future.result()
                    emit_job_result(result)
                    if result.returncode == 0:
                        success += 1
                    else:
                        failed += 1
                        if args.stop_on_error:
                            stop_submit = True

                    if stop_submit:
                        continue

                    try:
                        next_idx, next_job, next_cmd = next(pending_iter)
                    except StopIteration:
                        continue
                    new_future = executor.submit(
                        run_single_job, job=next_job, cmd=next_cmd
                    )
                    running[new_future] = (next_idx, next_job)

            if stop_submit:
                remaining = sum(1 for _ in pending_iter)
                skipped += remaining
                if remaining > 0:
                    print(
                        f"[STOP] stop-on-error enabled; skipped {remaining} not-yet-started folder(s)."
                    )

    print(
        f"[DONE] total={len(jobs)} success={success} failed={failed} "
        f"dryrun_or_skipped={skipped}"
    )


if __name__ == "__main__":
    main()
