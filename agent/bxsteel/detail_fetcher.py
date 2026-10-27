"""详情接口处理：抽取人工判级结果 + 原图 URL 列表。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Tuple

from .api_client import ApiClient
from .parser import parse_manual_result

logger = logging.getLogger(__name__)


@dataclass
class TruckDetail:
    flow_code: str
    car_number: str
    station_number: int
    check_start_time: str
    manual_raw: str
    materials: List[Tuple[str, int]]
    origin_image_urls: List[str] = field(default_factory=list)

    @property
    def has_manual(self) -> bool:
        return bool(self.materials)


def _extract_origin_image_urls(detail_data: dict[str, Any]) -> list[str]:
    items = detail_data.get("oneCheckSummaryDTOList") or []
    items = sorted(items, key=lambda x: x.get("accTimestamp") or "")
    seen: set[str] = set()
    urls: list[str] = []
    for it in items:
        u = it.get("originImageUrl")
        if not u or u in seen:
            continue
        seen.add(u)
        urls.append(u)
    return urls


def fetch_detail(
    client: ApiClient,
    flow_code: str,
    meta_fallback: dict[str, Any] | None = None,
) -> TruckDetail:
    data = client.get_check_detail(flow_code)
    manual = (data.get("manualCheck") or {}).get("manualResults", "") or ""
    materials = parse_manual_result(manual)

    car_number = data.get("carNumber") or (meta_fallback or {}).get("carNumber") or ""
    station = int(data.get("stationNumber") or (meta_fallback or {}).get("stationNumber") or 0)
    start_time = (
        (data.get("intelliTaskInfo") or {}).get("createTime")
        or (meta_fallback or {}).get("checkStartTime")
        or ""
    )
    urls = _extract_origin_image_urls(data)

    return TruckDetail(
        flow_code=flow_code,
        car_number=car_number,
        station_number=station,
        check_start_time=start_time,
        manual_raw=manual,
        materials=materials,
        origin_image_urls=urls,
    )
