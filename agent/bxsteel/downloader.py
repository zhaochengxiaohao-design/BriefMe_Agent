"""对一辆车的原图批量下载并按规则重命名。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .api_client import ApiClient
from .detail_fetcher import TruckDetail
from .naming import build_filename, extract_station_number, format_date_compact

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    flow_code: str
    car_number: str
    saved: List[Tuple[str, Path]]
    skipped_existing: int
    failed: List[Tuple[str, str]]


def download_truck_images(
    client: ApiClient,
    detail: TruckDetail,
    date_for_name: str,
    daily_index: int,
    output_root: Path,
) -> DownloadResult:
    date_compact = format_date_compact(date_for_name)
    station = extract_station_number(f"{detail.station_number}号工位")
    day_dir = output_root / date_compact
    day_dir.mkdir(parents=True, exist_ok=True)

    saved: list[tuple[str, Path]] = []
    failed: list[tuple[str, str]] = []
    skipped = 0

    for img_idx, url in enumerate(detail.origin_image_urls, start=1):
        fname = build_filename(
            date_compact=date_compact,
            materials=detail.materials,
            station=station,
            daily_index=daily_index,
            image_index=img_idx,
        )
        dest = day_dir / fname
        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1
            logger.debug("已存在，跳过：%s", dest.name)
            continue
        try:
            size = client.download_image(url, dest)
            logger.info(
                "下载 [%s #%d/%d] %s (%.1f KB)",
                detail.car_number,
                img_idx,
                len(detail.origin_image_urls),
                dest.name,
                size / 1024,
            )
            saved.append((url, dest))
        except Exception as exc:
            logger.error("下载失败：%s -> %s", url, exc)
            failed.append((url, str(exc)))

    return DownloadResult(
        flow_code=detail.flow_code,
        car_number=detail.car_number,
        saved=saved,
        skipped_existing=skipped,
        failed=failed,
    )
