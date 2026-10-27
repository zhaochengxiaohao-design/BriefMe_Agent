"""料型字典

固化自前端 fcs-web/assets/common-DqUQsKG1.js：
  steelType: 1=精炉料 2=杂摸 3=重废 4=中废 5/6/7=冲豆201/202/203 8=钢筋切头 9=滚剪钢筋切头
  steelLevel: 0=无 1=I级 2=II级 3=III级
"""
from __future__ import annotations

from typing import Optional, Tuple

STEEL_TYPE: dict[int, str] = {
    1: "精炉料",
    2: "杂摸",
    3: "重废",
    4: "中废",
    5: "冲豆201",
    6: "冲豆202",
    7: "冲豆203",
    8: "钢筋切头",
    9: "滚剪钢筋切头",
}

STEEL_LEVEL: dict[int, str] = {
    0: "",
    1: "I级",
    2: "II级",
    3: "III级",
}

# 前端字典里的合法 (steelType, steelLevel) 组合
VALID_COMBOS: set[str] = {
    "1-1", "1-2", "1-3",
    "2-0",
    "3-1", "3-2",
    "4-0",
    "5-0", "6-0", "7-0",
    "8-0", "9-0",
}

# 人工 manualResults 里使用的中文等级 → level 编码
LEVEL_CN_TO_CODE: dict[str, int] = {
    "等级一": 1, "一级": 1, "I级": 1, "Ⅰ级": 1,
    "等级二": 2, "二级": 2, "II级": 2, "Ⅱ级": 2,
    "等级三": 3, "三级": 3, "III级": 3, "Ⅲ级": 3,
}

# 反向：steelType 中文名 → 编码
STEEL_TYPE_NAME_TO_CODE: dict[str, int] = {v: k for k, v in STEEL_TYPE.items()}
# 兼容用户文档中的错字"杂模"
STEEL_TYPE_NAME_TO_CODE["杂模"] = 2

# 聚合料型：在主料判定/比较时不再细分 steelLevel。
#   3 = 重废
# 业务理由：现场视觉系统现在只输出"重废"（不分 I/II 级），但人工可能写
# "重废I级 30% + 重废II级 40%"。这两类要视作同一料型。
# 解析层会把这些料型的多条等级条目合并成 level=0 的一条；
# `is_same_material` 也对这些类型只比 steelType，作为双保险。
AGGREGATED_STEEL_TYPES: frozenset[int] = frozenset({3})


def get_material_name(steel_type: Optional[int], steel_level: Optional[int]) -> str:
    """把 (steelType, steelLevel) 组合转为可读中文名。

    Examples:
        >>> get_material_name(1, 2)  # 精炉料II级
        '精炉料II级'
        >>> get_material_name(4, 0)  # 中废
        '中废'
        >>> get_material_name(None, None)
        '--'
    """
    if steel_type is None:
        return "--"
    type_name = STEEL_TYPE.get(int(steel_type), f"未知{steel_type}")
    level_name = STEEL_LEVEL.get(int(steel_level or 0), "")
    return f"{type_name}{level_name}"


def is_same_material(
    a: Tuple[Optional[int], Optional[int]],
    b: Tuple[Optional[int], Optional[int]],
) -> bool:
    """判断两个 (steelType, steelLevel) 组合是否属于同一个料型。

    规则：
      - 任一方 steelType 为 None → 不一致
      - steelType 不同 → 不一致
      - steelType ∈ AGGREGATED_STEEL_TYPES（如重废）→ 不再比 steelLevel，直接一致
      - 其它情况 → steelLevel 也必须相等（None 归一化为 0）
    """
    a_type, a_level = a
    b_type, b_level = b
    if a_type is None or b_type is None:
        return False
    if int(a_type) != int(b_type):
        return False
    if int(a_type) in AGGREGATED_STEEL_TYPES:
        return True
    return int(a_level or 0) == int(b_level or 0)
