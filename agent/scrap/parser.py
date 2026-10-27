"""解析人工/赛迪的料型结果

人工 manualResults 是字符串，例：
  "中废等级一50.00%,重废等级二50.00%"
  "精炉料一95.00%"
  "重废II级80%,中废20%"

赛迪 steelTypeRateDTOList 是结构化 list[{steelType, steelLevel, steelRate}]
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from agent.scrap.dict import (
    AGGREGATED_STEEL_TYPES,
    LEVEL_CN_TO_CODE,
    STEEL_TYPE_NAME_TO_CODE,
)
from agent.scrap.models import MaterialEntry

logger = logging.getLogger(__name__)

# 按逗号/、/分号/空白切分多条料型
_SPLIT_RE = re.compile(r"[,，、;；]+")

# 单条料型格式: <中文料型名><等级(可选)><百分比>%
# 等级部分支持：等级一/二/三、一级/二级/三级、I级/II级/III级、Ⅰ级/Ⅱ级/Ⅲ级
_ITEM_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5\w]+?)"
    r"(?P<level>等级[一二三]|[一二三]级|I{1,3}级|[ⅠⅡⅢ]级)?"
    r"\s*"
    r"(?P<rate>\d+(?:\.\d+)?)"
    r"\s*%",
    re.UNICODE,
)


def parse_manual_results(text: Optional[str]) -> List[MaterialEntry]:
    """解析人工 manualResults 字符串。

    规则：
      - 贪婪优先从长匹配料型名（避免"中废等级一"里"中"先被匹配成别的）
      - 无等级后缀时 steel_level=0
      - 解析失败返回空列表
    """
    if not text or not isinstance(text, str):
        return []

    text = text.strip()
    if not text:
        return []

    entries: List[MaterialEntry] = []

    # 料型名单按长度降序排（防"精炉料"被"精"拦截等情况，但本字典不冲突；保守处理）
    type_names = sorted(
        STEEL_TYPE_NAME_TO_CODE.keys(), key=lambda s: len(s), reverse=True
    )

    for part in _SPLIT_RE.split(text):
        part = part.strip()
        if not part:
            continue

        type_code: Optional[int] = None
        matched_name = ""
        for name in type_names:
            if part.startswith(name):
                type_code = STEEL_TYPE_NAME_TO_CODE[name]
                matched_name = name
                break

        if type_code is None:
            m = _ITEM_RE.match(part)
            if not m:
                logger.warning("manualResults 片段无法解析: %s", part)
                continue
            name_raw = m.group("name")
            type_code = STEEL_TYPE_NAME_TO_CODE.get(name_raw)
            if type_code is None:
                logger.warning("未知料型名: %s (原片段=%s)", name_raw, part)
                continue
            matched_name = name_raw

        rest = part[len(matched_name):]
        m2 = re.match(
            r"(?P<level>等级[一二三]|[一二三]级|I{1,3}级|[ⅠⅡⅢ]级)?"
            r"\s*(?P<rate>\d+(?:\.\d+)?)\s*%",
            rest,
        )
        if not m2:
            logger.warning("料型后缀无法解析: %s (片段=%s)", rest, part)
            continue

        level_str = m2.group("level") or ""
        rate_str = m2.group("rate")
        level_code = LEVEL_CN_TO_CODE.get(level_str, 0)
        try:
            rate = float(rate_str)
        except ValueError:
            logger.warning("占比数字解析失败: %s", rate_str)
            continue

        entries.append(
            MaterialEntry(
                steel_type=type_code,
                steel_level=level_code,
                rate=rate,
                raw_name=part,
            )
        )

    return _aggregate_levels(entries)


def parse_ai_materials(dto_list: Optional[List[dict]]) -> List[MaterialEntry]:
    """解析赛迪 steelTypeRateDTOList，rate 从 0~1 转为 0~100"""
    if not dto_list:
        return []
    result: List[MaterialEntry] = []
    for d in dto_list:
        try:
            st = int(d.get("steelType")) if d.get("steelType") is not None else None
            sl = int(d.get("steelLevel") or 0)
            rate_raw = d.get("steelRate") or 0.0
            rate = float(rate_raw) * 100.0
        except (ValueError, TypeError) as e:
            logger.warning("AI 料型条目解析失败: %s (%s)", d, e)
            continue
        result.append(
            MaterialEntry(steel_type=st, steel_level=sl, rate=rate)
        )
    return _aggregate_levels(result)


def _aggregate_levels(entries: List[MaterialEntry]) -> List[MaterialEntry]:
    """对 AGGREGATED_STEEL_TYPES 内的 steelType，合并不同 level 的多条 entry。

    业务背景：
      "重废"现在不再细分 I/II 级。人工可能写"重废I级 30% + 重废II级 40%"，
      AI 视觉只输出"重废 70%"。这两侧要被视作"同一料型 70%"。

    合并规则：
      · steelType ∈ AGGREGATED_STEEL_TYPES → 同 steelType 的多条 entry 合并为 1 条
        - rate 累加
        - steel_level 归零
        - 在原列表中第一次出现的位置作为合并条目的位置（保留 pick_main 并列优先性）
        - raw_name 用 '+' 串接，调试用
      · 不在聚合集合的料型保持原样
    """
    if not entries:
        return entries

    out: List[MaterialEntry] = []
    aggregated_idx: dict[int, int] = {}  # steel_type → 在 out 中的下标
    for e in entries:
        if e.steel_type is None or e.steel_type not in AGGREGATED_STEEL_TYPES:
            out.append(e)
            continue
        if e.steel_type in aggregated_idx:
            target = out[aggregated_idx[e.steel_type]]
            target.rate += e.rate
            if e.raw_name:
                target.raw_name = (
                    f"{target.raw_name}+{e.raw_name}" if target.raw_name else e.raw_name
                )
            continue
        merged = MaterialEntry(
            steel_type=e.steel_type,
            steel_level=0,
            rate=e.rate,
            raw_name=e.raw_name,
        )
        aggregated_idx[e.steel_type] = len(out)
        out.append(merged)
    return out


def pick_main(entries: List[MaterialEntry]) -> Optional[MaterialEntry]:
    """取占比最高的主料型；并列时取列表中先出现者"""
    if not entries:
        return None
    return max(entries, key=lambda e: e.rate)


def safe_float(value: Any) -> Optional[float]:
    """把 "280.0" / 280 / None / "" 统一转为 float 或 None"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
