"""永锋烧结矿准确率端到端 CLI：取数 → 计算 → 出 JSON / xlsx

用法：
  python tools/yongfeng_export.py --start 2026-07-31 00:00:00 --end 2026-08-06 23:59:59
  python tools/yongfeng_export.py --start 2026-05-15 00:00:00 --end 2026-05-15 23:59:59 --output downloads/yongfeng/烧结矿颗粒度准确率统计_2026-05-15_2026-05-15.xlsx

默认读取 config/settings.py 中的永锋地址配置，避免在 README 里重复写一堆 URL。
JSON 中间结果写入 agent/yongfeng/output/，Excel 默认写入 downloads/yongfeng/。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.yongfeng.main import run_report
from config.settings import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="永锋烧结矿准确率报表导出")
    parser.add_argument("--start", nargs="+", required=True, help="起始时间 YYYY-MM-DD HH:MM:SS（不加引号也可）")
    parser.add_argument("--end", nargs="+", required=True, help="结束时间 YYYY-MM-DD HH:MM:SS（不加引号也可）")
    parser.add_argument("--mat-code-1", default="12031001", help="1# 物料编码")
    parser.add_argument("--mat-code-2", default="12031002", help="2# 物料编码")
    parser.add_argument("--output", help="Excel 输出路径（默认写入 downloads/yongfeng/）")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印 DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    start_time = " ".join(args.start).strip()
    end_time = " ".join(args.end).strip()

    result = run_report(
        analysis_base_url=str(settings.yongfeng.analysis_base_url).strip(),
        visual_1_base_url=str(settings.yongfeng.visual_1_base_url).strip(),
        visual_2_base_url=str(settings.yongfeng.visual_2_base_url).strip(),
        start_time=start_time,
        end_time=end_time,
        mat_code_1=args.mat_code_1,
        mat_code_2=args.mat_code_2,
        analysis_token=settings.yongfeng.analysis_token,
        api_code=settings.yongfeng.api_code,
        output=args.output,
        verbose=args.verbose,
    )

    print()
    print("=" * 70)
    print(f"Excel 报表 → {Path(result['output_path']).resolve()}")
    print(f"原始 JSON → {Path(result['raw_path']).resolve()}")
    print(f"计算 JSON → {Path(result['computed_path']).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
