"""废钢检判数据结构"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


def _normalize_station_number(value: Any) -> int | str:
    """兼容 stationNumber 返回数字、字符串或列表。"""
    if value is None or value == "":
        return 0
    if isinstance(value, (list, tuple, set)):
        parts = [
            str(_normalize_station_number(v))
            for v in value
            if v is not None and v != ""
        ]
        if not parts:
            return 0
        if len(parts) == 1:
            single = parts[0]
            return int(single) if single.isdigit() else single
        return "/".join(parts)
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value).strip() or 0


@dataclass
class MaterialEntry:
    """单个料型条目（人工或赛迪的料型列表里的一项）"""

    steel_type: Optional[int]  # 1~9，未知为 None
    steel_level: Optional[int]  # 0~3
    rate: float  # 占比百分比（0~100）
    raw_name: str = ""  # 人工原始字符串片段，调试用

    @property
    def key(self) -> str:
        """(steelType, steelLevel) 组合键，例如 '4-0'"""
        t = self.steel_type if self.steel_type is not None else "?"
        l = self.steel_level if self.steel_level is not None else 0
        return f"{t}-{l}"


@dataclass
class ScrapRecord:
    """列表接口单条记录（轻量）"""

    flow_code: str
    car_number: str
    station_number: int | str
    create_time: str  # "YYYY-MM-DD HH:MM:SS"
    status: int
    check_type: int
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_list_item(cls, item: dict) -> "ScrapRecord":
        return cls(
            flow_code=item.get("flowCode", ""),
            car_number=item.get("carNumber", ""),
            station_number=_normalize_station_number(item.get("stationNumber")),
            create_time=item.get("createTime", ""),
            status=int(item.get("status") or 0),
            check_type=int(item.get("checkType") or 0),
            raw=item,
        )


@dataclass
class TruckStat:
    """单车计算后的完整结果，xlsx 每一行对应一个"""

    date: str  # "YYYY-MM-DD"
    car_number: str
    station_number: int | str
    flow_code: str

    # 人工结果
    manual_materials: List[MaterialEntry] = field(default_factory=list)
    manual_main: Optional[MaterialEntry] = None  # 主料型
    manual_deduct_kg: Optional[float] = None  # 人工扣重

    # 赛迪/AI 结果
    ai_materials: List[MaterialEntry] = field(default_factory=list)
    ai_main: Optional[MaterialEntry] = None
    ai_deduct_kg: Optional[float] = None

    # 派生指标（None 表示无法计算）
    main_same: Optional[bool] = None  # 主料型是否一致
    diff_rate: Optional[float] = None  # 差异率 %（0~100）
    weight_diff: Optional[float] = None  # ABS(人工kg - 赛迪kg)
    weight_ratio: Optional[float] = None  # 赛迪kg / 人工kg

    # 统计资格：人工缺失 or 人工主料型为中废/杂摸 → False
    is_eligible: bool = False

    # 错判渲染图（用于下载）
    error_render_images: List[str] = field(default_factory=list)


@dataclass
class DailyScrapStats:
    """每日汇总"""

    date: str
    total_trucks: int = 0  # 全部车数（含中废/杂模）
    eligible_trucks: int = 0  # 参与统计车数
    main_same_count: int = 0  # 主料型一致车数

    avg_error_rate: Optional[float] = None  # %，参与车平均
    avg_weight_diff: Optional[float] = None  # kg，参与车平均
    avg_weight_ratio: Optional[float] = None  # 比值，参与车平均

    trucks: List[TruckStat] = field(default_factory=list)

    @property
    def accuracy_rate(self) -> Optional[float]:
        """主料型准确率 %"""
        if self.eligible_trucks == 0:
            return None
        return self.main_same_count / self.eligible_trucks * 100.0

    def summary_text(
        self,
        target_accuracy: float = 0.95,
        target_avg_error_rate: float = 0.10,
        target_weight_diff_kg: float = 100.0,
        target_weight_ratio_lower: float = 0.5,
        target_weight_ratio_upper: float = 1.5,
    ) -> str:
        """按文档格式生成 4 条文本汇总。"""
        y, m, d = self.date.split("-")
        header = f"{int(y)}年{int(m)}月{int(d)}日赛迪共检判 {self.total_trucks} 车；"

        lines = [header]

        acc = self.accuracy_rate
        acc_s = "N/A" if acc is None else f"{acc:.2f}%"
        acc_ok = (
            "达标"
            if acc is not None and acc >= target_accuracy * 100.0
            else "未达标"
        )
        lines.append(
            f"1. 主料型准确率：赛迪 {acc_s}（正确 {self.main_same_count} 辆）；"
            f"（目标值 {int(target_accuracy*100)}%）- {acc_ok}"
        )

        err_s = "N/A" if self.avg_error_rate is None else f"{self.avg_error_rate:.2f}%"
        err_ok = (
            "达标"
            if self.avg_error_rate is not None
            and self.avg_error_rate <= target_avg_error_rate * 100.0
            else "未达标"
        )
        lines.append(
            f"2. 料型占比误差率：赛迪平均误差率 {err_s}；"
            f"（目标值小于 {int(target_avg_error_rate*100)}%）- {err_ok}"
        )

        wd_s = "N/A" if self.avg_weight_diff is None else f"{self.avg_weight_diff:.2f}Kg"
        wd_ok = (
            "达标"
            if self.avg_weight_diff is not None
            and self.avg_weight_diff <= target_weight_diff_kg
            else "未达标"
        )
        lines.append(
            f"3. 扣重重量差值：赛迪 {wd_s}；"
            f"（目标值 ±{int(target_weight_diff_kg)}Kg）- {wd_ok}"
        )

        wr_s = "N/A" if self.avg_weight_ratio is None else f"{self.avg_weight_ratio:.2f}"
        wr_ok = (
            "达标"
            if self.avg_weight_ratio is not None
            and target_weight_ratio_lower
            <= self.avg_weight_ratio
            <= target_weight_ratio_upper
            else "未达标"
        )
        lines.append(
            f"4. 扣重占比值：赛迪 {wr_s}；"
            f"（目标值 {target_weight_ratio_lower}～{target_weight_ratio_upper}）- {wr_ok}"
        )

        # Markdown 里单个 \n 不换行，必须 \n\n 段落分隔或行尾 '  \n' 硬换行；
        # 另外避免 ~ 字符被当作删除线配对符，已把半角 ~ 换成全角 ～。
        return "\n\n".join(lines)
