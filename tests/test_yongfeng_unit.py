"""永锋烧结矿准确率模块 smoke 测试（不需要连接真实 VPN / API）

运行：
  python tests/test_yongfeng_unit.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TENCENT_DOCS_AUTO_UPLOAD"] = "0"

from agent.yongfeng.calculator import compute_accuracy
from agent.yongfeng.excel_writer import write_report


def _sample_raw():
    # 直接模拟你给的 response 结构，覆盖 inspectResult=Y/N 两种情况，确保不依赖网络
    return {
        "meta": {
            "startTime": "2026-05-17 00:00:00",
            "endTime": "2026-05-17 23:59:59",
            "generatedAt": "2026-05-17 23:59:59",
        },
        "manual_1": [
            {
                "time": "2026-05-17 10:08:00",
                "0-5mm": 2.83,
                "5-10mm": 17.49,
                "10-25mm": 46.67,
                "25-40mm": 22.23,
                ">40mm": 10.78,
                # 这个字段在 response 里会出现，说明应保留
                "inspectResult": "Y",
            },
            {
                "time": "2026-05-17 02:31:00",
                "0-5mm": 2.80,
                "5-10mm": 16.91,
                "10-25mm": 44.74,
                "25-40mm": 25.11,
                ">40mm": 10.44,
                # 这个字段模拟不合格，后续可用于过滤验证
                "inspectResult": "N",
            },
        ],
        "manual_2": [
            {
                "time": "2026-05-17 06:04:00",
                "0-5mm": 2.81,
                "5-10mm": 21.35,
                "10-25mm": 41.78,
                "25-40mm": 24.63,
                ">40mm": 9.43,
                "inspectResult": "Y",
            }
        ],
        "visual_1": [
            {"time": "2026-05-17 06:04:00", "0-5mm": 2.81, "5-10mm": 21.35, "10-25mm": 41.78, "25-40mm": 24.63, ">40mm": 9.43},
            {"time": "2026-05-17 10:08:00", "0-5mm": 2.83, "5-10mm": 17.49, "10-25mm": 46.67, "25-40mm": 22.23, ">40mm": 10.78},
            {"time": "2026-05-17 02:31:00", "0-5mm": 2.80, "5-10mm": 16.91, "10-25mm": 44.74, "25-40mm": 25.11, ">40mm": 10.44},
        ],
        "visual_2": [
            {"time": "2026-05-17 06:04:00", "0-5mm": 2.91, "5-10mm": 22.09, "10-25mm": 43.89, "25-40mm": 23.19, ">40mm": 7.92},
            {"time": "2026-05-17 10:08:00", "0-5mm": 2.83, "5-10mm": 17.49, "10-25mm": 46.67, "25-40mm": 22.23, ">40mm": 10.78},
            {"time": "2026-05-17 02:31:00", "0-5mm": 2.80, "5-10mm": 16.91, "10-25mm": 44.74, "25-40mm": 25.11, ">40mm": 10.44},
        ],
    }


def test_compute_accuracy_smoke():
    result = compute_accuracy(_sample_raw())
    assert "1#" in result and "2#" in result
    assert isinstance(result["1#"]["rows"], list)
    assert isinstance(result["2#"]["rows"], list)
    # 有可计算的视觉记录时，至少应产出一行
    assert len(result["1#"]["rows"]) >= 1
    assert len(result["2#"]["rows"]) >= 1

    row1 = result["1#"]["rows"][0]
    assert row1["time"] == "2026/05/17 10:08"
    assert row1["n_visual"] >= 1
    assert row1["visual_0-5mm"] is not None
    assert row1["mae"] is not None

    # inspectResult=Y/N 过滤逻辑应当能在上游数据整理阶段工作
    # 这里至少验证不影响正常样本计算。
    assert result["2#"]["rows"][0]["time"] == "2026/05/17 06:04"


def test_write_report_smoke():
    result = compute_accuracy(_sample_raw())
    with TemporaryDirectory() as td:
        out = Path(td) / "accuracy_report.xlsx"
        write_report(out, result)
        assert out.exists()
        assert out.stat().st_size > 0


if __name__ == "__main__":
    test_compute_accuracy_smoke()
    test_write_report_smoke()
    print("yongfeng smoke OK")
