#!/usr/bin/env python3
"""将多个 MAT 互相关结果转换成可按时间片读取的 HDF5 数据集。

用途
----
每一个匹配的 MAT 文件都被视为一个独立时间片，输出一个 HDF5 文件和一个
``manifest.json``。HDF5 中的 ``/cc`` 数据形状为 ``(nshot, nt, noffset)``，
使用按炮分块，方便 GUI 固定炮号跨时间读取。

典型用法（项目根目录）
----------------------
使用项目自己的 Python 环境和 uv：

    cd /Users/zhangzhiyu/MyProjects/dasQt
    uv run --python .venv/bin/python \\
        examples/03_ccf/scripts/convert_cc_mat_to_timeslice_h5.py \\
        --input-root /path/to/mat_root \\
        --output-root /path/to/ccf_timeslices \\
        --glob 'restack_*.mat' \\
        --compression gzip

输出
----
``output-root`` 下保留 MAT 的相对目录结构，每个 MAT 生成同名 ``.h5``；
manifest 保存时间标签、形状、几何元数据和 HDF5 相对路径。源 MAT 不会被
修改或删除。脚本会持续向终端输出扫描、转换和清单写入进度。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True, help="MAT 根目录")
    parser.add_argument("--output-root", type=Path, required=True, help="HDF5 输出根目录")
    parser.add_argument("--manifest", type=Path, default=None, help="清单路径，默认 <output-root>/manifest.json")
    parser.add_argument("--glob", default="restack_*.mat", help="递归匹配模式，默认 restack_*.mat")
    parser.add_argument("--compression", choices=("gzip", "lzf", "none"), default="gzip")
    parser.add_argument("--gzip-level", type=int, default=4, help="gzip 压缩级别 0-9")
    parser.add_argument("--max-slices", type=int, default=None, help="只转换前 N 个时间片，便于试跑")
    parser.add_argument(
        "--allow-metadata-mismatch",
        action="store_true",
        help="允许 dt/dx/dshot/xmin/xmax/offset 在时间片之间不一致",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 HDF5 文件")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并打印计划，不写文件")
    return parser


def main() -> int:
    from dasqt.features.dispersion.backend.components.time_lapse_ccf import (
        convert_mat_tree,
    )

    args = build_parser().parse_args()
    result = convert_mat_tree(
        args.input_root,
        args.output_root,
        manifest_path=args.manifest,
        pattern=args.glob,
        compression=args.compression,
        gzip_level=args.gzip_level,
        max_slices=args.max_slices,
        allow_metadata_mismatch=args.allow_metadata_mismatch,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    if result.get("dry_run"):
        print("[DONE] dry run; nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
