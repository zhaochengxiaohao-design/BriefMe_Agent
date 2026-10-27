"""通过 requests 下载图片（复用浏览器 cookies），按类别存入子目录。"""
from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from playwright.sync_api import BrowserContext

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class DownloadResult:
    plate: str
    saved: List[Tuple[str, Path]]
    skipped: int
    failed: List[Tuple[str, str]]
    by_category: Dict[str, int] = field(default_factory=dict)


def get_image_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)


def get_plate_from_filename(filename: str) -> str:
    clean = filename
    for prefix in ['visualize_superdetect_', 'visualize_special_', 'visualize_top_', 'visualize_']:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    match = re.match(r'^([\u4e00-\u9fffA-Za-z0-9]+)-[a-f0-9]{32}', clean)
    return match.group(1) if match else "unknown"


def download_truck_images(
    base_url: str,
    classified_urls: Dict[str, List[str]],
    context: BrowserContext,
    save_dir: Path,
    skip_existing: bool = True,
    plate: str = "",
) -> DownloadResult:
    all_urls: list[str] = []
    for urls in classified_urls.values():
        all_urls.extend(urls)
    if plate:
        pass  # 使用传入的车牌
    else:
        plate = get_plate_from_filename(get_image_filename(all_urls[0])) if all_urls else "unknown"

    plate_dir = save_dir / plate

    saved: list[tuple[str, Path]] = []
    failed: list[tuple[str, str]] = []
    skipped = 0
    by_category: dict[str, int] = {}

    headers = {}
    try:
        cookies = context.cookies()
        cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)
        headers["Cookie"] = cookie_str
    except Exception:
        pass

    for category, urls in classified_urls.items():
        cat_dir = plate_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        cat_saved = 0

        for url in urls:
            fname = get_image_filename(url)
            dest = cat_dir / fname

            if skip_existing and dest.exists() and dest.stat().st_size > 0:
                skipped += 1
                continue

            full_url = urljoin(base_url, url)
            try:
                resp = requests.get(full_url, headers=headers, timeout=30, verify=False)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                cat_saved += 1
                logger.info("  [%s] %s (%d KB)", category, fname, len(resp.content) // 1024)
                saved.append((url, dest))
            except Exception as exc:  # noqa: BLE001
                logger.error("  [%s] 失败 %s: %s", category, fname, exc)
                failed.append((url, f"{category}:{str(exc)}"))

        by_category[category] = cat_saved

    return DownloadResult(
        plate=plate,
        saved=saved,
        skipped=skipped,
        failed=failed,
        by_category=by_category,
    )


def save_pie_charts(
    plate: str,
    charts: List[Tuple[str, str]],
    save_dir: Path,
    skip_existing: bool = True,
) -> int:
    chart_dir = save_dir / plate / "饼图"
    chart_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    for label, data_url in charts:
        fname = f"{label}.png"
        dest = chart_dir / fname

        if skip_existing and dest.exists() and dest.stat().st_size > 0:
            continue

        _, b64 = data_url.split(",", 1)
        data = base64.b64decode(b64)
        dest.write_bytes(data)
        saved += 1
        logger.info("  [饼图] %s (%d KB)", fname, len(data) // 1024)

    return saved
