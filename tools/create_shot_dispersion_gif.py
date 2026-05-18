#!/usr/bin/env python3
"""
从频散输出目录树中收集同一炮号的 `shot_XXXX_dispersion.png`，按子目录顺序合成为 GIF。

用途:
1) 递归扫描一个根目录下所有匹配 `shot_{shot:04d}_dispersion.png` 的图片。
2) 按图片相对根目录路径排序，确保不同日期/批次按稳定顺序进入 GIF。
3) 将所有图片统一到第一张图的尺寸后保存为一个 GIF 动图。
4) 适合把同一炮号在多个日期、多个 `restack_*` 子目录中的频散图串起来做时序浏览。

典型输入示例:
- `outputs/dispersion_outputs_ChangBai/2025-04-28/restack_0001_nall_idx0000-0019/shot_0179_dispersion.png`
- `outputs/dispersion_outputs_ChangBai/2025-04-29/restack_0001_nall_idx0000-0019/shot_0179_dispersion.png`

运行方式:
1) 为第 179 炮生成 GIF:
   `uv run python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion_outputs_ChangBai \
      --shot-index 179 \
      --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif`

2) 指定每帧停留 500 ms:
   `uv run python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion_outputs_ChangBai \
      --shot-index 179 \
      --duration-ms 500 \
      --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion_fast.gif`

3) 只预览将被收集的文件，不实际输出 GIF:
   `uv run python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion_outputs_ChangBai \
      --shot-index 179 \
      --dry-run`

输入:
- `--input-root`: 递归扫描的频散输出根目录。
- `--shot-index`: 目标炮号，将匹配 `shot_XXXX_dispersion.png`。

输出:
- `--output-gif`: 输出 GIF 文件。
- 终端会打印命中的文件总数和每一帧对应的相对路径。

结果说明:
- 若图片尺寸不一致，会自动缩放到第一张图的尺寸。
- 本脚本只合成 GIF，不修改原始 PNG。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect one shot index across subfolders and build a GIF."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/dispersion_outputs_ChangBai",
        help="递归扫描的频散输出根目录。",
    )
    parser.add_argument(
        "--shot-index",
        type=int,
        required=True,
        help="目标炮号，例如 179。",
    )
    parser.add_argument(
        "--output-gif",
        type=Path,
        default=None,
        help="输出 GIF 路径。默认写到 input-root 下的 shot_XXXX_dispersion.gif。",
    )
    parser.add_argument(
        "--duration-ms",
        type=int,
        default=800,
        help="每帧停留时间，单位毫秒。",
    )
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        help="GIF loop 参数。0 表示无限循环。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印命中的图片列表，不实际写 GIF。",
    )
    return parser.parse_args()


def discover_pngs(input_root: Path, shot_index: int) -> list[Path]:
    pattern = f"shot_{int(shot_index):04d}_dispersion.png"
    files = sorted(
        p for p in input_root.rglob(pattern) if p.is_file()
    )
    return files


def build_gif(
    png_files: list[Path],
    output_gif: Path,
    *,
    duration_ms: int,
    loop: int,
) -> None:
    from PIL import Image

    frames: list[Image.Image] = []
    first_size: tuple[int, int] | None = None

    for path in png_files:
        with Image.open(path) as img:
            frame = img.convert("RGBA")
            if first_size is None:
                first_size = frame.size
            elif frame.size != first_size:
                frame = frame.resize(first_size, Image.Resampling.LANCZOS)
            frames.append(frame.copy())

    if not frames:
        raise ValueError("No frames collected for GIF.")

    output_gif.parent.mkdir(parents=True, exist_ok=True)
    first = frames[0]
    rest = frames[1:]
    first.save(
        output_gif,
        save_all=True,
        append_images=rest,
        duration=int(duration_ms),
        loop=int(loop),
        optimize=False,
    )


def main() -> None:
    args = parse_args()
    input_root = args.input_root.expanduser().resolve()
    if not input_root.exists() or not input_root.is_dir():
        raise ValueError(f"input-root not found: {input_root}")
    if args.shot_index < 0:
        raise ValueError("--shot-index must be >= 0")
    if args.duration_ms <= 0:
        raise ValueError("--duration-ms must be > 0")
    if args.loop < 0:
        raise ValueError("--loop must be >= 0")

    output_gif = (
        args.output_gif.expanduser().resolve()
        if args.output_gif is not None
        else input_root / f"shot_{int(args.shot_index):04d}_dispersion.gif"
    )

    png_files = discover_pngs(input_root, args.shot_index)
    print(f"[INFO] input root : {input_root}")
    print(f"[INFO] shot index : {args.shot_index}")
    print(f"[INFO] frames     : {len(png_files)}")
    print(f"[INFO] output gif : {output_gif}")
    for idx, path in enumerate(png_files, start=1):
        print(f"[{idx:04d}] {path.relative_to(input_root)}")

    if not png_files:
        raise FileNotFoundError(
            f"No PNG matched shot_{int(args.shot_index):04d}_dispersion.png under {input_root}"
        )

    if args.dry_run:
        print("[DONE] dry-run only; GIF not written.")
        return

    build_gif(
        png_files,
        output_gif,
        duration_ms=int(args.duration_ms),
        loop=int(args.loop),
    )
    print(f"[DONE] gif saved: {output_gif}")


if __name__ == "__main__":
    main()
