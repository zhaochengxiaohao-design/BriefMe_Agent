"""镔鑫球机图像下载模块单元/组件 smoke 测试（不需要访问真实 API）

运行：
  python tests/test_bxsteel_unit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bxsteel.config import BxSettings, create_settings, DEFAULT_DOWNLOAD_DIR
from agent.bxsteel.naming import (
    build_filename,
    extract_station_number,
    format_date_compact,
    sanitize_for_fs,
)
from agent.bxsteel.parser import parse_manual_result
from agent.bxsteel.detail_fetcher import _extract_origin_image_urls
from agent.bxsteel.list_fetcher import TruckMeta, enumerate_daily


# =====================================================================
# config
# =====================================================================
def test_create_settings():
    s = create_settings(username="testuser", password="testpass")
    assert s.username == "testuser"
    assert s.password == "testpass"
    assert s.base_url == "http://172.31.1.102:8081"
    assert s.login_url == "http://172.31.1.102:8081/fcs-web/#/login"
    assert s.download_dir.resolve() == DEFAULT_DOWNLOAD_DIR.resolve()
    assert s.cache_dir.exists()

    # 自定义下载目录 & base_url
    custom = create_settings(
        username="u", password="p",
        base_url="http://10.0.0.1:9090",
        download_dir="/tmp/test_bx_dl",
    )
    assert custom.base_url == "http://10.0.0.1:9090"
    assert custom.download_dir == Path("/tmp/test_bx_dl").resolve()
    print("create_settings OK")


# =====================================================================
# naming
# =====================================================================
def test_format_date_compact():
    assert format_date_compact("2026-04-17 10:30:00") == "20260417"
    assert format_date_compact("2026/04/17") == "20260417"
    assert format_date_compact("20260417") == "20260417"
    print("format_date_compact OK")


def test_format_date_compact_errors():
    try:
        format_date_compact("")
        assert False, "空字符串应抛异常"
    except ValueError:
        pass

    try:
        format_date_compact("no-date-here")
        assert False, "无日期应抛异常"
    except ValueError:
        pass
    print("format_date_compact_errors OK")


def test_extract_station_number():
    assert extract_station_number("1号工位") == "1"
    assert extract_station_number("2号工位") == "2"
    assert extract_station_number("station 3") == "3"
    print("extract_station_number OK")


def test_extract_station_number_errors():
    try:
        extract_station_number("")
        assert False, "空字符串应抛异常"
    except ValueError:
        pass

    try:
        extract_station_number("no number")
        assert False, "无数字应抛异常"
    except ValueError:
        pass
    print("extract_station_number_errors OK")


def test_build_filename():
    fname = build_filename(
        date_compact="20260417",
        materials=[("zhongfei1", 80), ("jingluliao3", 20)],
        station="2",
        daily_index=5,
        image_index=3,
    )
    assert fname == "20260417_zhongfei1_80_jingluliao3_20_2_5_3.jpg"


def test_build_filename_single_material():
    fname = build_filename(
        date_compact="20260418",
        materials=[("medium", 100)],
        station="1",
        daily_index=1,
        image_index=1,
    )
    assert fname == "20260418_medium_100_1_1_1.jpg"


def test_build_filename_errors():
    try:
        build_filename("20260417", [], "1", 1, 1)
        assert False, "空 materials 应抛异常"
    except ValueError:
        pass


def test_sanitize_for_fs():
    assert sanitize_for_fs("hello world") == "hello_world"
    assert sanitize_for_fs("a/b:c*d?e\"f<g>h|i") == "a_b_c_d_e_f_g_h_i"
    assert sanitize_for_fs("  spaces  ") == "spaces"
    print("sanitize_for_fs OK")


# =====================================================================
# parser
# =====================================================================
def test_parse_manual_result_basic():
    # 重废等级二 80% + 精炉料等级三 20%
    result = parse_manual_result("重废等级二80.00%,精炉料等级三20.00%")
    assert result == [("zhongfei2", 80), ("jingluliao3", 20)]

    # 中废 100%
    result2 = parse_manual_result("中废 100%")
    assert result2 == [("medium", 100)]

    # 重废等级一 60% + 重废等级二 40%（按占比排序）
    result3 = parse_manual_result("重废等级一 60%, 重废等级二 40%")
    assert result3 == [("zhongfei1", 60), ("zhongfei2", 40)]

    print("parse_manual_result_basic OK")


def test_parse_manual_result_ungraded():
    # 杂模
    result = parse_manual_result("杂模100%")
    assert result == [("zamo", 100)]
    print("parse_manual_result_ungraded OK")


def test_parse_manual_result_edges():
    # 空字符串
    assert parse_manual_result("") == []
    assert parse_manual_result("   ") == []

    # 精炉料不带等级：被跳过
    result = parse_manual_result("精炉料80%")
    assert result == []

    # 未知料型被跳过
    result2 = parse_manual_result("未知料型100%")
    assert result2 == []
    print("parse_manual_result_edges OK")


# =====================================================================
# detail_fetcher
# =====================================================================
def test_extract_origin_image_urls():
    data = {
        "oneCheckSummaryDTOList": [
            {
                "accTimestamp": "2026-04-17 10:00:01",
                "originImageUrl": "http://host/a/img1.jpg",
            },
            {
                "accTimestamp": "2026-04-17 10:00:02",
                "originImageUrl": "http://host/a/img2.jpg",
            },
            {
                "accTimestamp": "2026-04-17 10:00:03",
                "originImageUrl": "http://host/a/img1.jpg",  # 重复
            },
            {
                "accTimestamp": "2026-04-17 10:00:00",
                "originImageUrl": "http://host/a/img0.jpg",
            },
        ]
    }
    urls = _extract_origin_image_urls(data)
    # 按时间排序，去重
    assert urls == [
        "http://host/a/img0.jpg",
        "http://host/a/img1.jpg",
        "http://host/a/img2.jpg",
    ]
    print("extract_origin_image_urls OK")


def test_extract_origin_image_urls_empty():
    assert _extract_origin_image_urls({}) == []
    assert _extract_origin_image_urls({"oneCheckSummaryDTOList": []}) == []
    print("extract_origin_image_urls_empty OK")


# =====================================================================
# list_fetcher
# =====================================================================
def test_truck_meta_from_record():
    rec = {
        "flowCode": "abc-123",
        "carNumber": "鲁A-0001",
        "stationNumber": 2,
        "checkStartTime": "2026-04-17 10:30:00",
        "checkCompleteTime": "2026-04-17 10:35:00",
        "checkSituation": 1,
    }
    meta = TruckMeta.from_record(rec)
    assert meta.flow_code == "abc-123"
    assert meta.car_number == "鲁A-0001"
    assert meta.station_number == 2
    assert meta.check_start_time == "2026-04-17 10:30:00"
    assert meta.check_complete_time == "2026-04-17 10:35:00"
    assert meta.check_situation == 1
    print("truck_meta_from_record OK")


def test_truck_meta_missing_fields():
    rec = {}
    meta = TruckMeta.from_record(rec)
    assert meta.flow_code == ""
    assert meta.car_number == ""
    assert meta.station_number == 0
    assert meta.check_start_time == ""
    assert meta.check_complete_time is None
    assert meta.check_situation is None
    print("truck_meta_missing_fields OK")


def test_enumerate_daily():
    metas = [
        TruckMeta("f1", "鲁A-001", 1, "10:00", None, None),
        TruckMeta("f2", "鲁A-002", 2, "11:00", None, None),
    ]
    enumerated = enumerate_daily(metas)
    assert enumerated == [(1, metas[0]), (2, metas[1])]
    print("enumerate_daily OK")


def test_enumerate_daily_empty():
    assert enumerate_daily([]) == []
    print("enumerate_daily_empty OK")


if __name__ == "__main__":
    test_create_settings()
    test_format_date_compact()
    test_format_date_compact_errors()
    test_extract_station_number()
    test_extract_station_number_errors()
    test_build_filename()
    test_build_filename_single_material()
    test_build_filename_errors()
    test_sanitize_for_fs()
    test_parse_manual_result_basic()
    test_parse_manual_result_ungraded()
    test_parse_manual_result_edges()
    test_extract_origin_image_urls()
    test_extract_origin_image_urls_empty()
    test_truck_meta_from_record()
    test_truck_meta_missing_fields()
    test_enumerate_daily()
    test_enumerate_daily_empty()
    print("\nAll bxsteel unit smoke tests PASSED")
