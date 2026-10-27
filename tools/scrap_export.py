"""废钢端到端 CLI：登录→取数→算→出 xlsx→下错判渲染图

用法：
  /opt/anaconda3/bin/python tools/scrap_export.py --start 2026-04-15 --end 2026-04-15
  /opt/anaconda3/bin/python tools/scrap_export.py --start 2026-04-13 --end 2026-04-15
  /opt/anaconda3/bin/python tools/scrap_export.py --start 2026-04-15 --end 2026-04-15 --no-images

输出：
  downloads/scrap/<日期或区间>/赛迪废钢判级_<日期>.xlsx
  downloads/scrap/<日期或区间>/<日期>/<车牌>_工位X_N.jpg
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scrap.client import ScrapClient
from agent.scrap.excel_writer import write_stats_xlsx
from config.settings import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="废钢检判统计端到端导出")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="只导 xlsx，不下载错判渲染图",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="打印 DEBUG 日志"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = settings.scrap

    with ScrapClient() as cli:
        cli.login()
        stats_list = cli.build_range_stats(args.start, args.end)

        if args.start == args.end:
            out_root = Path("downloads/scrap") / args.start
            xlsx_name = f"赛迪废钢判级_{args.start}.xlsx"
        else:
            out_root = Path("downloads/scrap") / f"{args.start}_{args.end}"
            xlsx_name = f"赛迪废钢判级_{args.start}_{args.end}.xlsx"
        out_root.mkdir(parents=True, exist_ok=True)
        xlsx_path = out_root / xlsx_name
        write_stats_xlsx(stats_list, xlsx_path)

        print()
        print("=" * 70)
        for stats in stats_list:
            print(
                stats.summary_text(
                    target_accuracy=cfg.target_accuracy,
                    target_avg_error_rate=cfg.target_avg_error_rate,
                    target_weight_diff_kg=cfg.target_weight_diff_kg,
                    target_weight_ratio_lower=cfg.target_weight_ratio_lower,
                    target_weight_ratio_upper=cfg.target_weight_ratio_upper,
                )
            )
            print("-" * 70)
        print(f"\nxlsx 报表 → {xlsx_path.resolve()}")

        if args.no_images:
            return 0

        total = 0
        for stats in stats_list:
            day_dir = out_root / stats.date
            day_count = 0
            for truck in stats.trucks:
                if truck.main_same is False and truck.error_render_images:
                    for i, url in enumerate(truck.error_render_images, start=1):
                        suffix = Path(url.split("?")[0]).suffix or ".jpg"
                        fname = (
                            f"{truck.car_number}_工位{truck.station_number}"
                            f"_{i}{suffix}"
                        )
                        fpath = day_dir / fname
                        if cli.download_image(url, fpath):
                            day_count += 1
                            total += 1
            if day_count:
                print(f"错判渲染图 {stats.date}: {day_count} 张 → {day_dir.resolve()}")
        print(f"\n合计下载错判渲染图 {total} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
