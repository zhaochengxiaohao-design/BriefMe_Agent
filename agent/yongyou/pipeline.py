"""串联登录 → 列表 → 点击 → 下载的顶层流程（仅图片下载，无 Excel 导出）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
import urllib3

from .auth import ensure_logged_in
from .config import YYSettings, STORAGE_STATE_FILE
from .downloader import DownloadResult, download_truck_images, save_pie_charts
from .page_actions import (
    ALLOWED_GRADES,
    apply_date_filter,
    wait_for_table,
    get_table_rows,
    get_grading_col_index,
    get_row_grade,
    is_allowed_grade,
    click_row_and_extract,
    extract_pie_charts,
    go_to_next_page,
    wait_spinner_gone,
    get_row_cell_data,
)

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class YYReport:
    """用友下载汇总"""
    date: str
    processed: int = 0
    total_trucks: int = 0
    saved_files: int = 0
    skipped_existing: int = 0
    failed_files: int = 0
    skipped_grade: int = 0
    skipped_no_detail: int = 0
    excel_path: str = ""


def _do_download(
    settings: YYSettings,
    from_date: str,
    to_date: str,
) -> YYReport:
    """执行下载流程并返回报告。"""
    logger.info("=" * 60)
    logger.info("用友智能判级系统 - 检判结果图片自动下载")
    logger.info("=" * 60)
    logger.info("  服务地址: %s", settings.base_url)
    logger.info("  下载目录: %s", settings.download_dir)
    logger.info("  目标料型: %s", ", ".join(sorted(ALLOWED_GRADES)))
    if from_date:
        logger.info("  日期范围: %s ~ %s", from_date, to_date or from_date)

    report = YYReport(date=from_date or "all")
    skip_existing = True
    vehicle_data: list[tuple[str, dict, Path]] = []  # (plate, row_data, vehicle_dir)

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(
            channel="msedge",
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        if STORAGE_STATE_FILE.exists():
            logger.info("从缓存恢复会话: %s", STORAGE_STATE_FILE)
            context: BrowserContext = browser.new_context(
                storage_state=str(STORAGE_STATE_FILE)
            )
        else:
            context = browser.new_context()

        page: Page = context.new_page()
        page.set_default_timeout(30000)

        # ---- 登录 ----
        ensure_logged_in(
            context=context,
            page=page,
            username=settings.username,
            password=settings.password,
            record_url=settings.record_url,
            base_url=settings.base_url,
            storage_state_file=STORAGE_STATE_FILE,
        )

        # ---- 导航：直接进入历史数据页面 ----
        logger.info("进入历史数据...")
        page.goto(settings.record_url, wait_until="commit", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(3)
        wait_spinner_gone(page, timeout=30)

        # ---- 筛选 ----
        apply_date_filter(page, from_date, to_date)
        time.sleep(2)
        wait_spinner_gone(page, timeout=30)

        # ---- 等待表格 ----
        rows = wait_for_table(page)
        if not rows:
            browser.close()
            logger.warning("未找到数据行")
            return report

        grad_col = get_grading_col_index(page)
        seen_plates: set[str] = set()
        page_num = 1
        all_rows_count = 0

        while True:
            logger.info("#" * 50 + f"\n### 第 {page_num} 页")
            rows = get_table_rows(page)
            if not rows:
                logger.warning("当前页无数据行，结束翻页")
                break

            all_rows_count += len(rows)

            # 提取当前页所有行的表格数据
            all_rows_data = get_row_cell_data(page)

            page_processed = 0
            page_skipped = 0

            for idx, row in enumerate(rows):
                # 料型过滤
                grade = get_row_grade(row, grad_col)
                if not is_allowed_grade(grade):
                    logger.info("[%d] 跳过: 料型=%s", idx + 1, grade or "(空白)")
                    report.skipped_grade += 1
                    page_skipped += 1
                    continue

                # 点击行 → 提取图片 URL（按类别）
                plate, classified = click_row_and_extract(
                    page, row, idx, report.processed, 2.0
                )

                if not classified:
                    logger.info("无图片")
                    report.skipped_no_detail += 1
                    report.processed += 1
                    page_processed += 1
                    continue

                if plate in seen_plates:
                    logger.info("%s 已处理过，跳过", plate)
                    report.processed += 1
                    page_processed += 1
                    continue
                seen_plates.add(plate)

                # 收集行数据供后续 Excel 构建
                row_data = all_rows_data[idx] if idx < len(all_rows_data) else {}
                vehicle_data.append((plate, row_data, settings.download_dir / plate))

                # 下载
                dres: DownloadResult = download_truck_images(
                    base_url=settings.base_url,
                    classified_urls=classified,
                    context=context,
                    save_dir=settings.download_dir,
                    skip_existing=skip_existing,
                    plate=plate,
                )
                report.processed += 1
                report.saved_files += len(dres.saved)
                report.skipped_existing += dres.skipped
                report.failed_files += len(dres.failed)
                page_processed += 1

                total_urls = sum(len(v) for v in classified.values())
                logger.info("本车: %d/%d | 总计: %d车 %d张",
                            len(dres.saved), total_urls,
                            report.processed, report.saved_files)

                # 饼图
                charts = extract_pie_charts(page)
                if charts:
                    n_charts = save_pie_charts(
                        plate=plate,
                        charts=charts,
                        save_dir=settings.download_dir,
                        skip_existing=skip_existing,
                    )
                    report.saved_files += n_charts
                time.sleep(0.5)

            logger.info("第 %d 页完成: 处理 %d 辆, 跳过 %d 辆",
                        page_num, page_processed, page_skipped)

            # 翻页
            logger.info("翻页...")
            if not go_to_next_page(page):
                logger.info("已是最后一页")
                break
            wait_spinner_gone(page, timeout=30)
            page_num += 1

        report.total_trucks = all_rows_count
        browser.close()

    # ---- 构建统计 Excel ----
    excel_path = None
    if vehicle_data:
        from .excel_builder import build_stat_excel
        date_tag = from_date or "all"
        if to_date and to_date != from_date:
            date_tag = f"{from_date}-{to_date}"
        xlsx_name = f"用友检判统计_{date_tag.replace('-', '')}.xlsx"
        excel_path = build_stat_excel(vehicle_data, settings.download_dir / xlsx_name)
        report.excel_path = str(excel_path)

    logger.info("=" * 60)
    logger.info("下载完成！")
    logger.info("  处理车辆: %d", report.processed)
    logger.info("  下载图片: %d", report.saved_files)
    logger.info("  跳过料型: %d", report.skipped_grade)
    logger.info("  跳过已存在: %d", report.skipped_existing)
    logger.info("  失败: %d", report.failed_files)
    logger.info("  保存目录: %s", settings.download_dir)
    if excel_path:
        logger.info("  统计 Excel: %s", excel_path)

    return report


def run(
    settings: YYSettings,
    start_date: str = "",
    end_date: str = "",
) -> list[YYReport]:
    """主入口：下载指定日期范围的用友检判结果图片。

    Args:
        settings:   配置对象
        start_date: 起始日期 YYYY-MM-DD（空=全部）
        end_date:   结束日期 YYYY-MM-DD（空=全部/等于start_date）
    """
    return [_do_download(settings, start_date, end_date or start_date)]


def _wait_network(page: Page):
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
