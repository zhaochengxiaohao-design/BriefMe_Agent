"""废钢模块单元/组件 smoke 测试（不需要访问真实 API）

运行：
  /opt/anaconda3/bin/python tests/test_scrap_unit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scrap.calculator import aggregate_daily, calc_truck
from agent.scrap.dict import get_material_name, is_same_material
from agent.scrap.excel_writer import write_stats_xlsx
from agent.scrap.models import ScrapRecord
from agent.scrap.parser import parse_manual_results, pick_main


def test_dict():
    assert get_material_name(1, 2) == "精炉料II级"
    assert get_material_name(4, 0) == "中废"
    assert get_material_name(None, None) == "--"
    assert is_same_material((1, 2), (1, 2))
    assert not is_same_material((1, 2), (1, 1))
    assert not is_same_material((1, 2), (3, 2))
    assert is_same_material((4, 0), (4, 0))
    assert is_same_material((4, 0), (4, None))
    # 聚合料型：重废不再细分 I/II 级
    assert is_same_material((3, 0), (3, 0))
    assert is_same_material((3, 1), (3, 0))  # 重废1 vs 重废
    assert is_same_material((3, 2), (3, 0))  # 重废2 vs 重废
    assert is_same_material((3, 1), (3, 2))  # 重废1 vs 重废2
    print("dict OK")


def test_scrap_record_station_number_list():
    base = {
        "flowCode": "f1",
        "carNumber": "皖TEST",
        "createTime": "2026-04-14 10:00:00",
        "status": 1,
        "checkType": 1,
    }
    r1 = ScrapRecord.from_list_item({**base, "stationNumber": [36]})
    assert r1.station_number == 36
    r2 = ScrapRecord.from_list_item({**base, "stationNumber": [36, 53]})
    assert r2.station_number == "36/53"
    print("scrap record station list OK")


def test_parser():
    # 重废现在不细分等级 → 解析后 level 归零
    r = parse_manual_results("中废等级一50.00%,重废等级二50.00%")
    assert len(r) == 2
    assert r[0].steel_type == 4 and r[0].steel_level == 1 and r[0].rate == 50.0
    assert r[1].steel_type == 3 and r[1].steel_level == 0 and r[1].rate == 50.0

    r2 = parse_manual_results("精炉料一级95%")
    assert len(r2) == 1 and r2[0].steel_type == 1 and r2[0].steel_level == 1

    # 重废 II 级 80% → 解析后 level=0
    r3 = parse_manual_results("重废II级80%,中废20%")
    assert len(r3) == 2
    assert r3[0].steel_type == 3 and r3[0].steel_level == 0 and r3[0].rate == 80.0
    assert r3[1].steel_type == 4 and r3[1].steel_level == 0 and r3[1].rate == 20.0

    # 重废 I 级 + 重废 II 级 应被合并为 1 条 重废，rate 累加
    r5 = parse_manual_results("重废I级30%,重废II级40%,中废30%")
    assert len(r5) == 2
    rebei = next(e for e in r5 if e.steel_type == 3)
    zhongfei = next(e for e in r5 if e.steel_type == 4)
    assert rebei.steel_level == 0 and abs(rebei.rate - 70.0) < 1e-6
    assert zhongfei.steel_level == 0 and zhongfei.rate == 30.0

    r4 = parse_manual_results("")
    assert r4 == []

    main = pick_main(r)
    assert main.rate == 50.0
    print("parser OK")


def test_calc_and_aggregate():
    # 人工主=中废一级 50%, 赛迪主=中废一级 45% → 主料型一致；但中废在过滤集合里 → is_eligible=False
    d1 = {
        "manualCheck": {
            "manualResults": "中废等级一50.00%,重废等级二50.00%",
            "deductionResults": "280.0",
        },
        "steelTypeRateDTOList": [
            {"steelType": 4, "steelLevel": 1, "steelRate": 0.45},
            {"steelType": 3, "steelLevel": 2, "steelRate": 0.55},
        ],
        "deductCalculationResultDTO": {"calculatedDeductWeight": 310.0},
    }
    t1 = calc_truck("2026-04-15", "鲁A-0001", 1, "flow-1", d1)
    assert t1.manual_main.steel_type == 4
    assert t1.ai_main.steel_type == 3
    assert t1.main_same is False
    assert t1.diff_rate == 100.0
    assert t1.weight_diff == 30.0
    assert abs(t1.weight_ratio - 310.0 / 280.0) < 1e-6
    assert t1.is_eligible is False

    # 人工主=精炉料二级 80%, 赛迪主=精炉料二级 70% → 一致
    d2 = {
        "manualCheck": {
            "manualResults": "精炉料等级二80.00%,精炉料等级三20.00%",
            "deductionResults": "150.0",
        },
        "steelTypeRateDTOList": [
            {"steelType": 1, "steelLevel": 2, "steelRate": 0.7},
            {"steelType": 1, "steelLevel": 3, "steelRate": 0.3},
        ],
        "deductCalculationResultDTO": {"calculatedDeductWeight": 120.0},
    }
    t2 = calc_truck("2026-04-15", "鲁A-0002", 2, "flow-2", d2)
    assert t2.main_same is True
    expected = abs(80 - 70) / 80 * 100
    assert abs(t2.diff_rate - expected) < 1e-6
    assert t2.weight_diff == 30.0
    assert t2.is_eligible is True

    # 人工缺失 → skip stat, 不一致但不参与
    d3 = {"manualCheck": {"manualResults": "", "deductionResults": None},
          "steelTypeRateDTOList": [{"steelType": 1, "steelLevel": 2, "steelRate": 1.0}]}
    t3 = calc_truck("2026-04-15", "鲁A-0003", 3, "flow-3", d3)
    assert t3.manual_main is None
    assert t3.is_eligible is False

    # 重废聚合规则验证：
    # 人工 "重废I级30% + 重废II级40% + 中废30%" → 解析后 重废70% / 中废30% → 主料=重废70%
    # AI   "重废 80%"（无等级） → 主料=重废80%
    # → main_same=True；diff_rate=|70-80|/70*100≈14.286%
    d4 = {
        "manualCheck": {
            "manualResults": "重废I级30%,重废II级40%,中废30%",
            "deductionResults": "200.0",
        },
        "steelTypeRateDTOList": [
            {"steelType": 3, "steelLevel": 0, "steelRate": 0.8},
            {"steelType": 4, "steelLevel": 0, "steelRate": 0.2},
        ],
        "deductCalculationResultDTO": {"calculatedDeductWeight": 220.0},
    }
    t4 = calc_truck("2026-04-15", "鲁A-0004", 4, "flow-4", d4)
    assert t4.manual_main.steel_type == 3
    assert t4.manual_main.steel_level == 0
    assert abs(t4.manual_main.rate - 70.0) < 1e-6
    assert t4.ai_main.steel_type == 3
    assert t4.main_same is True
    expected_d4_diff = abs(70 - 80) / 70 * 100
    assert abs(t4.diff_rate - expected_d4_diff) < 1e-6
    assert t4.is_eligible is True

    stats = aggregate_daily("2026-04-15", [t1, t2, t3])
    assert stats.total_trucks == 3
    assert stats.eligible_trucks == 1  # 只有 t2
    assert stats.main_same_count == 1
    assert abs(stats.accuracy_rate - 100.0) < 1e-6
    assert abs(stats.avg_error_rate - expected) < 1e-6
    assert abs(stats.avg_weight_diff - 30.0) < 1e-6
    print("calc/aggregate OK")

    text = stats.summary_text()
    print("summary_text:\n" + text)
    assert "2026年4月15日" in text
    assert "3 车" in text
    assert "正确 1 辆" in text


def test_excel():
    # 制作小数据集，渲染 xlsx
    d_error = {
        "manualCheck": {"manualResults": "精炉料等级二80%", "deductionResults": "150"},
        "steelTypeRateDTOList": [{"steelType": 3, "steelLevel": 1, "steelRate": 0.9}],
        "deductCalculationResultDTO": {"calculatedDeductWeight": 180.0},
    }
    t_a = calc_truck("2026-04-15", "鲁A-0001", 1, "fa", d_error)
    d_ok = {
        "manualCheck": {"manualResults": "精炉料等级二80%", "deductionResults": "150"},
        "steelTypeRateDTOList": [{"steelType": 1, "steelLevel": 2, "steelRate": 0.72}],
        "deductCalculationResultDTO": {"calculatedDeductWeight": 140.0},
    }
    t_b = calc_truck("2026-04-15", "鲁A-0002", 2, "fb", d_ok)
    stats_1 = aggregate_daily("2026-04-15", [t_a, t_b])

    t_c = calc_truck("2026-04-16", "鲁A-0003", 1, "fc", d_ok)
    stats_2 = aggregate_daily("2026-04-16", [t_c])

    out = Path("downloads/scrap/_unit_test/report.xlsx")
    write_stats_xlsx([stats_1, stats_2], out)
    assert out.exists() and out.stat().st_size > 1000
    print(f"excel OK → {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    test_dict()
    test_scrap_record_station_number_list()
    test_parser()
    test_calc_and_aggregate()
    test_excel()
    print("\nAll scrap unit smoke tests PASSED")
