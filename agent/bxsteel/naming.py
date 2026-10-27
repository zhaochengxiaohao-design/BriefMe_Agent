"""文件名拼接工具。

目标格式：日期_料型1_占比1[_料型2_占比2...]_点位_当天第几辆_此辆车的第几张图.jpg
例：20260417_zhongfei1_80_jingluliao3_20_2_5_3.jpg
"""
from __future__ import annotations

import re
from typing import Sequence, Tuple


def extract_station_number(station_text: str) -> str:
    m = re.search(r"(\d+)", station_text or "")
    if not m:
        raise ValueError(f"无法从工位文本中解析数字：{station_text!r}")
    return m.group(1)


def format_date_compact(date_text: str) -> str:
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", date_text or "")
    if not m:
        raise ValueError(f"无法从时间文本解析日期：{date_text!r}")
    y, mo, d = m.groups()
    return f"{y}{mo}{d}"


def build_filename(
    date_compact: str,
    materials: Sequence[Tuple[str, int]],
    station: str,
    daily_index: int,
    image_index: int,
    ext: str = "jpg",
) -> str:
    if not materials:
        raise ValueError("materials 不能为空")
    mat_part = "_".join(f"{code}_{pct}" for code, pct in materials)
    return f"{date_compact}_{mat_part}_{station}_{daily_index}_{image_index}.{ext}"


def sanitize_for_fs(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
