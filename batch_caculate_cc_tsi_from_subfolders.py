#!/usr/bin/env python3
"""
Batch-run CC/TSI computation for all subfolders under one root DAS directory.

用途:
1) 扫描一个根目录下的子文件夹（可选递归）。
2) 对每个包含 DAS 数据文件的子文件夹，调用现有脚本
   `caculate_cc_tsi_from_folder.py` 进行计算。
3) 结果按子文件夹相对路径分别保存，避免不同数据源混在一起。

适用场景:
- 例如批量处理:
  `/Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07`
  下多个子目录，每个子目录都是一组 DAS 文件。

执行方式:
1) 直接批处理（默认遍历一层子目录）:
   `uv run python examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py \
      --root-data-dir /Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07 \
      --output-root /Users/zhangzhiyu/Downloads/cc_tsi_outputs_by_folder \
      --config /Users/zhangzhiyu/MyProjects/dasQt/examples/03_ccf/config/cc_config.jsonc \
      --cc-enhance tsi --tsi-mode single_shot_local_cc --save-every 5`

2) 先预览命令，不执行:
   `uv run python examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py \
      --root-data-dir /Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07 \
      --dry-run`

3) 递归扫描所有层级子目录:
   `uv run python examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py \
      --root-data-dir /Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07 \
      --recursive`

4) 仅处理每天 08:00-18:00:
   `uv run python examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py \
      --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
      --config ./config_ChangBai/cc_config.jsonc \
      --time-start 08:00 --time-end 18:00`

5) 从指定日期开始处理:
   `uv run python examples/03_ccf/batch_caculate_cc_tsi_from_subfolders.py \
      --root-data-dir /mnt/nas_1/DAS/ChangBai/ChangBai_20250317/ \
      --config ./config_ChangBai/cc_config.jsonc \
      --date-start 2025-03-23`

扫描优化:
- 子目录内的数据文件统计改为 `os.scandir()`，比逐个 `Path.iterdir()` 更适合 NAS。
- 如需重建单目录脚本的文件索引缓存，可加 `--refresh-file-index`，该参数会透传。
- 如需完全绕开每个数据子目录的扫描，可在每个子目录内放一个固定名字的
  manifest 文件，并配合 `--file-list-name` 使用。
- 如需仅计算每天固定时段，可加 `--time-start HH:MM --time-end HH:MM`，
  当前 ChangBai 文件按 `YYYY-MM-DD-HH-MM-SS-out.dat` 从文件名解析时间。

输出结果:
- 每个被处理的子目录对应一个输出目录:
  `<output_root>/<子目录相对 root-data-dir 的路径>/`
- 每个输出目录内是原脚本产物:
  `cc_stack_*.mat` 和（TSI 模式下）`*_diag.json`
- 终端会输出汇总统计: 总目录数、已运行、成功、失败、跳过。
"""

from __future__ import annotations

import argparse
from datetime import date as dt_date, datetime, time as dt_time
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SUPPORTED_SUFFIXES = (".pkl", ".pickle", ".tdms", ".h5", ".hdf5", ".segy", ".sgy", ".dat")
FILENAME_TIME_FORMAT = "%Y-%m-%d-%H-%M-%S"


@dataclass
class FolderJob:
    folder: Path
    output_dir: Path
    file_count: int
    file_list: Path | None = None


def parse_hour_minute(value: str, *, arg_name: str) -> dt_time:
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"Invalid {arg_name}: {value!r}. Expected HH:MM in 24-hour format.") from exc
    return parsed.time()


def parse_date_yyyy_mm_dd(value: str, *, arg_name: str) -> dt_date:
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid {arg_name}: {value!r}. Expected YYYY-MM-DD.") from exc
    return parsed.date()


def _extract_folder_date(folder: Path, root_data_dir: Path) -> dt_date | None:
    rel = folder.relative_to(root_data_dir)
    candidates: list[str] = []
    if rel.parts and rel.parts[0] not in {"", "."}:
        candidates.append(rel.parts[0])
    candidates.append(folder.name)
    seen: set[str] = set()
    for text in candidates:
        if text in seen:
            continue
        seen.add(text)
        try:
            return parse_date_yyyy_mm_dd(text, arg_name="folder-date")
        except ValueError:
            continue
    return None


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


def _in_time_window(path: Path, *, time_start: dt_time | None, time_end: dt_time | None) -> bool:
    if time_start is None and time_end is None:
        return True
    if time_start is None or time_end is None:
        raise ValueError("Both --time-start and --time-end must be provided together.")
    if time_start >= time_end:
        raise ValueError("--time-start must be earlier than --time-end. Cross-midnight windows are not supported.")
    tod = parse_timestamp_from_filename(path).time()
    return time_start <= tod < time_end


def _count_supported_files(
    folder: Path,
    *,
    time_start: dt_time | None = None,
    time_end: dt_time | None = None,
) -> int:
    count = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            path = Path(entry.name)
            if path.suffix.lower() in SUPPORTED_SUFFIXES and _in_time_window(
                path,
                time_start=time_start,
                time_end=time_end,
            ):
                count += 1
    return count


def _count_manifest_entries(
    file_list_path: Path,
    *,
    time_start: dt_time | None = None,
    time_end: dt_time | None = None,
) -> int:
    count = 0
    for raw_line in file_list_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = Path(line)
        if path.suffix.lower() in SUPPORTED_SUFFIXES and _in_time_window(
            path,
            time_start=time_start,
            time_end=time_end,
        ):
            count += 1
    return count


def discover_jobs(
    root_data_dir: Path,
    output_root: Path,
    recursive: bool,
    include_root: bool,
    file_list_name: str | None,
    date_start: dt_date | None = None,
    time_start: dt_time | None = None,
    time_end: dt_time | None = None,
) -> list[FolderJob]:
    if recursive:
        folders = sorted(p for p in root_data_dir.rglob("*") if p.is_dir())
    else:
        folders = sorted(p for p in root_data_dir.iterdir() if p.is_dir())

    if include_root:
        folders = [root_data_dir] + folders

    jobs: list[FolderJob] = []
    for folder in folders:
        if date_start is not None:
            folder_date = _extract_folder_date(folder, root_data_dir)
            if folder_date is None or folder_date < date_start:
                continue
        file_list_path: Path | None = None
        if file_list_name:
            candidate = folder / file_list_name
            if not candidate.exists() or not candidate.is_file():
                continue
            file_list_path = candidate
            file_count = _count_manifest_entries(candidate, time_start=time_start, time_end=time_end)
        else:
            file_count = _count_supported_files(folder, time_start=time_start, time_end=time_end)
        if file_count <= 0:
            continue
        rel = folder.relative_to(root_data_dir)
        out_dir = output_root / (str(rel) if str(rel) != "." else folder.name)
        jobs.append(
            FolderJob(
                folder=folder,
                output_dir=out_dir,
                file_count=file_count,
                file_list=file_list_path,
            )
        )
    return jobs


def build_single_command(
    *,
    single_script: Path,
    config: Path,
    data_folder: Path,
    output_dir: Path,
    save_every: int,
    start_index: int,
    end_index: int | None,
    workers: int | None,
    cc_enhance: str | None,
    refresh_file_index: bool,
    file_list: Path | None,
    time_start: str | None,
    time_end: str | None,
    forward_args: Sequence[str],
) -> list[str]:
    cmd = [
        sys.executable,
        str(single_script),
        "--config",
        str(config),
        "--data-folder",
        str(data_folder),
        "--output-dir",
        str(output_dir),
        "--save-every",
        str(int(save_every)),
        "--start-index",
        str(int(start_index)),
    ]
    if end_index is not None:
        cmd.extend(["--end-index", str(int(end_index))])
    if workers is not None:
        cmd.extend(["--workers", str(int(workers))])
    if cc_enhance is not None:
        cmd.extend(["--cc-enhance", str(cc_enhance)])
    if refresh_file_index:
        cmd.append("--refresh-file-index")
    if file_list is not None:
        cmd.extend(["--file-list", str(file_list)])
    if time_start is not None:
        cmd.extend(["--time-start", str(time_start)])
    if time_end is not None:
        cmd.extend(["--time-end", str(time_end)])

    # 任意额外参数透传给原脚本（例如 --tsi-mode, --tsi-shot-batch-size, --tsi-progress 等）
    cmd.extend(list(forward_args))
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch run caculate_cc_tsi_from_folder.py for all data subfolders."
    )
    parser.add_argument(
        "--root-data-dir",
        type=Path,
        default=Path("/Volumes/CSIM_LAB/DATA/DAS/JLU/JLU-2025-01-07"),
        help="根数据目录（遍历其子目录）。",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "cc_tsi_outputs_by_folder",
        help="批处理输出根目录（每个子目录会对应一个子输出目录）。",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config/cc_config.jsonc",
        help="单目录计算脚本使用的配置文件。",
    )
    parser.add_argument(
        "--single-script",
        type=Path,
        default=Path(__file__).resolve().parent / "caculate_cc_tsi_from_folder.py",
        help="单目录计算脚本路径。",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="递归扫描所有层级子目录（默认仅扫描 root 下一层）。",
    )
    parser.add_argument(
        "--file-list-name",
        type=str,
        default=None,
        help="若提供，则每个子目录从 <subfolder>/<file-list-name> 读取 manifest，而不是扫描数据文件目录。",
    )
    parser.add_argument(
        "--include-root",
        action="store_true",
        help="包含 root-data-dir 本身（若根目录直接有数据文件）。",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=5,
        help="透传到单目录脚本的 --save-every。",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="透传到单目录脚本的 --start-index。",
    )
    parser.add_argument(
        "--date-start",
        type=str,
        default=None,
        help="仅处理日期 >= YYYY-MM-DD 的子目录。默认按相对路径第一层目录名或子目录名解析日期。",
    )
    parser.add_argument(
        "--time-start",
        type=str,
        default=None,
        help="仅保留文件名时间 >= HH:MM 的文件。需要同时提供 --time-end。",
    )
    parser.add_argument(
        "--time-end",
        type=str,
        default=None,
        help="仅保留文件名时间 < HH:MM 的文件。需要同时提供 --time-start。",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=None,
        help="透传到单目录脚本的 --end-index。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="透传到单目录脚本的 --workers。",
    )
    parser.add_argument(
        "--cc-enhance",
        type=str,
        default=None,
        choices=["raw", "tsi"],
        help="透传到单目录脚本的 --cc-enhance。",
    )
    parser.add_argument(
        "--refresh-file-index",
        action="store_true",
        help="透传到单目录脚本，强制重建每个数据目录的文件索引缓存。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的命令，不实际运行。",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="任一子目录失败即停止（默认继续处理后续目录）。",
    )
    parser.add_argument(
        "--max-folders",
        type=int,
        default=None,
        help="最多处理前 N 个匹配子目录（调试用）。",
    )

    # parse_known_args: 未显式声明的参数将原样透传给单目录脚本
    args, forward_args = parser.parse_known_args()

    root_data_dir = args.root_data_dir.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    config = args.config.expanduser().resolve()
    single_script = args.single_script.expanduser().resolve()
    date_start = parse_date_yyyy_mm_dd(args.date_start, arg_name="--date-start") if args.date_start is not None else None
    time_start = parse_hour_minute(args.time_start, arg_name="--time-start") if args.time_start is not None else None
    time_end = parse_hour_minute(args.time_end, arg_name="--time-end") if args.time_end is not None else None

    if not root_data_dir.exists() or not root_data_dir.is_dir():
        raise ValueError(f"root-data-dir not found: {root_data_dir}")
    if not config.exists() or not config.is_file():
        raise ValueError(f"config not found: {config}")
    if not single_script.exists() or not single_script.is_file():
        raise ValueError(f"single-script not found: {single_script}")
    if args.save_every <= 0:
        raise ValueError("--save-every must be > 0")
    if args.start_index < 0:
        raise ValueError("--start-index must be >= 0")
    if args.end_index is not None and args.end_index < 0:
        raise ValueError("--end-index must be >= 0")
    if args.workers is not None and args.workers <= 0:
        raise ValueError("--workers must be > 0")

    jobs = discover_jobs(
        root_data_dir=root_data_dir,
        output_root=output_root,
        recursive=bool(args.recursive),
        include_root=bool(args.include_root),
        file_list_name=args.file_list_name,
        date_start=date_start,
        time_start=time_start,
        time_end=time_end,
    )
    if args.max_folders is not None:
        jobs = jobs[: max(int(args.max_folders), 0)]

    print(f"[INFO] root data dir : {root_data_dir}")
    print(f"[INFO] output root   : {output_root}")
    print(f"[INFO] config        : {config}")
    print(f"[INFO] single script : {single_script}")
    print(f"[INFO] recursive     : {bool(args.recursive)}")
    print(f"[INFO] file list name: {args.file_list_name}")
    if date_start is not None:
        print(f"[INFO] date start   : {date_start.isoformat()}")
    if time_start is not None and time_end is not None:
        print(f"[INFO] time window  : [{time_start.strftime('%H:%M')}, {time_end.strftime('%H:%M')})")
    print(f"[INFO] include root  : {bool(args.include_root)}")
    print(f"[INFO] matched dirs  : {len(jobs)}")
    if forward_args:
        print(f"[INFO] forwarded args: {' '.join(shlex.quote(a) for a in forward_args)}")

    if not jobs:
        print("[DONE] no subfolder contains supported DAS files.")
        return

    success = 0
    failed = 0
    skipped = 0

    for idx, job in enumerate(jobs, start=1):
        max_index = int(job.file_count) - 1
        if int(args.start_index) > max_index:
            skipped += 1
            print(
                f"[SKIP] {job.folder.name}: start-index={args.start_index} > max-index={max_index} "
                f"(files={job.file_count})"
            )
            continue
        effective_end_index = None
        if args.end_index is not None:
            effective_end_index = min(int(args.end_index), max_index)
            if int(args.end_index) != effective_end_index:
                print(
                    f"[INFO] clamp end-index for {job.folder.name}: "
                    f"{args.end_index} -> {effective_end_index} (files={job.file_count})"
                )

        cmd = build_single_command(
            single_script=single_script,
            config=config,
            data_folder=job.folder,
            output_dir=job.output_dir,
            save_every=int(args.save_every),
            start_index=int(args.start_index),
            end_index=effective_end_index,
            workers=args.workers,
            cc_enhance=args.cc_enhance,
            refresh_file_index=bool(args.refresh_file_index),
            file_list=job.file_list,
            time_start=args.time_start,
            time_end=args.time_end,
            forward_args=forward_args,
        )
        print(
            f"[{idx}/{len(jobs)}] folder={job.folder} files={job.file_count} "
            f"-> output={job.output_dir}"
        )
        print(f"[CMD] {' '.join(shlex.quote(x) for x in cmd)}")

        if args.dry_run:
            skipped += 1
            continue

        job.output_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, check=False)
        if proc.returncode == 0:
            success += 1
            print(f"[OK] {job.folder.name}")
        else:
            failed += 1
            print(f"[FAILED] {job.folder.name}, returncode={proc.returncode}")
            if args.stop_on_error:
                break

    print(
        f"[DONE] total={len(jobs)} success={success} failed={failed} "
        f"dryrun_or_skipped={skipped}"
    )


if __name__ == "__main__":
    main()
