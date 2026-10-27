"""永锋废钢料型字典（检判原图命名 / 打包用）。

下载命名只用人工上传的废钢主料型，共 6 种。页面写成「废钢主料型：重废1」，
文件夹用罗马数字。本文件自维护，禁止 import agent.shenglong.dict。
烧结矿颗粒度字典在同包 dict.py，不要混用。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

STEEL_TYPE: dict[int, str] = {
    0: "",
    1: "重废1",
    2: "重废2",
    3: "重废3",
    4: "剪料1",
    5: "剪料2",
    6: "剪料3",
    7: "剪料4",
    8: "破碎料1",
    9: "破碎料2",
    10: "破碎料3",
    11: "中废",
    12: "生铁",
    13: "厚剪",
    14: "钢筋切粒",
    15: "汽车壳",
    16: "超标",
}

STEEL_TYPE_EN: dict[int, str] = {
    0: "",
    1: "zhongfei1",
    2: "zhongfei2",
    3: "zhongfei3",
    4: "jianliao1",
    5: "jianliao2",
    6: "jianliao3",
    7: "jianliao4",
    8: "posuiliao1",
    9: "posuiliao2",
    10: "posuiliao3",
    11: "medium",
    12: "shengtie",
    13: "houjian",
    14: "gangjinqieli",
    15: "qicheke",
    16: "chaobiao",
}

REFERENCE_TYPES: frozenset[int] = frozenset({16})
EMPTY_TYPES: frozenset[int] = frozenset({0})

MATERIAL_PRIORITY: dict[int, int] = {
    1: 1,
    2: 2,
    3: 3,
    12: 4,
    11: 5,
    13: 6,
    4: 7,
    5: 8,
    6: 9,
    7: 10,
    8: 11,
    9: 12,
    10: 13,
    14: 14,
    15: 15,
}


# 详情 deductionDetail.declaredSteelTypeCode / declaredSteelTypeName。
# 4 在页面旧字典里写成统料1，现场接口返回的名字是「统料」。以现场名字为准。
DOWNLOAD_MAIN_TYPES: dict[int, tuple[str, str]] = {
    1: ("重废Ⅰ", "zhongfei1"),
    2: ("重废Ⅱ", "zhongfei2"),
    3: ("重废Ⅲ", "zhongfei3"),
    4: ("统料", "tongliao"),
    5: ("统料Ⅰ", "tongliao1"),
    6: ("统料Ⅱ", "tongliao2"),
}

_MAIN_LABEL_CODE: dict[str, int] = {
    "重废1": 1,
    "重废2": 2,
    "重废3": 3,
    "统料2": 6,
    "统料1": 5,
    "统料": 4,
}
_ROMAN_TO_DIGIT = str.maketrans({"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3"})
_PAREN_LABEL = re.compile(r"[（(]([^）)]+)[）)]")


def _normalize_main_label(value: object) -> str:
    text = str(value or "").strip()
    matched = _PAREN_LABEL.search(text)
    if matched:
        text = matched.group(1).strip()
    text = text.replace(" ", "").translate(_ROMAN_TO_DIGIT)
    text = text.replace("III", "3").replace("II", "2")
    return text


def resolve_download_main_type(
    label: object = None,
    code: object = None,
) -> Optional[Tuple[int, str, str]]:
    """把废钢主料型收成 (编码, 文件夹中文名, 文件英文码)。对不上返回 None。"""
    resolved: Optional[int] = None
    normalized = _normalize_main_label(label)
    if normalized:
        for alias, mapped in sorted(_MAIN_LABEL_CODE.items(), key=lambda item: len(item[0]), reverse=True):
            if normalized == alias:
                resolved = mapped
                break
        if resolved is None and normalized.endswith("统料") and "统料1" not in normalized and "统料2" not in normalized:
            resolved = 4
    if resolved is None and code not in (None, ""):
        try:
            resolved = int(code)
        except (TypeError, ValueError):
            resolved = None
    spec = DOWNLOAD_MAIN_TYPES.get(resolved) if resolved is not None else None
    if spec is None or resolved is None:
        return None
    name_zh, name_en = spec
    return resolved, name_zh, name_en


def get_material_en(steel_type: Optional[int]) -> str:
    if steel_type is None:
        return "unknown"
    code = int(steel_type)
    if code in EMPTY_TYPES:
        return "unknown"
    return STEEL_TYPE_EN.get(code, f"unknown{code}")


def get_material_name(steel_type: Optional[int]) -> str:
    if steel_type is None:
        return "--"
    code = int(steel_type)
    if code in EMPTY_TYPES:
        return "--"
    return STEEL_TYPE.get(code, f"未知{code}")


def is_valid(steel_type: Optional[int]) -> bool:
    if steel_type is None:
        return False
    code = int(steel_type)
    if code in EMPTY_TYPES or code in REFERENCE_TYPES:
        return False
    return code in STEEL_TYPE


def filter_main_candidates(
    items: Iterable[Tuple[Optional[int], float]],
) -> List[Tuple[int, float]]:
    out: List[Tuple[int, float]] = []
    for st, rate in items:
        if st is None:
            continue
        code = int(st)
        if code in EMPTY_TYPES or code in REFERENCE_TYPES:
            continue
        if code not in STEEL_TYPE:
            continue
        try:
            out.append((code, float(rate)))
        except (TypeError, ValueError):
            continue
    return out
