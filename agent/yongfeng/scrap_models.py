"""永锋废钢检判下载数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class YongfengRecord:
    flow_code: str
    car_number: str
    station_number: int | str
    create_time: str
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, item: dict) -> "YongfengRecord":
        return cls(
            flow_code=str(item.get("flowCode") or ""),
            car_number=str(item.get("carNumber") or ""),
            station_number=item.get("stationNumber") if item.get("stationNumber") not in (None, "") else 0,
            create_time=str(item.get("createTime") or ""),
            raw=item,
        )
