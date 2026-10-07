#!/usr/bin/env python3
"""Write a source-counted summary from completed seasonal diagnostic tables."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def fmt(value: float) -> str:
    return f"{value:.3g}" if np.isfinite(value) else "—"


def write_report(cfg: dict[str, Any], manifest: dict[str, Any]) -> Path:
    out = Path(cfg["output_dir"])
    raw = read_csv(out / "raw_per_file_position.csv")
    monthly = read_csv(out / "monthly_summary.csv")
    failures = read_csv(out / "failures.csv")
    raw_expected = sum(entry["raw_count"] for entry in manifest["days"].values())
    raw_files = len({row["file"] for row in raw})
    complete = raw_files == raw_expected and not failures
    full_raw = raw_files == raw_expected and int(cfg["raw_step_minutes"]) == 1
    days = sorted(manifest["days"])
    lines = [
        "# 长白山多位置季节诊断报告",
        "",
        f"状态：{'全部预期文件处理完成' if complete else '部分文件未完成，见失败清单与运行日志'}。",
        "",
        f"- 实际分析日期：{days[0]} 至 {days[-1]}（{len(days)} 天）；每日 {cfg['time_start']}–{cfg['time_end']}。",
        f"- 请求位置：{', '.join(f'{x:g}' for x in cfg['positions_m'])} m。",
        f"- 原始 DAT：{raw_files}/{raw_expected} 个有效文件；失败记录：{len(failures)}。",
        f"- 原始 DAT 抽样间隔：{cfg['raw_step_minutes']} 分钟；{'分析时段内全部分钟文件' if full_raw else '此结果仍为抽样或未完成数据'}。",
        "- 月值为每日中位数的中位数，位置之间等权；表中 `n_days` 为有效日期数。",
        "",
        "## 月度指标（所有请求位置的中位数）",
        "",
        "| 指标 | 3 月 | 4 月 | 5 月 | 6 月 | 各月有效天数 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    if manifest.get("first_started_at") and manifest.get("last_finished_at"):
        duration_h = (
            datetime.fromisoformat(manifest["last_finished_at"])
            - datetime.fromisoformat(manifest["first_started_at"])
        ).total_seconds() / 3600
        lines.insert(
            9,
            f"- 从首次运行到本次完成的墙钟时间：{duration_h:.2f} 小时（包含中断与续算）。",
        )
    metrics = [
        ("raw", "both", "fk_ratio", "原始局部 FK 目标/背景比"),
        ("raw", "both", "rms", "原始 RMS"),
        ("raw", "both", "target_background_power", "原始目标/频带外功率比"),
    ]
    for source, branch, metric, label in metrics:
        subset = [
            row
            for row in monthly
            if row["source"] == source
            and row["branch"] == branch
            and row["metric"] == metric
        ]
        cells = []
        counts = []
        for month in ("2025-03", "2025-04", "2025-05", "2025-06"):
            rows = [row for row in subset if row["month"] == month]
            values = [
                float(row["median"])
                for row in rows
                if np.isfinite(float(row["median"]))
            ]
            cells.append(fmt(float(np.median(values))) if values else "—")
            counts.append(str(max((int(row["n_days"]) for row in rows), default=0)))
        lines.append(f"| {label} | {' | '.join(cells)} | {'/'.join(counts)} |")
    lines += [
        "",
        "## 证据范围与图像核对",
        "",
        "- 原始 RMS、频谱、时频和 FK 只有在抽样间隔为 1 分钟且完成数等于预期数时才可视为分析时段的全量结果。",
        "- 这些原始数据质量指标的季节变化只说明相关，不能单凭这些指标确定车辆、环境噪声或处理参数的因果作用。",
        "- [季节汇总图与位置热图](figures)、[春夏原始数据对比图](figures/comparison)、[逐日原始诊断图](figures/daily)可结合同一坐标与色标核对。",
        "- 逐文件数值见 [raw_per_file_position.csv](raw_per_file_position.csv)；每日和每月四分位范围见相应汇总 CSV。",
        "",
    ]
    path = out / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
