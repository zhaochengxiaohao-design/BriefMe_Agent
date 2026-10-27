"""盛隆废钢多周期主表 CLI 导出工具。

把多个统计周期累积到一个 xlsx：Sheet1 多个 14 行块依次往下，环比自动链；
Sheet2 每周期一段（深蓝段标题 + 三级表头 + 单车明细 + 期间汇总）。

用法（周期按时间升序）：
    python tools/shenglong_master_export.py \
        2026-04-14:2026-04-22 \
        2026-04-23:2026-04-29

也可以用空格分隔：
    python tools/shenglong_master_export.py "2026-04-14 2026-04-22" ...
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.shenglong.calculator import (
    aggregate_period,
    aggregate_period_heavy_normalized,
    to_heavy_normalized_view,
)
from agent.shenglong.client import ShenglongClient
from agent.shenglong.excel_writer import write_master_xlsx
from config.settings import settings

logger = logging.getLogger(__name__)


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_cycle(spec: str) -> tuple[str, str, bool]:
    """解析一段日期，支持：
        ``YYYY-MM-DD:YYYY-MM-DD``
        ``YYYY-MM-DD:YYYY-MM-DD+`` （后缀 + 表示与下一段合并成同一统计周期）
    返回 (start, end, merge_with_next)
    """
    merge = spec.endswith("+")
    body = spec[:-1] if merge else spec
    matches = _DATE_RE.findall(body)
    if len(matches) != 2:
        raise ValueError(f"无法解析周期: {spec!r}（需要 2 个 YYYY-MM-DD）")
    return matches[0], matches[1], merge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="盛隆废钢多周期主表导出"
    )
    parser.add_argument(
        "cycles",
        nargs="+",
        help=(
            "周期，按时间升序，如 2026-04-14:2026-04-22 2026-04-23:2026-04-29。"
            "末尾加 '+' 表示与下一段合并成同一统计周期，例：2026-04-30:2026-05-06+ "
            "2026-05-07:2026-05-13 → 2 段合并成 1 个周期"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 xlsx 路径（默认 downloads/shenglong/master/...）",
    )
    parser.add_argument(
        "--target-recognition",
        type=float,
        default=None,
        help=f"识别率目标值（默认 {settings.shenglong.target_recognition_rate}）",
    )
    parser.add_argument(
        "--target-deduction",
        type=float,
        default=None,
        help=f"扣杂符合率目标值（默认 {settings.shenglong.target_deduction_compliance_rate}）",
    )
    parser.add_argument(
        "--heavy-normalized",
        action="store_true",
        help=(
            "启用重废1/2/3归一化准确率口径：只看重废1/2/3，"
            "先把这三类占比归一化到 100%%，再判断主重废类和差异≤10%%"
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    seg_specs = [parse_cycle(c) for c in args.cycles]
    if not seg_specs:
        print("[错] 至少需要 1 个周期")
        return 1

    # ---- 按 merge 链分组成 effective groups ----
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for sd, ed, merge in seg_specs:
        current.append((sd, ed))
        if not merge:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    client = ShenglongClient()
    cycles_data = []
    aggregate_func = (
        aggregate_period_heavy_normalized
        if args.heavy_normalized
        else aggregate_period
    )
    for group in groups:
        merged_by_date = {}
        for sd, ed in group:
            print(f"[取数] {sd} ~ {ed} ...")
            stats_list = client.build_range_stats(sd, ed)
            for day in stats_list:
                merged_by_date[day.date] = day
        merged_stats = [merged_by_date[d] for d in sorted(merged_by_date)]
        gs, ge = group[0][0], group[-1][1]
        period = aggregate_func(merged_stats, gs, ge)
        report_stats = (
            to_heavy_normalized_view(merged_stats)
            if args.heavy_normalized
            else merged_stats
        )
        if len(group) > 1:
            seg_labels = " + ".join(f"{s}~{e}" for s, e in group)
            period.cycle_label = (
                f"{gs.replace('-', '.')} 至 {ge.replace('-', '.')}"
                f"（合并：{seg_labels}）"
            )
        car_count = sum(len(d.trucks) for d in merged_stats)
        print(
            f"  · 周期 {gs}~{ge}{' [合并]' if len(group) > 1 else ''}: "
            f"检判 {car_count} 车 / 可评估 {period.judgable_trucks} / "
            f"识别率 {period.recognition_rate_pct:.2f}% / "
            f"扣重符合率 {period.sheet_deduction_compliance_rate_pct:.2f}%"
        )
        cycles_data.append((report_stats, period))

    if args.out:
        out_path = args.out
    else:
        first_start = seg_specs[0][0]
        last_end = seg_specs[-1][1]
        out_path = (
            ROOT
            / "downloads"
            / "shenglong"
            / "master"
            / (
                f"盛隆赛迪废钢判级_主表"
                f"{'_重废归一化' if args.heavy_normalized else ''}"
                f"_{first_start}_{last_end}.xlsx"
            )
        )

    cfg = settings.shenglong
    target_r = (
        args.target_recognition
        if args.target_recognition is not None
        else cfg.target_recognition_rate
    )
    target_c = (
        args.target_deduction
        if args.target_deduction is not None
        else cfg.target_deduction_compliance_rate
    )

    write_master_xlsx(
        cycles_data,
        out_path,
        target_recognition_rate=target_r,
        target_deduction_compliance_rate=target_c,
    )
    print(f"\n[完成] 主表已生成（{len(cycles_data)} 个周期）：")
    print(f"  {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
