"""Main entry for accuracy report pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    PACKAGE_ROOT = Path(__file__).resolve().parent
    PACKAGE_PARENT = PACKAGE_ROOT.parent
    if str(PACKAGE_PARENT) not in sys.path:
        sys.path.insert(0, str(PACKAGE_PARENT))
    from yongfeng.calculator import compute_accuracy
    from yongfeng.client import HttpConfig, fetch_raw_data
    from yongfeng.excel_writer import write_report
else:
    from .calculator import compute_accuracy
    from .client import HttpConfig, fetch_raw_data
    from .excel_writer import write_report

ROOT = Path(__file__).resolve().parent.parent.parent
YONGFENG_ROOT = ROOT / "agent" / "yongfeng"
OUTPUT_DIR = YONGFENG_ROOT / "output"
DOWNLOAD_DIR = ROOT / "downloads" / "yongfeng"
RAW_PATH = OUTPUT_DIR / "accuracy_report_raw.json"
COMPUTED_PATH = OUTPUT_DIR / "accuracy_report_computed.json"

LOGGER = logging.getLogger("accuracy_main")


def run_report(
    *,
    analysis_base_url: str,
    visual_1_base_url: str,
    visual_2_base_url: str,
    start_time: str,
    end_time: str,
    mat_code_1: str = "12031001",
    mat_code_2: str = "12031002",
    analysis_cookie: str | None = None,
    visual_1_cookie: str | None = None,
    visual_2_cookie: str | None = None,
    analysis_token: str | None = None,
    api_code: str | None = None,
    output: str | None = None,
    verbose: bool = False,
) -> dict:
    required = {
        "analysis_base_url": analysis_base_url,
        "visual_1_base_url": visual_1_base_url,
        "visual_2_base_url": visual_2_base_url,
        "start_time": start_time,
        "end_time": end_time,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(f"缺少必要参数: {', '.join(missing)}")

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = HttpConfig(
        analysis_base_url=analysis_base_url,
        visual_1_base_url=visual_1_base_url,
        visual_2_base_url=visual_2_base_url,
        analysis_cookie=analysis_cookie,
        visual_1_cookie=visual_1_cookie,
        visual_2_cookie=visual_2_cookie,
        analysis_token=analysis_token,
        api_code=api_code,
    )

    LOGGER.info("获取原始数据...")
    raw = fetch_raw_data(cfg, mat_code_1, mat_code_2, start_time, end_time)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    LOGGER.info("原始数据已写入 %s", RAW_PATH)

    LOGGER.info("计算准确率...")
    computed = compute_accuracy(raw)
    COMPUTED_PATH.write_text(json.dumps(computed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    LOGGER.info("计算结果已写入 %s", COMPUTED_PATH)

    out_path = Path(output) if output else DOWNLOAD_DIR / f"烧结矿颗粒度准确率统计_{start_time[:10]}_{end_time[:10]}.xlsx"
    LOGGER.info("输出报表 %s...", out_path)
    write_report(out_path, computed)
    LOGGER.info("完成")
    return {
        "output_path": str(out_path),
        "raw_path": str(RAW_PATH),
        "computed_path": str(COMPUTED_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成人工 vs 视觉准确率报表")
    parser.add_argument("--analysis-base-url", required=True)
    parser.add_argument("--visual-1-base-url", required=True)
    parser.add_argument("--visual-2-base-url", required=True)
    parser.add_argument("--mat-code-1", default="12031001")
    parser.add_argument("--mat-code-2", default="12031002")
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--analysis-cookie")
    parser.add_argument("--visual-1-cookie")
    parser.add_argument("--visual-2-cookie")
    parser.add_argument("--analysis-token")
    parser.add_argument("--api-code")
    parser.add_argument("--output")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    run_report(
        analysis_base_url=args.analysis_base_url,
        visual_1_base_url=args.visual_1_base_url,
        visual_2_base_url=args.visual_2_base_url,
        start_time=args.start_time,
        end_time=args.end_time,
        mat_code_1=args.mat_code_1,
        mat_code_2=args.mat_code_2,
        analysis_cookie=args.analysis_cookie,
        visual_1_cookie=args.visual_1_cookie,
        visual_2_cookie=args.visual_2_cookie,
        analysis_token=args.analysis_token,
        api_code=args.api_code,
        output=args.output,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
