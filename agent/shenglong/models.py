"""盛隆废钢检判数据结构

与镔鑫（agent.scrap.models）的主要差异：
  · 人工是 3 人平均（avgResult / avgDeduction / avgSteelPrice），明细在 checkDetails
  · 扣重：AI `totalDeductWeight` 单位 **kg**，人工 `avgDeduction` 单位 **吨**
    统计时统一转**吨**对比
  · 料型：不再有 steelLevel 概念，只有 steelType
  · 无 steelLevel → `主料型正确?` 只看 steelType 名字一致

扣重单位自动修正：
  · 业务约束：正常单车扣重在 0.1~2 吨；超过 10 吨的车在实际生产中会被
    直接退货而非进入正常检判流程，因此 > 10 视为人工录入时按 kg 录入
    但忘了换成吨，自动 /1000
  · 阈值在常量 ``DEDUCTION_KG_HEURISTIC_THRESHOLD`` 中可调
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from agent.shenglong.dict import get_material_name

logger = logging.getLogger(__name__)

# 人工检判行高亮：仅 1 人 / 多人主料型不一致 / 主料相同但任意两人占比差 > 10%
ManualHighlight = Literal["single", "disagree", "spread"]


# 扣重值若大于此阈值（吨），视为人工录入时单位写成了 kg，自动 / 1000
# 业务依据：正常单车扣重 0.1~2 吨，超过 10 吨的车会被直接退货不会进入检判流程
DEDUCTION_KG_HEURISTIC_THRESHOLD: float = 10.0


def _first_scalar(value):
    """兼容后端把单值字段包成 list 的情况。"""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _normalize_deduction(raw: Optional[float], operator_name: str = "") -> Optional[float]:
    """把疑似 kg 录入的扣重换算成吨。

    规则：值 > DEDUCTION_KG_HEURISTIC_THRESHOLD（默认 10 吨）→ 视为 kg，
         返回 raw / 1000；否则原样返回（包含 None / 0 / 负数）

    打 INFO 日志，便于事后审计哪些车次被自动修正。
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v > DEDUCTION_KG_HEURISTIC_THRESHOLD:
        corrected = v / 1000.0
        logger.info(
            "扣重单位自动修正: %s 录入 %.3f → 视为 kg → %.3f 吨",
            operator_name or "(unknown)", v, corrected,
        )
        return corrected
    return v


@dataclass
class MaterialRate:
    """单个料型占比条目：(steelType, rate)

    rate 统一为**百分比**（0~100），即便后端返 0~1，这里也会乘 100 归一。
    """

    steel_type: Optional[int]
    rate: float  # 0~100

    @property
    def name(self) -> str:
        return get_material_name(self.steel_type)


@dataclass
class ManualOperator:
    """单个人工质检员明细（checkDetails 里一条）"""

    name: str  # operatorName
    deduction_ton: Optional[float] = None  # 该员扣重，单位吨
    steel_price: Optional[float] = None  # 该员单价，元
    materials: List[MaterialRate] = field(default_factory=list)  # details[*]
    main: Optional[MaterialRate] = None  # 占比最高合法料型（剔除超标/DEFAULT）

    @classmethod
    def from_dict(cls, d: dict) -> "ManualOperator":
        """从 checkDetails[*] 直接构造"""
        from agent.shenglong.dict import get_main_type_from_list

        mats: List[MaterialRate] = []
        for item in d.get("details") or []:
            st = _first_scalar(item.get("steelType"))
            rate_raw = item.get("steelRate", 0)
            try:
                rate = float(rate_raw) * 100.0
            except (TypeError, ValueError):
                rate = 0.0
            mats.append(
                MaterialRate(
                    steel_type=int(st) if st is not None else None,
                    rate=rate,
                )
            )

        main_tup = get_main_type_from_list([(m.steel_type, m.rate) for m in mats])
        main_entry: Optional[MaterialRate] = None
        if main_tup is not None:
            main_entry = MaterialRate(steel_type=main_tup[0], rate=main_tup[1])

        operator_name = str(d.get("operatorName") or "").strip()
        return cls(
            name=operator_name,
            deduction_ton=_normalize_deduction(
                _safe_float(d.get("deduction")), operator_name
            ),
            steel_price=_safe_float(d.get("steelPrice")),
            materials=mats,
            main=main_entry,
        )


@dataclass
class ShenglongRecord:
    """列表接口单条记录（轻量）"""

    flow_code: str
    car_number: str
    station_number: int
    create_time: str  # "YYYY-MM-DD HH:MM:SS"
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_list_item(cls, item: dict) -> "ShenglongRecord":  # noqa: F821
        station_val = item.get("stationNumber")
        
        # 修复列表逻辑：非空列表才处理，空列表走 else 变成 0
        if isinstance(station_val, list) and station_val:
            if len(station_val) == 1:
                final_station = int(station_val[0])
            else:
                final_station = "/".join(str(x) for x in station_val)
        else:
            final_station = int(_first_scalar(station_val) or 0)

        return cls(
            flow_code=str(_first_scalar(item.get("flowCode")) or ""),
            car_number=str(_first_scalar(item.get("carNumber")) or ""),
            station_number=final_station,
            create_time=str(_first_scalar(item.get("createTime")) or ""),
            raw=item,
        )


@dataclass
class TruckStat:
    """单车完整统计（xlsx 一行）"""

    date: str  # "YYYY-MM-DD"
    car_number: str
    station_number: int
    flow_code: str

    # --- 人工汇总（3 人平均，已由后端算好） ---
    manual_materials: List[MaterialRate] = field(default_factory=list)  # avgResult
    manual_main: Optional[MaterialRate] = None
    manual_deduct_ton: Optional[float] = None  # avgDeduction（吨）
    manual_steel_price: Optional[float] = None  # avgSteelPrice（元）

    # 人工 3 人明细（0~3 人，不足时留空）
    manual_operators: List[ManualOperator] = field(default_factory=list)

    # --- AI（赛迪）汇总 ---
    ai_materials: List[MaterialRate] = field(default_factory=list)  # steelTypeRateList
    ai_main: Optional[MaterialRate] = None
    ai_deduct_ton: Optional[float] = None  # totalDeductWeight / 1000
    ai_steel_price: Optional[float] = None  # 目前无输出 → None
    final_steel_price: Optional[float] = None  # 最终结算单价（顶层 finalPrice）

    # --- 派生指标 ---
    main_name_match: Optional[bool] = None  # 仅料型名字一致（并列主料任一命中）
    main_same: Optional[bool] = None  # None=无法判定, True=名字一致 AND 差异<11%, False=不满足
    diff_rate: Optional[float] = None  # 差异值 %，命中时=abs(人工占比-AI对应料型占比)
    # 扣重
    weight_diff_ton: Optional[float] = None  # AI - 人工（带正负）
    weight_ratio: Optional[float] = None  # AI / 人工
    # 扣杂符合：0.5≤ratio≤1.5 OR |diff| < 0.151t
    deduction_compliant: Optional[bool] = None
    # 单价差异
    price_diff: Optional[float] = None  # |AI - 人工|

    # 人工检判质量：None=无需高亮
    manual_highlight: Optional[ManualHighlight] = None

    def manual_rate_of(self, steel_type: int) -> Optional[float]:
        """查人工 avgResult 中某料型的占比（%），找不到返回 None"""
        for m in self.manual_materials:
            if m.steel_type == steel_type:
                return m.rate
        return None

    def ai_rate_of(self, steel_type: int) -> Optional[float]:
        for m in self.ai_materials:
            if m.steel_type == steel_type:
                return m.rate
        return None


@dataclass
class DailyShenglongStats:
    """单日汇总"""

    date: str
    trucks: List[TruckStat] = field(default_factory=list)

    @property
    def total_trucks(self) -> int:
        return len(self.trucks)

    @property
    def judgable_trucks(self) -> int:
        """可判定主料型的车数（双方主料都非空）"""
        return sum(1 for t in self.trucks if t.main_same is not None)

    @property
    def main_name_match_count(self) -> int:
        """主料型名字一致的车数（不要求 <11% 差异）"""
        return sum(1 for t in self.trucks if t.main_name_match is True)

    @property
    def main_same_count(self) -> int:
        """主料型正确车数 = 名字一致 AND 差异 < 11%"""
        return sum(1 for t in self.trucks if t.main_same is True)

    @property
    def main_match_count(self) -> int:
        """主料识别车数 = 只要主料名字一致即可"""
        return sum(1 for t in self.trucks if t.main_name_match is True)

    @property
    def deduction_compliant_count(self) -> int:
        """扣杂符合的车数"""
        return sum(1 for t in self.trucks if t.deduction_compliant is True)

    @property
    def deduction_evaluable(self) -> int:
        """扣杂可评估的车数（AI 扣重与人工扣重均非空，且 AI 扣重不为 0）"""
        return sum(
            1
            for t in self.trucks
            if t.deduction_compliant is not None
            and t.ai_deduct_ton is not None
            and t.ai_deduct_ton != 0
        )

    @property
    def recognition_rate(self) -> Optional[float]:
        """识别率 R（%）：主料一致 / 可判定车数"""
        n = self.judgable_trucks
        if n == 0:
            return None
        return self.main_same_count / n * 100.0

    @property
    def deduction_compliance_rate(self) -> Optional[float]:
        """扣杂符合率（%）：扣杂准确 / 可评估扣杂车数（剔除 AI 扣重为 0 的车）"""
        n = self.deduction_evaluable
        if n == 0:
            return None
        return self.deduction_compliant_count / n * 100.0

    def summary_text(
        self,
        target_recognition_rate: float = 0.92,
        target_deduction_compliance_rate: float = 0.90,
    ) -> str:
        """按图3 汇总口径输出文本（Markdown 友好，段落分隔用 \n\n）"""
        y, m, d = self.date.split("-")
        header = (
            f"{int(y)}年{int(m)}月{int(d)}日盛隆赛迪共检判 {self.total_trucks} 车；"
        )
        lines = [header]

        r = self.recognition_rate
        r_s = "N/A" if r is None else f"{r:.2f}%"
        r_ok = (
            "达标"
            if r is not None and r >= target_recognition_rate * 100.0
            else "未达标"
        )
        lines.append(
            f"1. 识别准确率 R：赛迪 {r_s}（正确 {self.main_same_count}/"
            f"{self.judgable_trucks} 辆）；"
            f"（目标值 ≥{int(target_recognition_rate*100)}%）- {r_ok}"
        )

        c = self.deduction_compliance_rate
        c_s = "N/A" if c is None else f"{c:.2f}%"
        c_ok = (
            "达标"
            if c is not None and c >= target_deduction_compliance_rate * 100.0
            else "未达标"
        )
        lines.append(
            f"2. 扣杂符合率：赛迪 {c_s}（符合 {self.deduction_compliant_count}/"
            f"{self.deduction_evaluable} 辆）；"
            f"（目标值 ≥{int(target_deduction_compliance_rate*100)}%）- {c_ok}"
        )

        return "\n\n".join(lines)


@dataclass
class PeriodSummary:
    """周期级（多日）总体统计——对应参考表 sheet1「统计周期概括」14 行模板。

    分桶口径：
      · 价格差异 |人工单价 - AI单价| 分 4 档（左闭右开）：
          [0, 30) / [30, 50) / [50, 100) / [100, +∞)
        分母 = price_diff_evaluable（双方单价都非空的车次）
      · 识别准确率分母 = judgable_trucks（双方主料都非空的车次）
      · 扣杂符合率分母 = deduction_evaluable（双方扣重都非空的车次）
    """

    cycle_label: str  # 例如 "2026.4.14 至 2026.4.22"
    start_date: str  # "YYYY-MM-DD"
    end_date: str  # "YYYY-MM-DD"

    judgable_trucks: int = 0
    main_name_match_count: int = 0  # 主料型名字一致车数
    main_within_10pct_count: int = 0  # 其中差异<11% 车次数 (= main_same_count)

    # Sheet1 识别率区域文案。默认是原有主料型口径；重废归一化主表会覆盖这些文案。
    recognition_section_title: str = "识别率"
    recognition_condition1_label: str = "主料型相同车次数"
    recognition_condition2_label: str = "其中主料型占比差异\n小于10% 车次数"
    recognition_result_label: str = "综合识别准确率"
    cumulative_recognition_label: str = "累计准确率"
    recognition_match_label: str = "主料识别率"

    deduction_evaluable: int = 0
    deduction_compliant_count: int = 0

    price_diff_evaluable: int = 0
    price_diff_lt30: int = 0
    price_diff_30_50: int = 0
    price_diff_50_100: int = 0
    price_diff_gt100: int = 0

    # 上周期对比（小数 0~1，可选）。提供后会在 Sheet1 F/G 列写入"上周期结果"和"环比"。
    # · prev_recognition_rate: 上周期识别准确率（即 main_within_10pct_count / judgable_trucks）
    # · prev_deduction_compliance_rate: 上周期扣重符合率
    # · prev_cycle_label: 用于备注/调试的上周期标签（不影响公式）
    prev_recognition_rate: Optional[float] = None
    prev_deduction_compliance_rate: Optional[float] = None
    prev_cycle_label: Optional[str] = None

    # ====== 累计指标（多周期主表才用，单周期导出保持 None）======
    # 含义：从首期累计到本期（含）所有车次的累积值，便于"看趋势"
    #   cumulative_recognition_rate
    #     = sum(0..i, main_within_10pct_count) / sum(0..i, judgable_trucks)
    #   cumulative_deduction_compliance_rate
    #     = sum(0..i, deduction_compliant_count) / sum(0..i, deduction_evaluable)
    # 修复拼写：确保没有 sheet_ 前缀
    cumulative_recognition_rate: Optional[float] = None
    cumulative_deduction_compliance_rate: Optional[float] = None

    @property
    def main_name_match_rate_pct(self) -> Optional[float]:
        """主料型识别率（%）= 主料型相同车次 / 周期内有效检判车次"""
        if self.judgable_trucks == 0:
            return None
        return self.main_name_match_count / self.judgable_trucks * 100.0

    @property
    def recognition_rate_pct(self) -> Optional[float]:
        """综合识别准确率（%）= 主料正确（差异<11%）/ 周期内有效检判车次"""
        if self.judgable_trucks == 0:
            return None
        return self.main_within_10pct_count / self.judgable_trucks * 100.0

    @property
    def deduction_compliance_rate_pct(self) -> Optional[float]:
        """扣重符合率（%）= 符合车次 / 周期内有效扣重车次"""
        if self.deduction_evaluable == 0:
            return None
        return self.deduction_compliant_count / self.deduction_evaluable * 100.0


# ----------------------------------------------------------------------
#  工具函数
# ----------------------------------------------------------------------
def _unwrap_scalar(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _safe_float(value) -> Optional[float]:
    value = _unwrap_scalar(value)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
