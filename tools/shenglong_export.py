"""盛隆废钢端到端 CLI：登录 → 取数 → 算 → 出 xlsx

用法：
  /opt/anaconda3/bin/python tools/shenglong_export.py --start 2026-04-22 --end 2026-04-22
  /opt/anaconda3/bin/python tools/shenglong_export.py --start 2026-04-20 --end 2026-04-22

输出：
  downloads/shenglong/<日期或区间>/盛隆赛迪废钢判级_<日期>.xlsx

试运行阶段不下载错判图像（与镔鑫 scrap_export.py 的关键差异）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.shenglong.client import ShenglongClient
from agent.shenglong.excel_writer import write_stats_xlsx
from config.settings import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="盛隆废钢检判统计端到端导出")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="打印 DEBUG 日志"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = settings.shenglong

    with ShenglongClient() as cli:
        cli.login()
        stats_list = cli.build_range_stats(args.start, args.end)

        if args.start == args.end:
            out_root = Path("downloads/shenglong") / args.start
            xlsx_name = f"盛隆赛迪废钢判级_{args.start}.xlsx"
        else:
            out_root = Path("downloads/shenglong") / f"{args.start}_{args.end}"
            xlsx_name = f"盛隆赛迪废钢判级_{args.start}_{args.end}.xlsx"
        out_root.mkdir(parents=True, exist_ok=True)
        xlsx_path = out_root / xlsx_name
        write_stats_xlsx(
            stats_list,
            xlsx_path,
            target_recognition_rate=cfg.target_recognition_rate,
            target_deduction_compliance_rate=cfg.target_deduction_compliance_rate,
        )

        print()
        print("=" * 70)
        for stats in stats_list:
            print(
                stats.summary_text(
                    target_recognition_rate=cfg.target_recognition_rate,
                    target_deduction_compliance_rate=cfg.target_deduction_compliance_rate,
                )
            )
            print("-" * 70)
        print(f"\nxlsx 报表 → {xlsx_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
