#!/usr/bin/env python3
"""
将频散输出图片合成为 GIF。

用途:
1) `--all-images` 模式: 收集一个目录下的全部图片，按文件名顺序合成为 GIF。
2) `--shot-index` 模式: 递归扫描一个根目录下所有匹配
   `shot_{shot:04d}_dispersion.png` 的图片，合成为同一炮号的时序 GIF。
3) 将所有图片统一到第一张图的尺寸后保存为一个 GIF 动图。

典型输入示例:
- `outputs/dispersion/2025-03-23/shot_0001_dispersion.png`
- `outputs/dispersion/2025-03-23/shot_0002_dispersion.png`
- `outputs/dispersion_outputs_ChangBai/2025-04-28/restack_0001_nall_idx0000-0019/shot_0179_dispersion.png`
- `outputs/dispersion_outputs_ChangBai/2025-04-29/restack_0001_nall_idx0000-0019/shot_0179_dispersion.png`

运行方式:
1) 将 `outputs/dispersion/2025-03-23` 下所有图片合成为 GIF:
   `uv run --with pillow python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion/2025-03-23 \
      --all-images \
      --output-gif ./outputs/dispersion/2025-03-23/dispersion_all.gif`

2) 递归收集子目录中的所有图片:
   `uv run --with pillow python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion/2025-03-23 \
      --all-images \
      --recursive \
      --output-gif ./outputs/dispersion/2025-03-23/dispersion_all_recursive.gif`

3) 为第 179 炮生成 GIF:
   `uv run --with pillow python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion_outputs_ChangBai \
      --shot-index 179 \
      --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion.gif`

4) 指定每帧停留 500 ms:
   `uv run --with pillow python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion_outputs_ChangBai \
      --shot-index 179 \
      --duration-ms 500 \
      --output-gif ./outputs/dispersion_outputs_ChangBai/shot_0179_dispersion_fast.gif`

5) 只预览将被收集的文件，不实际输出 GIF:
   `uv run python tools/create_shot_dispersion_gif.py \
      --input-root ./outputs/dispersion/2025-03-23 \
      --all-images \
      --dry-run`

输入:
- `--input-root`: 图片目录或频散输出根目录。
- `--all-images`: 收集 `--input-root` 下全部图片。
- `--recursive`: 在 `--all-images` 模式下递归收集子目录图片。
- `--shot-index`: 目标炮号，将递归匹配 `shot_XXXX_dispersion.png`。

输出:
- `--output-gif`: 输出 GIF 文件。
- 终端会打印命中的文件总数和每一帧对应的相对路径。

结果说明:
- 若图片尺寸不一致，会自动缩放到第一张图的尺寸。
- 本脚本只合成 GIF，不修改原始 PNG。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a GIF from dispersion images."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/dispersion/2025-03-23",
        help="图片目录或递归扫描的频散输出根目录。",
    )
    parser.add_argument(
        "--all-images",
        action="store_true",
        help="收集 input-root 下所有图片。若不加 --recursive，则只收集当前目录直属图片。",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="在 --all-images 模式下递归收集子目录图片。",
    )
    parser.add_argument(
        "--shot-index",
        type=int,
        default=None,
        help="目标炮号，例如 179。设置后递归匹配 shot_XXXX_dispersion.png。",
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


def natural_sort_key(path: Path) -> tuple[str | int, ...]:
    relative = path.as_posix()
    parts = re.split(r"(\d+)", relative)
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def discover_images(input_root: Path, *, recursive: bool) -> list[Path]:
    iterator = input_root.rglob("*") if recursive else input_root.iterdir()
    return sorted(
        (
            path
            for path in iterator
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=natural_sort_key,
    )


def discover_pngs(input_root: Path, shot_index: int) -> list[Path]:
    pattern = f"shot_{int(shot_index):04d}_dispersion.png"
    files = sorted(
        (p for p in input_root.rglob(pattern) if p.is_file()),
        key=natural_sort_key,
    )
    return files


def build_gif(
    png_files: list[Path],
    output_gif: Path,
    *,
    duration_ms: int,
    loop: int,
) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "This script requires Pillow. Run with "
            "`uv run --with pillow python tools/create_shot_dispersion_gif.py ...` "
            "or install Pillow into the project Python environment."
        ) from exc

    frames: list[Image.Image] = []
    first_size: tuple[int, int] | None = None

    for path in png_files:
        with Image.open(path) as img:
            frame = img.convert("RGBA")
            background = Image.new("RGBA", frame.size, (255, 255, 255, 255))
            frame = Image.alpha_composite(background, frame).convert("RGB")
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
    if args.shot_index is not None and args.shot_index < 0:
        raise ValueError("--shot-index must be >= 0")
    if args.duration_ms <= 0:
        raise ValueError("--duration-ms must be > 0")
    if args.loop < 0:
        raise ValueError("--loop must be >= 0")
    if args.all_images and args.shot_index is not None:
        raise ValueError("--all-images and --shot-index cannot be used together")
    if not args.all_images and args.shot_index is None:
        raise ValueError("Set either --all-images or --shot-index")

    if args.output_gif is not None:
        output_gif = args.output_gif.expanduser().resolve()
    elif args.all_images:
        output_gif = input_root / "dispersion_all.gif"
    else:
        output_gif = input_root / f"shot_{int(args.shot_index):04d}_dispersion.gif"

    if args.all_images:
        png_files = discover_images(input_root, recursive=bool(args.recursive))
        mode_label = "all images"
    else:
        png_files = discover_pngs(input_root, int(args.shot_index))
        mode_label = f"shot {int(args.shot_index):04d}"

    print(f"[INFO] input root : {input_root}")
    print(f"[INFO] mode       : {mode_label}")
    print(f"[INFO] recursive  : {bool(args.recursive) if args.all_images else True}")
    print(f"[INFO] frames     : {len(png_files)}")
    print(f"[INFO] output gif : {output_gif}")
    for idx, path in enumerate(png_files, start=1):
        print(f"[{idx:04d}] {path.relative_to(input_root)}")

    if not png_files:
        if args.all_images:
            raise FileNotFoundError(f"No image files found under {input_root}")
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
