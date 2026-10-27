"""解析人工判级结果文本为 (料型代码, 占比) 列表。

输入示例：
    "重废等级一-80.00%,精炉料等级三20.00%"
    "中废 100%"
    "重废等级二 60%, 重废等级一 40%"

输出：按占比降序排列的 [(code, percent_int), ...]
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

_CN_NUM: dict[str, int] = {"一": 1, "二": 2, "三": 3}

_GRADED: dict[str, str] = {
    "重废": "zhongfei",
    "精炉料": "jingluliao",
}
_UNGRADED: dict[str, str] = {
    "中废": "medium",
    "杂模": "zamo",
}

_PATTERN = re.compile(
    r"(重废|精炉料|中废|杂模)"
    r"(?:\s*等级\s*([一二三]))?"
    r"[^\d%\-\u4e00-\u9fa5]*[-\s]*"
    r"([0-9]+(?:\.[0-9]+)?)\s*%"
)


def _to_code(material: str, grade_cn: str | None) -> str | None:
    if material in _UNGRADED:
        return _UNGRADED[material]
    if material in _GRADED:
        if not grade_cn:
            logger.warning("料型 %s 缺少等级后缀，跳过", material)
            return None
        num = _CN_NUM.get(grade_cn)
        if num is None:
            logger.warning("不认识的等级中文数字：%s", grade_cn)
            return None
        return f"{_GRADED[material]}{num}"
    return None


def parse_manual_result(text: str) -> List[Tuple[str, int]]:
    if not text or not text.strip():
        return []

    results: list[tuple[str, int]] = []
    order: list[tuple[str, int]] = []
    for idx, m in enumerate(_PATTERN.finditer(text)):
        material, grade_cn, pct_str = m.group(1), m.group(2), m.group(3)
        code = _to_code(material, grade_cn)
        if code is None:
            continue
        try:
            pct = round(float(pct_str))
        except ValueError:
            logger.warning("无法解析百分比：%s", pct_str)
            continue
        results.append((code, pct))
        order.append((code, idx))

    idx_map = {code: idx for code, idx in order}
    results.sort(key=lambda x: (-x[1], idx_map.get(x[0], 0)))
    return results
