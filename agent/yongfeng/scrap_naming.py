"""永锋检判原图的目录名、文件名、数据集分组。

文件夹按详情页「废钢主料型」命名，不写占比（主料默认 100%）：
    2026-10-27_鲁Q768TM_统料
    2026-10-27_鲁Q06362D_统料Ⅰ

单张原图：
    日期_英文料型_点位_第几辆_第几张.jpg
    20261027_tongliao_1_1_3.jpg
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from agent.yongfeng.scrap_dict import (
    MATERIAL_PRIORITY,
    filter_main_candidates,
    get_material_en,
    get_material_name,
    resolve_download_main_type,
)

logger = logging.getLogger(__name__)

AVG_TYPE_DIFF_TOLERANCE: float = 15.0
AVERAGE_TYPE_NAME = "平均料型"

_UNSAFE_FS = re.compile(r'[\\/:*?"<>|]+')


@dataclass(frozen=True)
class _ParsedRate:
    steel_type: Optional[int]
    rate: float


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


def _first_scalar(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_rate_list(raw_list) -> List[_ParsedRate]:
    """把 [{steelType, steelRate|avgRate|rate}, ...] 解析为百分比（0~100）。"""
    out: List[_ParsedRate] = []
    if not raw_list:
        return out
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        st = _first_scalar(item.get("steelType"))
        if "steelRate" in item:
            rate_raw = item.get("steelRate", 0)
        elif "avgRate" in item:
            rate_raw = item.get("avgRate", 0)
        elif "rate" in item:
            rate_raw = item.get("rate", 0)
        else:
            logger.warning("未知料型字段命名，跳过: %s", item)
            continue
        try:
            rate = float(rate_raw)
        except (TypeError, ValueError):
            logger.warning("rate 解析失败: %s", item)
            continue
        if abs(rate) <= 1.0 + 1e-9:
            rate *= 100.0
        try:
            steel_type = int(st) if st is not None else None
        except (TypeError, ValueError):
            steel_type = None
        out.append(_ParsedRate(steel_type=steel_type, rate=rate))
    return out


def _rate_sort_key(item: MaterialShare) -> Tuple[float, int]:
    return (-item.rate_pct, MATERIAL_PRIORITY.get(item.steel_type, 999))


def parse_manual_shares(avg_result) -> List[MaterialShare]:
    """从人工 avgResult 或 AI steelTypeRateList 解析料型占比，按占比降序。"""
    shares: List[MaterialShare] = []
    for item in _parse_rate_list(avg_result):
        if item.steel_type is None or item.rate <= 0:
            continue
        name_zh = get_material_name(item.steel_type)
        if not name_zh or name_zh == "--":
            continue
        share = MaterialShare(
            steel_type=item.steel_type,
            rate_pct=item.rate,
            name_zh=name_zh,
            name_en=get_material_en(item.steel_type),
        )
        if share.pct_int <= 0:
            continue
        shares.append(share)
    shares.sort(key=_rate_sort_key)
    return shares


def single_full_main(shares: Sequence[MaterialShare]) -> bool:
    """人工主料型只有一种，占比按 100% 记，名字里不再写数字。"""
    return len(shares) == 1 and shares[0].pct_int == 100


def format_folder_materials(shares: Sequence[MaterialShare]) -> str:
    if not shares:
        return "无人工"
    if single_full_main(shares):
        return shares[0].name_zh
    return "、".join(f"{s.name_zh}({s.pct_int})" for s in shares)


def build_truck_folder_stem(
    car_number: str,
    shares: Sequence[MaterialShare],
) -> str:
    plate = sanitize_fs_name(car_number) or "未知车牌"
    return sanitize_fs_name(f"{plate}_{format_folder_materials(shares)}")


def build_truck_folder_name(
    car_number: str,
    shares: Sequence[MaterialShare],
    date_text: str,
) -> str:
    date_part = sanitize_fs_name(date_text) or "未知日期"
    return sanitize_fs_name(f"{date_part}_{build_truck_folder_stem(car_number, shares)}")


def format_date_compact(date_text: str) -> str:
    matched = re.search(r"(\d{4})[-/_]?(\d{2})[-/_]?(\d{2})", date_text or "")
    if not matched:
        raise ValueError(f"无法解析日期: {date_text!r}")
    return "".join(matched.groups())


def resolve_station_code(station_number, detail: Optional[dict] = None) -> str:
    """取质检工位号。多工位时用列表最后一个。"""
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
    if single_full_main(shares):
        return shares[0].name_en
    return "_".join(f"{s.name_en}_{s.pct_int}" for s in shares)


def build_image_filename(
    date_text: str,
    station: str,
    daily_index: int,
    shares: Sequence[MaterialShare],
    image_index: int,
    ext: str = "jpg",
) -> str:
    date_part = format_date_compact(date_text)
    mat_part = format_filename_materials(shares)
    suffix = ext.lstrip(".") or "jpg"
    return f"{date_part}_{mat_part}_{station}_{daily_index}_{image_index}.{suffix}"


def share_from_uploaded_main(label: object = None, code: object = None) -> Optional[MaterialShare]:
    """详情里的废钢主料型 → 一条 100% 占比，供文件夹、文件名和打包使用。"""
    resolved = resolve_download_main_type(label, code)
    if resolved is None:
        return None
    steel_type, name_zh, name_en = resolved
    return MaterialShare(
        steel_type=steel_type,
        rate_pct=100.0,
        name_zh=name_zh,
        name_en=name_en,
    )


def main_share_from_detail(detail: dict, raw: Optional[dict] = None) -> List[MaterialShare]:
    """只认已上传的废钢主料型，不用智能检判占比。"""
    deduction = ((detail or {}).get("totalCheckResult") or {}).get("deductionDetail") or {}
    share = share_from_uploaded_main(
        deduction.get("declaredSteelTypeName"),
        deduction.get("declaredSteelTypeCode"),
    )
    if share is None and raw:
        share = share_from_uploaded_main(raw.get("materialName"), raw.get("declaredSteelType"))
    return [share] if share is not None else []


def classify_pack_group(
    shares: Sequence[MaterialShare],
    *,
    avg_diff: float = AVG_TYPE_DIFF_TOLERANCE,
) -> Tuple[str, str]:
    """返回 (main|average|none, 压缩包主料型名)。主次料差 ≤15 百分点 → 平均料型。"""
    if single_full_main(shares):
        return "main", shares[0].name_zh
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
    """智能判级原图：优先 oneCheckSummaryDTOList.originImageUrl（按时间），否则 allOriginImageUrls。

    只收原图，跳过 *_render_* 预览。
    """
    tcr = (detail or {}).get("totalCheckResult") or {}
    summaries = tcr.get("oneCheckSummaryDTOList") or []
    timed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in summaries:
        if not isinstance(item, dict):
            continue
        url = str(item.get("originImageUrl") or "").strip()
        if not url or url in seen or "_render_" in url:
            continue
        seen.add(url)
        timed.append((str(item.get("accTimestamp") or ""), url))
    if timed:
        timed.sort(key=lambda pair: pair[0])
        return [url for _, url in timed]

    urls: list[str] = []
    for url in tcr.get("allOriginImageUrls") or []:
        text = str(url or "").strip()
        if text and text not in seen and "_render_" not in text:
            seen.add(text)
            urls.append(text)
    return urls
