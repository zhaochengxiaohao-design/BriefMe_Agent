"""列表拉取 + 车辆元信息整理。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from .api_client import ApiClient

logger = logging.getLogger(__name__)


@dataclass
class TruckMeta:
    flow_code: str
    car_number: str
    station_number: int
    check_start_time: str
    check_complete_time: str | None
    check_situation: int | None

    @classmethod
    def from_record(cls, rec: dict) -> "TruckMeta":
        return cls(
            flow_code=rec.get("flowCode") or "",
            car_number=rec.get("carNumber") or "",
            station_number=int(rec.get("stationNumber") or 0),
            check_start_time=rec.get("checkStartTime") or "",
            check_complete_time=rec.get("checkCompleteTime"),
            check_situation=rec.get("checkSituation"),
        )


def fetch_day(
    client: ApiClient,
    date_str: str,
    page_size: int = 50,
) -> List[TruckMeta]:
    start = f"{date_str} 00:00:00"
    end = f"{date_str} 23:59:59"
    records: list[TruckMeta] = []
    for rec in client.iter_judgments_by_date(start, end, page_size=page_size):
        meta = TruckMeta.from_record(rec)
        if not meta.flow_code:
            logger.warning("跳过 flowCode 为空的记录：%s", rec.get("id"))
            continue
        records.append(meta)

    records.sort(key=lambda m: m.check_start_time)
    logger.info("日期 %s 共取得 %d 条记录", date_str, len(records))
    return records


def enumerate_daily(trucks: Iterable[TruckMeta]) -> list[tuple[int, TruckMeta]]:
    return [(idx, t) for idx, t in enumerate(trucks, start=1)]
