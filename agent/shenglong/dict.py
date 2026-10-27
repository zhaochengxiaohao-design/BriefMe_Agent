"""盛隆废钢料型字典

固化自盛隆后端枚举与页面展示名称：
  0  DEFAULT       （空值/未知）
  1  zhongfei1     重废1
  2  zhongfei2     重废2
  3  zhongfei3     重废3
  4  jianliao1     剪料1
  5  jianliao2     剪料2
  6  jianliao3     剪料3
  7  jianliao4     剪料4
  8  posuiliao1    破碎料1
  9  posuiliao2    破碎料2
 10  posuiliao3    破碎料3
 11  medium        中废
 12  shengtie      生铁
 13  houjian       厚剪
 14  gangjinqieli  钢筋切粒
 15  qicheke       汽车壳
 16  chaobiao      超标（参考型指标，主料判定时剔除）

与镔鑫字典（agent/scrap/dict.py）编码含义完全不同，严禁互相引用。
"""
from __future__ import annotations

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

# 检判原图文件名用的英文码（与页面/数据集约定一致）
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

# 参考型：不参与主料判定，只作为辅助维度统计（超尺寸废钢占比）
REFERENCE_TYPES: frozenset[int] = frozenset({16})

# 空值编码（过滤掉不进入任何统计）
EMPTY_TYPES: frozenset[int] = frozenset({0})

# 新准确率口径专用：只评估重废1/2/3，并把这三类占比归一化到 100%
HEAVY_STEEL_TYPES: frozenset[int] = frozenset({1, 2, 3})

# 检判员黑名单：这些人是测试 / 管理员 / 离场人员，不参与人工合并
# 计算每辆车的"人工最终结果"时，会先把 checkDetails 里这些人的明细剔除，
# 再用剩下的人重新平均出 manual_materials/manual_deduct_ton/manual_steel_price。
# 如果剔除后只剩 0 个人，整辆车视为"人工结果缺失"——不计入识别率/扣重符合率分母。
EXCLUDED_OPERATORS: frozenset[str] = frozenset({
    "施宏波",
    "冉星明",
    "周倩",
    "王宇泰",
    "王重阳",
})


def is_excluded_operator(name: Optional[str]) -> bool:
    """判定一个人工检判员是否在黑名单中（按姓名严格匹配，去除两端空白）"""
    if not name:
        return False
    return str(name).strip() in EXCLUDED_OPERATORS

# 反向映射：中文 → 编码（调试/Excel 兼容用途）
STEEL_TYPE_NAME_TO_CODE: dict[str, int] = {
    v: k for k, v in STEEL_TYPE.items() if v
}

# 盛隆废钢当前生效单价（元/吨）
STEEL_TYPE_PRICE: dict[int, float] = {
    1: 2300.0,  # 重废1
    2: 2280.0,  # 重废2
    3: 2260.0,  # 重废3
    4: 2170.0,  # 剪料1
    5: 2130.0,  # 剪料2
    6: 2070.0,  # 剪料3
    7: 1320.0,  # 剪料4
    8: 2280.0,  # 破碎料1
    9: 2250.0,  # 破碎料2
    10: 2210.0, # 破碎料3
    11: 2240.0, # 中废
    12: 2230.0, # 生铁
    13: 2210.0, # 厚剪
    14: 2350.0, # 钢筋切粒
    15: 2010.0, # 汽车壳
}
# 依据业务截图从上到下定义的优先级（数字越小，平局时优先级越高）。用于决出主料。
MATERIAL_PRIORITY: dict[int, int] = {
    1: 1,    # 重废1
    2: 2,    # 重废2
    3: 3,    # 重废3
    12: 4,   # 生铁
    11: 5,   # 中废
    13: 6,   # 厚剪
    4: 7,    # 剪料1
    5: 8,    # 剪料2
    6: 9,    # 剪料3
    7: 10,   # 剪料4
    8: 11,   # 破碎料1
    9: 12,   # 破碎料2
    10: 13,  # 破碎料3
    14: 14,  # 钢筋切粒
    15: 15,  # 汽车壳
}


def get_material_en(steel_type: Optional[int]) -> str:
    """把 steelType 编码转为英文码；空/未知返回 unknown{code}。"""
    if steel_type is None:
        return "unknown"
    code = int(steel_type)
    if code in EMPTY_TYPES:
        return "unknown"
    return STEEL_TYPE_EN.get(code, f"unknown{code}")


def get_material_name(steel_type: Optional[int]) -> str:
    """把 steelType 编码转为中文名；空/未知返回 "--"。

    Examples:
        >>> get_material_name(1)
        '重废1'
        >>> get_material_name(11)
        '中废'
        >>> get_material_name(0)
        '--'
        >>> get_material_name(None)
        '--'
    """
    if steel_type is None:
        return "--"
    code = int(steel_type)
    if code in EMPTY_TYPES:
        return "--"
    return STEEL_TYPE.get(code, f"未知{code}")


def is_valid(steel_type: Optional[int]) -> bool:
    """是否为"参与主料判定"的合法料型（非空值、非参考型）"""
    if steel_type is None:
        return False
    code = int(steel_type)
    if code in EMPTY_TYPES or code in REFERENCE_TYPES:
        return False
    return code in STEEL_TYPE


def filter_main_candidates(
    items: Iterable[Tuple[Optional[int], float]],
) -> List[Tuple[int, float]]:
    """从 (steelType, rate) 列表中剔除空值和参考型，返回合法候选列表"""
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


def get_tied_main_types(
    items: Iterable[Tuple[Optional[int], float]],
    *,
    eps: float = 1e-6,
) -> List[Tuple[int, float]]:
    """返回所有并列最高占比的合法主料型。

    例如重废1 40%、重废2 40% 会同时返回这两项，供「任一命中即正确」判定。
    """
    filtered = filter_main_candidates(items)
    if not filtered:
        return []
    top = max(rate for _, rate in filtered)
    return [(st, rate) for st, rate in filtered if abs(rate - top) <= eps]


def get_main_type_from_list(
    items: Iterable[Tuple[Optional[int], float]],
) -> Optional[Tuple[int, float]]:
    """从料型占比列表中取占比最高的合法主料型（展示用，平局走优先级）。

    Args:
        items: 形如 [(steelType, rate), ...]；rate 建议统一到"百分比"或"0~1"任一种口径

    Returns:
        (steelType, rate) 或 None（列表为空 / 全部是空值/参考型）
    """
    filtered = filter_main_candidates(items)
    if not filtered:
        return None

    # 1. 优先按 rate 降序
    # 2. 当 rate 相同时，按 MATERIAL_PRIORITY 升序（越小越优先，找不到的给999垫底）
    filtered.sort(key=lambda x: (-x[1], MATERIAL_PRIORITY.get(x[0], 999)))

    return filtered[0]

def lookup_rate_of(
    items: Iterable[Tuple[Optional[int], float]], steel_type: int
) -> Optional[float]:
    """在料型占比列表中查某个 steelType 的占比；找不到返回 None"""
    target = int(steel_type)
    for st, rate in items:
        if st is None:
            continue
        if int(st) == target:
            try:
                return float(rate)
            except (TypeError, ValueError):
                return None
    return None
