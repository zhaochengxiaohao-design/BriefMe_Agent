"""串联登录 → 列表 → 详情 → 下载的顶层流程。"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import List, Optional

from .api_client import ApiClient
from .config import BxSettings, TOKEN_CACHE_FILE
from .detail_fetcher import TruckDetail, fetch_detail
from .downloader import DownloadResult, download_truck_images
from .list_fetcher import TruckMeta, enumerate_daily, fetch_day

logger = logging.getLogger(__name__)

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


@dataclass
class DayReport:
    date: str
    total_trucks: int
    processed: int
    skipped_no_manual: int
    skipped_no_images: int
    saved_files: int
    skipped_existing: int
    failed_files: int
    failed_detail: list[tuple[str, str]] = field(default_factory=list)


def run_for_day(
    client: ApiClient,
    date_str: str,
    output_root: Path,
    cache_dir: Path,
    limit: Optional[int] = None,
    page_size: int = 50,
    progress_callback: callable = None,
) -> DayReport:
    trucks = fetch_day(client, date_str, page_size=page_size)
    if limit is not None:
        trucks = trucks[:limit]

    report = DayReport(
        date=date_str,
        total_trucks=len(trucks),
        processed=0,
        skipped_no_manual=0,
        skipped_no_images=0,
        saved_files=0,
        skipped_existing=0,
        failed_files=0,
    )

    day_cache = cache_dir / "reports" / date_str
    day_cache.mkdir(parents=True, exist_ok=True)
    skip_csv = day_cache / "skipped.csv"
    fail_csv = day_cache / "failed.csv"

    with (
        skip_csv.open("w", newline="", encoding="utf-8") as sf,
        fail_csv.open("w", newline="", encoding="utf-8") as ff,
    ):
        skw = csv.writer(sf)
        skw.writerow(["flow_code", "car_number", "reason"])
        fw = csv.writer(ff)
        fw.writerow(["flow_code", "car_number", "url", "error"])

        iterator = enumerate_daily(trucks)
        pbar = tqdm(iterator, total=len(trucks), desc=date_str, unit="truck")
        for daily_idx, meta in pbar:
            pbar.set_postfix_str(f"{meta.car_number}")
            if progress_callback:
                progress_callback(
                    f"[{date_str}] 处理车辆 {daily_idx}/{len(trucks)}: {meta.car_number}"
                )
            try:
                detail = fetch_detail(client, meta.flow_code, meta_fallback=meta.__dict__)
            except Exception as exc:
                logger.error("详情获取失败：%s -> %s", meta.flow_code, exc)
                report.failed_detail.append((meta.flow_code, str(exc)))
                continue

            if not detail.has_manual:
                logger.info(
                    "跳过无人工判级：%s (manualRaw=%r)",
                    meta.car_number,
                    detail.manual_raw,
                )
                skw.writerow([meta.flow_code, meta.car_number, "no_manual_result"])
                report.skipped_no_manual += 1
                continue

            if not detail.origin_image_urls:
                logger.warning("跳过无原图：%s", meta.car_number)
                skw.writerow([meta.flow_code, meta.car_number, "no_origin_images"])
                report.skipped_no_images += 1
                continue

            date_for_name = detail.check_start_time or meta.check_start_time or date_str
            try:
                dres: DownloadResult = download_truck_images(
                    client=client,
                    detail=detail,
                    date_for_name=date_for_name,
                    daily_index=daily_idx,
                    output_root=output_root,
                )
            except Exception as exc:
                logger.error("下载批次失败：%s -> %s", meta.car_number, exc)
                fw.writerow([meta.flow_code, meta.car_number, "*batch*", str(exc)])
                continue

            report.processed += 1
            report.saved_files += len(dres.saved)
            report.skipped_existing += dres.skipped_existing
            report.failed_files += len(dres.failed)
            for u, err in dres.failed:
                fw.writerow([meta.flow_code, meta.car_number, u, err])

    logger.info("日期 %s 汇总：%s", date_str, report)
    return report


def run(
    settings: BxSettings,
    start_date: str,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    progress_callback: callable = None,
) -> List[DayReport]:
    client = ApiClient(base_url=settings.base_url)
    client.ensure_login(
        username=settings.username,
        password=settings.password,
        token_file=TOKEN_CACHE_FILE,
    )

    if end_date is None:
        end_date = start_date

    def _parse(s: str) -> date_cls:
        y, m, d = s.split("-")
        return date_cls(int(y), int(m), int(d))

    start = _parse(start_date)
    end = _parse(end_date)
    if end < start:
        raise ValueError("end_date 不能早于 start_date")

    reports: list[DayReport] = []
    cur = start
    while cur <= end:
        rep = run_for_day(
            client=client,
            date_str=cur.isoformat(),
            output_root=settings.download_dir,
            cache_dir=settings.cache_dir,
            limit=limit,
            progress_callback=progress_callback,
        )
        reports.append(rep)
        cur = cur + timedelta(days=1)

    return reports
