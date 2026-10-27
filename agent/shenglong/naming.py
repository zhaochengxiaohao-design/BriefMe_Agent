"""盛隆检判原图的目录名、文件名、数据集分组。

文件夹（中文，与页面「人工检判结果」一致，带当天日期）：
    2026-08-27_桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)

单张原图：
    日期_料型占比_点位_第几辆_第几张.jpg
    20260827_medium_40_zhongfei1_30_zhongfei3_20_houjian_10_53_1_1.jpg
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from agent.shenglong.calculator import _parse_rate_list
from agent.shenglong.dict import (
    MATERIAL_PRIORITY,
    filter_main_candidates,
    get_material_en,
    get_material_name,
    is_valid,
)
from agent.shenglong.models import MaterialRate

# 主料与次料占比相差不超过该值（百分点）→ 全部打进同一个「平均料型」包
AVG_TYPE_DIFF_TOLERANCE: float = 15.0
AVERAGE_TYPE_NAME = "平均料型"

_UNSAFE_FS = re.compile(r'[\\/:*?"<>|]+')


@dataclass(frozen=True)
class MaterialShare:
    steel_type: int
    rate_pct: float
    name_zh: str
    name_en: str

    @property
    def pct_int(self) -> int:
        return int(round(self.rate_pct))


def sanitize_fs_name(value: str) -> str:
    text = _UNSAFE_FS.sub("_", (value or "").strip())
    return text.strip(" .") or "unknown"


def _rate_sort_key(item: MaterialShare) -> Tuple[float, int]:
    return (-item.rate_pct, MATERIAL_PRIORITY.get(item.steel_type, 999))


def parse_manual_shares(avg_result) -> List[MaterialShare]:
    """从页面同款 avgResult 解析料型占比，按占比降序。"""
    parsed: List[MaterialRate] = _parse_rate_list(avg_result)
    shares: List[MaterialShare] = []
    for item in parsed:
        if item.steel_type is None:
            continue
        if item.rate <= 0:
            continue
        name_zh = get_material_name(item.steel_type)
        if name_zh == "--":
            continue
        shares.append(
            MaterialShare(
                steel_type=item.steel_type,
                rate_pct=item.rate,
                name_zh=name_zh,
                name_en=get_material_en(item.steel_type),
            )
        )
    shares.sort(key=_rate_sort_key)
    return shares


def format_folder_materials(shares: Sequence[MaterialShare]) -> str:
    """中废(40)、重废1(30) —— 不含百分号。"""
    if not shares:
        return "无人工"
    return "、".join(f"{s.name_zh}({s.pct_int})" for s in shares)


def build_truck_folder_stem(
    car_number: str,
    shares: Sequence[MaterialShare],
) -> str:
    """不含日期的旧车次文件夹名：桂ND3699_中废(40)..."""
    plate = sanitize_fs_name(car_number) or "未知车牌"
    return sanitize_fs_name(f"{plate}_{format_folder_materials(shares)}")


def build_truck_folder_name(
    car_number: str,
    shares: Sequence[MaterialShare],
    date_text: str,
) -> str:
    """2026-08-27_桂ND3699_中废(40)、重废1(30)..."""
    date_part = sanitize_fs_name(date_text) or "未知日期"
    return sanitize_fs_name(f"{date_part}_{build_truck_folder_stem(car_number, shares)}")


def format_date_compact(date_text: str) -> str:
    """2026-08-26 / 2026_08_26 → 20260826。"""
    matched = re.search(r"(\d{4})[-/_]?(\d{2})[-/_]?(\d{2})", date_text or "")
    if not matched:
        raise ValueError(f"无法解析日期: {date_text!r}")
    return "".join(matched.groups())


def resolve_station_code(station_number, detail: Optional[dict] = None) -> str:
    """取质检工位号。多工位时用列表最后一个（通常是检判工位，如 36/53 → 53）。"""
    if isinstance(detail, dict):
        nums = detail.get("stationNumbers")
        if isinstance(nums, list) and nums:
            return str(int(nums[-1]))
    if isinstance(station_number, (list, tuple)) and station_number:
        return str(int(station_number[-1]))
    text = str(station_number or "").strip()
    parts = re.findall(r"\d+", text)
    if not parts:
        return "0"
    return parts[-1]


def format_filename_materials(shares: Sequence[MaterialShare]) -> str:
    if not shares:
        return "unknown_0"
    return "_".join(f"{s.name_en}_{s.pct_int}" for s in shares)


def build_image_filename(
    date_text: str,
    station: str,
    daily_index: int,
    shares: Sequence[MaterialShare],
    image_index: int,
    ext: str = "jpg",
) -> str:
    """日期_料型占比_点位_第几辆_第几张.jpg"""
    date_part = format_date_compact(date_text)
    mat_part = format_filename_materials(shares)
    suffix = ext.lstrip(".") or "jpg"
    return (
        f"{date_part}_{mat_part}_{station}_{daily_index}_{image_index}.{suffix}"
    )


def classify_pack_group(
    shares: Sequence[MaterialShare],
    *,
    avg_diff: float = AVG_TYPE_DIFF_TOLERANCE,
) -> Tuple[str, str]:
    """返回 (main|average|none, 压缩包主料型名)。

    主料与次料相差 <=15 个百分点 → 一律打进「平均料型」；
    谁高谁低都进同一个包，不再拆成 重废1_重废2 / 重废2_重废1。
    否则按主料型单独成包。
    """
    valid = filter_main_candidates([(s.steel_type, s.rate_pct) for s in shares])
    if not valid:
        return "none", ""
    valid.sort(key=lambda x: (-x[1], MATERIAL_PRIORITY.get(x[0], 999)))
    main_type, main_rate = valid[0]
    if len(valid) == 1:
        return "main", get_material_name(main_type)
    _, second_rate = valid[1]
    if abs(main_rate - second_rate) <= avg_diff:
        return "average", AVERAGE_TYPE_NAME
    return "main", get_material_name(main_type)


def extract_origin_image_urls(detail: dict) -> List[str]:
    """智能判级原图：优先 oneCheckSummaryDTOList.originImageUrl（按时间），否则 allOriginImageUrls。"""
    tcr = (detail or {}).get("totalCheckResult") or {}
    summaries = tcr.get("oneCheckSummaryDTOList") or []
    timed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in summaries:
        if not isinstance(item, dict):
            continue
        url = str(item.get("originImageUrl") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        timed.append((str(item.get("accTimestamp") or ""), url))
    if timed:
        timed.sort(key=lambda pair: pair[0])
        return [url for _, url in timed]

    urls: list[str] = []
    for url in tcr.get("allOriginImageUrls") or []:
        text = str(url or "").strip()
        if text and text not in seen:
            seen.add(text)
            urls.append(text)
    return urls
