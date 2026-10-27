"""废钢检判业务计算

单车指标：
  - 主料型一致性
  - 差异率（主料型一致=ABS差/人工%，不一致=100%）
  - 扣重差值 ABS(人工kg - 赛迪kg)
  - 扣重占比 赛迪kg / 人工kg

日汇总：
  - 准确率 = 主料型一致车数 / 参与统计车数
  - 平均误差率 = 参与车差异率平均
  - 平均扣重差值、平均扣重占比

过滤：人工主料型 steelType ∈ {2,4} 的车次不参与统计，但保留在表格
人工缺失：不参与统计，保留在表格

周度料型统计：
  - 按赛迪主料型分组，统计各料型的车数、准确率、占比差异、扣重误差等指标
  - 支持赛迪/用友数据源筛选
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.scrap.dict import STEEL_TYPE, is_same_material
from agent.scrap.models import DailyScrapStats, MaterialEntry, TruckStat
from agent.scrap.parser import (
    parse_ai_materials,
    parse_manual_results,
    pick_main,
    safe_float,
)

logger = logging.getLogger(__name__)

_FIXED_ORDER: Dict[str, int] = {
    "精炉料I级": 0,
    "精炉料II级": 1,
    "精炉料III级": 2,
    "重废": 3,
    "中废": 4,
    "杂摸": 5,
}

_FIXED_TYPES = (
    (1, 1, "精炉料I级"),
    (1, 2, "精炉料II级"),
    (1, 3, "精炉料III级"),
    (3, 0, "重废"),
    (4, 1, "中废"),
    (2, 0, "杂摸"),
)

_EXCLUDED_STEEL_TYPES = {2, 4}


def _material_display_name(steel_type: int, steel_level: int) -> str:
    name = STEEL_TYPE.get(steel_type, f"未知{steel_type}")
    if steel_type == 3:
        return "重废"
    if steel_level > 0 and steel_type == 1:
        level_map = {1: "I级", 2: "II级", 3: "III级"}
        return f"{name}{level_map.get(steel_level, '')}"
    return name


@dataclass
class WeeklyTypeRow:
    """周度各料型统计单行"""

    material_name: str
    steel_type: int
    steel_level: int
    truck_count: int = 0
    main_correct_count: int = 0
    main_accuracy_pct: Optional[float] = None
    ratio_within_10pct_count: int = 0
    ratio_accuracy_pct: Optional[float] = None
    weight_within_100kg_count: int = 0
    avg_weight_diff_kg: Optional[float] = None
    weight_accuracy_pct: Optional[float] = None
    deduct_ratio: Optional[float] = None


@dataclass
class WeeklyTypeReport:
    """周度料型统计完整报告"""

    start_date: str
    end_date: str
    rows: List[WeeklyTypeRow] = field(default_factory=list)
    overall_main_same_pct: Optional[float] = None
    overall_ratio_within_10pct_pct: Optional[float] = None
    overall_avg_weight_diff_kg: Optional[float] = None
    overall_deduct_ratio: Optional[float] = None
    overall_truck_count: int = 0
    overall_eligible_count: int = 0
    overall_main_same_count: int = 0
    overall_ratio_within_10pct_count: int = 0
    overall_weight_within_100kg_count: int = 0
    no_exclude_truck_count: int = 0
    no_exclude_eligible_count: int = 0
    no_exclude_main_same_count: int = 0
    no_exclude_main_same_pct: Optional[float] = None
    no_exclude_ratio_within_10pct_count: int = 0
    no_exclude_ratio_within_10pct_pct: Optional[float] = None
    no_exclude_weight_within_100kg_count: int = 0
    no_exclude_weight_within_100kg_pct: Optional[float] = None
    no_exclude_avg_weight_diff_kg: Optional[float] = None
    no_exclude_deduct_ratio: Optional[float] = None

    summary_text: str = ""


@dataclass
class _GroupStats:
    eligible_count: int = 0
    main_same_count: int = 0
    main_same_pct: Optional[float] = None
    ratio_10pct_count: int = 0
    ratio_10pct_pct: Optional[float] = None
    weight_100kg_count: int = 0
    weight_100kg_pct: Optional[float] = None
    avg_weight_diff_kg: Optional[float] = None
    deduct_ratio: Optional[float] = None


def _compute_group_stats(group: List[TruckStat]) -> _GroupStats:
    eligible = [t for t in group if t.is_eligible]
    n = len(group)
    if n == 0:
        return _GroupStats()

    main_same = sum(1 for t in eligible if t.main_same is True)
    ratio_10pct = sum(
        1 for t in eligible
        if t.diff_rate is not None and t.diff_rate <= 10.0
    )
    all_weight_diffs = [
        t.weight_diff for t in eligible
        if t.weight_diff is not None
    ]
    within_100kg_diffs = [wd for wd in all_weight_diffs if wd <= 100.0]
    avg_wd_all = (
        round(sum(all_weight_diffs) / len(all_weight_diffs), 2)
        if all_weight_diffs else None
    )
    weight_100kg = len(within_100kg_diffs)
    ratios = [t.weight_ratio for t in eligible if t.weight_ratio is not None]
    avg_ratio = round(sum(ratios) / len(ratios), 2) if ratios else None

    return _GroupStats(
        eligible_count=n,
        main_same_count=main_same,
        main_same_pct=(main_same / n * 100.0) if n > 0 else None,
        ratio_10pct_count=ratio_10pct,
        ratio_10pct_pct=(ratio_10pct / n * 100.0) if n > 0 else None,
        weight_100kg_count=weight_100kg,
        weight_100kg_pct=(weight_100kg / n * 100.0) if n > 0 else None,
        avg_weight_diff_kg=avg_wd_all,
        deduct_ratio=avg_ratio,
    )


def calc_weekly_type_stats(
    trucks: List[TruckStat],
    start_date: str,
    end_date: str,
    source_filter: Optional[int] = None,
) -> WeeklyTypeReport:
    """对一批单车计算各主料型的周度统计。

    Args:
        trucks: 已计算好的单车列表
        start_date / end_date: 日期范围
        source_filter: 保留参数兼容性（当前仅赛迪数据源，无需过滤）
    """
    filtered = trucks

    if not filtered:
        report = WeeklyTypeReport(start_date=start_date, end_date=end_date)
        report.rows = [
            WeeklyTypeRow(material_name=d[2], steel_type=d[0], steel_level=d[1])
            for d in _FIXED_TYPES
        ]
        return report

    by_type: Dict[tuple, List[TruckStat]] = {}
    for t in filtered:
        if t.manual_main is None:
            continue
        key = (t.manual_main.steel_type or 0, t.manual_main.steel_level or 0)
        by_type.setdefault(key, []).append(t)

    row_map: Dict[tuple, WeeklyTypeRow] = {}
    for (st, sl), group in by_type.items():
        eligible_group = [t for t in group if t.is_eligible]
        n = len(group)
        e = len(eligible_group)
        if e > 0:
            main_correct = sum(1 for t in eligible_group if t.main_same is True)
            ratio_within_10pct = sum(
                1 for t in eligible_group
                if t.diff_rate is not None and t.diff_rate <= 10.0
            )
            weight_within_100kg = sum(
                1 for t in eligible_group
                if t.weight_diff is not None and t.weight_diff <= 100.0
            )
            all_weight_diffs = [
                t.weight_diff for t in eligible_group
                if t.weight_diff is not None
            ]
            avg_wd_all = round(
                sum(all_weight_diffs) / len(all_weight_diffs), 2
            ) if all_weight_diffs else None
            ratios = [
                t.weight_ratio for t in eligible_group
                if t.weight_ratio is not None
            ]
            avg_ratio = round(sum(ratios) / len(ratios), 2) if ratios else None

            row = WeeklyTypeRow(
                material_name=_material_display_name(st, sl),
                steel_type=st,
                steel_level=sl,
                truck_count=n,
                main_correct_count=main_correct,
                main_accuracy_pct=(main_correct / n * 100.0) if n > 0 else None,
                ratio_within_10pct_count=ratio_within_10pct,
                ratio_accuracy_pct=(
                    ratio_within_10pct / main_correct * 100.0
                ) if main_correct > 0 else None,
                weight_within_100kg_count=weight_within_100kg,
                avg_weight_diff_kg=avg_wd_all,
                weight_accuracy_pct=(
                    weight_within_100kg / n * 100.0
                ) if n > 0 else None,
                deduct_ratio=avg_ratio,
            )
        else:
            row = WeeklyTypeRow(
                material_name=_material_display_name(st, sl),
                steel_type=st,
                steel_level=sl,
                truck_count=n,
            )
        row_map[(st, sl)] = row

    rows: List[WeeklyTypeRow] = []
    for st, sl, name in _FIXED_TYPES:
        key = (st, sl)
        existing = row_map.pop(key, None)
        if existing:
            rows.append(existing)
        else:
            rows.append(WeeklyTypeRow(
                material_name=name, steel_type=st, steel_level=sl,
            ))

    remaining = sorted(
        row_map.values(),
        key=lambda r: (_FIXED_ORDER.get(r.material_name, 99), r.material_name),
    )
    rows.extend(remaining)

    all_with_manual = [t for t in filtered if t.manual_main is not None]

    overall = _compute_group_stats(all_with_manual)

    no_exclude_trucks = [
        t for t in all_with_manual
        if (t.manual_main.steel_type or 0) not in _EXCLUDED_STEEL_TYPES
    ]
    no_exclude = _compute_group_stats(no_exclude_trucks)

    report = WeeklyTypeReport(
        start_date=start_date,
        end_date=end_date,
        rows=rows,
        overall_truck_count=len(all_with_manual),
        overall_eligible_count=overall.eligible_count,
        overall_main_same_count=overall.main_same_count,
        overall_main_same_pct=overall.main_same_pct,
        overall_ratio_within_10pct_count=overall.ratio_10pct_count,
        overall_ratio_within_10pct_pct=overall.ratio_10pct_pct,
        overall_weight_within_100kg_count=overall.weight_100kg_count,
        overall_avg_weight_diff_kg=overall.avg_weight_diff_kg,
        overall_deduct_ratio=overall.deduct_ratio,
        no_exclude_truck_count=len(no_exclude_trucks),
        no_exclude_eligible_count=no_exclude.eligible_count,
        no_exclude_main_same_count=no_exclude.main_same_count,
        no_exclude_main_same_pct=no_exclude.main_same_pct,
        no_exclude_ratio_within_10pct_count=no_exclude.ratio_10pct_count,
        no_exclude_ratio_within_10pct_pct=no_exclude.ratio_10pct_pct,
        no_exclude_weight_within_100kg_count=no_exclude.weight_100kg_count,
        no_exclude_weight_within_100kg_pct=no_exclude.weight_100kg_pct,
        no_exclude_avg_weight_diff_kg=no_exclude.avg_weight_diff_kg,
        no_exclude_deduct_ratio=no_exclude.deduct_ratio,
    )

    logger.info(
        "[镔鑫周度料型] %s~%s 整体%d车(eligible%d) 不含中废杂模%d车(eligible%d) 准确率%.2f%%",
        start_date, end_date,
        len(all_with_manual), overall.eligible_count,
        len(no_exclude_trucks), no_exclude.eligible_count,
        no_exclude.main_same_pct or 0,
    )
    return report


def calc_truck(
    date_str: str,
    car_number: str,
    station_number: int | str,
    flow_code: str,
    detail_data: dict,
    exclude_steel_types: tuple = (2, 4),
) -> TruckStat:
    """对一辆车的详情 data 做完整计算。

    Args:
        detail_data: getCheckDetail 返回的 data 字段（含 manualCheck、steelTypeRateDTOList 等）
    """
    stat = TruckStat(
        date=date_str,
        car_number=car_number,
        station_number=station_number,
        flow_code=flow_code,
    )

    manual_check = detail_data.get("manualCheck") or {}
    manual_text = manual_check.get("manualResults") or ""
    stat.manual_materials = parse_manual_results(manual_text)
    stat.manual_main = pick_main(stat.manual_materials)
    stat.manual_deduct_kg = safe_float(manual_check.get("deductionResults"))

    stat.ai_materials = parse_ai_materials(detail_data.get("steelTypeRateDTOList"))
    stat.ai_main = pick_main(stat.ai_materials)

    deduct_dto = detail_data.get("deductCalculationResultDTO") or {}
    stat.ai_deduct_kg = safe_float(
        deduct_dto.get("finalDeduct")
        if deduct_dto.get("finalDeduct") is not None
        else deduct_dto.get("calculatedDeductWeight")
    )

    manual_missing = (
        stat.manual_main is None
        or not stat.manual_materials
        or stat.manual_deduct_kg is None
    )

    manual_type = stat.manual_main.steel_type if stat.manual_main else None
    stat.is_eligible = (
        not manual_missing and manual_type not in exclude_steel_types
    )

    if stat.manual_main and stat.ai_main:
        same = is_same_material(
            (stat.manual_main.steel_type, stat.manual_main.steel_level),
            (stat.ai_main.steel_type, stat.ai_main.steel_level),
        )
        stat.main_same = same
        if same:
            p_man = stat.manual_main.rate
            p_ai = stat.ai_main.rate
            if p_man > 0:
                stat.diff_rate = abs(p_man - p_ai) / p_man * 100.0
            else:
                stat.diff_rate = 100.0 if p_ai > 0 else 0.0
        else:
            stat.diff_rate = 100.0

    if stat.manual_deduct_kg is not None and stat.ai_deduct_kg is not None:
        stat.weight_diff = abs(stat.manual_deduct_kg - stat.ai_deduct_kg)
        if stat.manual_deduct_kg != 0:
            stat.weight_ratio = stat.ai_deduct_kg / stat.manual_deduct_kg
        else:
            stat.weight_ratio = None

    if stat.main_same is False:
        one_check_list = detail_data.get("oneCheckSummaryDTOList") or []
        for o in one_check_list:
            url = o.get("deductWeightUrl") or o.get("originImageUrl")
            if url:
                stat.error_render_images.append(url)

    return stat


def aggregate_daily(date_str: str, trucks: List[TruckStat]) -> DailyScrapStats:
    """把一天的多辆车聚合为日汇总"""
    stats = DailyScrapStats(date=date_str, trucks=trucks)
    stats.total_trucks = len(trucks)

    eligible = [t for t in trucks if t.is_eligible]
    stats.eligible_trucks = len(eligible)

    if not eligible:
        return stats

    stats.main_same_count = sum(1 for t in eligible if t.main_same is True)

    diff_rates = [t.diff_rate for t in eligible if t.diff_rate is not None]
    if diff_rates:
        stats.avg_error_rate = sum(diff_rates) / len(diff_rates)

    weight_diffs = [t.weight_diff for t in eligible if t.weight_diff is not None]
    if weight_diffs:
        stats.avg_weight_diff = sum(weight_diffs) / len(weight_diffs)

    ratios = [t.weight_ratio for t in eligible if t.weight_ratio is not None]
    if ratios:
        stats.avg_weight_ratio = sum(ratios) / len(ratios)

    return stats
