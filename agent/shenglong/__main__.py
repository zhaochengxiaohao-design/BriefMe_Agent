"""盛隆废钢检判命令行入口。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent.shenglong.downloader import download_images_by_date_range


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.shenglong",
        description="盛隆废钢智能检判图片下载：按日期范围拉取列表、详情并下载原图。",
    )
    parser.add_argument("--start", required=True, help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出目录，默认当前目录下的 shenglong_images",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="开启 DEBUG 日志",
    )
    parser.add_argument(
        "--no-manual",
        action="store_true",
        help="跳过无人工判级的记录",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    def progress(done: int, total: int, message: str) -> None:
        print(f"[{done}/{total}] {message}")

    result = download_images_by_date_range(
        args.start,
        args.end,
        output_dir=args.output or (Path.cwd() / "shenglong_images"),
        progress_callback=progress,
        include_missing_manual=not args.no_manual,
    )

    print("\n==================== 下载结果 ====================")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=================================================\n")

    return 1 if result.get("failed", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
