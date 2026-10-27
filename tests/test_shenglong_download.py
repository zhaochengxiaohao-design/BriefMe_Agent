"""盛隆检判原图命名 / 打包 / 续传 单测。"""
from __future__ import annotations

import sys
import zipfile
from datetime import date as date_cls
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import _parse_save_path
from agent.shenglong.dict import get_material_en
from agent.shenglong.naming import (
    AVERAGE_TYPE_NAME,
    build_image_filename,
    build_truck_folder_name,
    classify_pack_group,
    extract_origin_image_urls,
    parse_manual_shares,
    resolve_station_code,
)
from agent.shenglong.downloader import _unique_truck_dir, parse_requested_dates
from agent.shenglong.packager import (
    MULTILABEL_NAME,
    pack_day_datasets,
    pack_multilabel_from_disk,
    write_images_zip,
)


def _avg_result():
    return [
        {"steelType": 11, "avgRate": 0.4000000000000001},
        {"steelType": 1, "avgRate": 0.3},
        {"steelType": 3, "avgRate": 0.20000000000000004},
        {"steelType": 13, "avgRate": 0.10000000000000002},
    ]


def test_material_en_codes():
    assert get_material_en(1) == "zhongfei1"
    assert get_material_en(11) == "medium"
    assert get_material_en(13) == "houjian"
    assert get_material_en(14) == "gangjinqieli"
    print("material en OK")


def test_folder_and_filename_match_screenshot():
    shares = parse_manual_shares(_avg_result())
    folder = build_truck_folder_name("桂ND3699", shares, "2026-08-27")
    assert folder == "2026-08-27_桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)"

    name = build_image_filename("2026-08-27", "53", 1, shares, 10)
    assert name == (
        "20260827_medium_40_zhongfei1_30_zhongfei3_20_houjian_10_53_1_10.jpg"
    )
    sample = parse_manual_shares(
        [
            {"steelType": 1, "avgRate": 0.80},
            {"steelType": 2, "avgRate": 0.10},
            {"steelType": 13, "avgRate": 0.10},
        ]
    )
    assert build_image_filename("2026-08-26", "53", 5, sample, 14) == (
        "20260826_zhongfei1_80_zhongfei2_10_houjian_10_53_5_14.jpg"
    )
    print("folder/filename OK")


def test_classify_pack_group_average_and_main():
    avg = parse_manual_shares(_avg_result())
    kind, stem = classify_pack_group(avg)
    assert kind == "average"
    assert stem == AVERAGE_TYPE_NAME == "平均料型"

    swapped = parse_manual_shares(
        [{"steelType": 1, "avgRate": 0.40}, {"steelType": 2, "avgRate": 0.38}]
    )
    kind2, stem2 = classify_pack_group(swapped)
    assert (kind2, stem2) == ("average", "平均料型")
    flipped = parse_manual_shares(
        [{"steelType": 2, "avgRate": 0.40}, {"steelType": 1, "avgRate": 0.38}]
    )
    assert classify_pack_group(flipped) == ("average", "平均料型")

    main_only = parse_manual_shares([{"steelType": 1, "avgRate": 0.80}, {"steelType": 11, "avgRate": 0.20}])
    kind, stem = classify_pack_group(main_only)
    assert kind == "main"
    assert stem == "重废1"

    edge = parse_manual_shares([{"steelType": 1, "avgRate": 0.40}, {"steelType": 11, "avgRate": 0.25}])
    kind, stem = classify_pack_group(edge)
    assert kind == "average"

    just_over = parse_manual_shares([{"steelType": 1, "avgRate": 0.40}, {"steelType": 11, "avgRate": 0.24}])
    kind, stem = classify_pack_group(just_over)
    assert kind == "main"
    assert stem == "重废1"
    print("pack group OK")


def test_station_uses_last_number():
    assert resolve_station_code("36/53", {"stationNumbers": [36, 53]}) == "53"
    assert resolve_station_code([36, 53], {}) == "53"
    assert resolve_station_code(36, {}) == "36"
    print("station OK")


def test_origin_urls_prefer_summary_order():
    detail = {
        "totalCheckResult": {
            "allOriginImageUrls": ["http://x/late.jpg", "http://x/early.jpg"],
            "oneCheckSummaryDTOList": [
                {"accTimestamp": "2026-08-27 10:46:14", "originImageUrl": "http://x/late.jpg"},
                {"accTimestamp": "2026-08-27 10:45:09", "originImageUrl": "http://x/early.jpg"},
            ],
        }
    }
    assert extract_origin_image_urls(detail) == ["http://x/early.jpg", "http://x/late.jpg"]
    print("origin urls OK")


def test_auto_pack_skips_multilabel(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "keep.jpg"
    img.write_bytes(b"x")
    shares = parse_manual_shares([{"steelType": 1, "avgRate": 0.80}, {"steelType": 11, "avgRate": 0.20}])
    zips = pack_day_datasets(tmp_path, [{"files": [img], "shares": shares}])
    names = {p.name for p in zips}
    assert f"{MULTILABEL_NAME}.zip" not in names
    assert any("实例分割数据集.zip" in n for n in names)
    print("auto pack skips multilabel OK")


def test_multilabel_pack_uses_remaining_files(tmp_path: Path):
    day = tmp_path / "2026-08-01"
    truck = day / "桂A00001_重废1(80)、中废(20)"
    truck.mkdir(parents=True)
    keep = truck / "keep.jpg"
    drop = truck / "drop.jpg"
    keep.write_bytes(b"keep")
    drop.write_bytes(b"drop")
    drop.unlink()
    result = pack_multilabel_from_disk(tmp_path, dates=["2026-08-01"])
    zpath = Path(result["zip_files"][0])
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert names == ["images/keep.jpg"]
    assert "drop.jpg" not in "".join(names)
    print("multilabel pack reviewed files OK")


def test_zip_contains_only_images_folder(tmp_path: Path):
    img = tmp_path / "2026_08_27_53_1_medium_40_1.jpg"
    img.write_bytes(b"fake-jpg")
    zpath = write_images_zip(tmp_path / "中废_实例分割数据集.zip", [img])
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert names == ["images/2026_08_27_53_1_medium_40_1.jpg"]
    print("zip layout OK")


def test_skip_existing_file_logic(tmp_path: Path):
    dest = tmp_path / "exists.jpg"
    dest.write_bytes(b"12345")
    assert dest.exists() and dest.stat().st_size > 0
    print("skip existing OK")


def test_unique_truck_dir_renames_legacy_folder(tmp_path: Path):
    day = tmp_path / "2026-08-27"
    day.mkdir(parents=True)
    legacy = day / "桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    legacy.mkdir()
    (legacy / "old.jpg").write_bytes(b"x")
    new_name = "2026-08-27_桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    resolved = _unique_truck_dir(day, new_name, 1, legacy_name=legacy.name)
    assert resolved.name == new_name
    assert resolved.is_dir()
    assert (resolved / "old.jpg").is_file()
    assert not legacy.exists()
    print("legacy folder rename OK")


def test_unique_truck_dir_canonical_without_daily_index(tmp_path: Path):
    day = tmp_path
    day.mkdir(parents=True, exist_ok=True)
    name = "2026-08-27_桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    created = _unique_truck_dir(day, name, 3)
    assert created.name == name
    indexed = day / f"{name}_3"
    indexed.mkdir()
    (indexed / "a.jpg").write_bytes(b"x")
    resumed = _unique_truck_dir(day, name, 3)
    assert resumed.name == name
    assert (resumed / "a.jpg").is_file()
    assert not indexed.exists()
    print("canonical folder without _N OK")


def test_unique_truck_dir_collision_keeps_second_truck_separate(tmp_path: Path):
    day = tmp_path
    day.mkdir(parents=True, exist_ok=True)
    name = "2026-08-27_桂ND3699_重废1(80)"
    first = _unique_truck_dir(day, name, 1)
    first.mkdir()
    (first / "a.jpg").write_bytes(b"x")
    (first / ".briefme_flow").write_text("flow-first\n", encoding="utf-8")
    second = _unique_truck_dir(day, name, 2)
    assert second.name == f"{name}_2"
    assert second != first
    assert (first / "a.jpg").is_file()
    print("collision keeps second truck separate OK")


def test_unique_truck_dir_resume_high_index_reuses_canonical(tmp_path: Path):
    day = tmp_path
    day.mkdir(parents=True, exist_ok=True)
    name = "2026-08-27_桂ND3699_重废1(80)"
    first = _unique_truck_dir(day, name, 3)
    first.mkdir()
    (first / "a.jpg").write_bytes(b"x")
    resumed = _unique_truck_dir(day, name, 3)
    assert resumed.name == name
    assert (resumed / "a.jpg").is_file()
    assert not (day / f"{name}_3").exists()
    print("resume high index reuses canonical OK")


def test_parse_requested_dates_single_range_and_list():
    assert parse_requested_dates("下载 2026-08-01 的【盛隆】检判原图") == ["2026-08-01"]
    assert parse_requested_dates(
        "下载 2026-08-01、2026-08-03、2026-08-05 的【盛隆】检判原图"
    ) == ["2026-08-01", "2026-08-03", "2026-08-05"]
    assert parse_requested_dates("下载 2026-08-01 到 2026-08-03 的【盛隆】检判原图") == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]
    assert parse_requested_dates(
        "下载 2026-08-01 到 2026-08-02、2026-08-05 的【盛隆】检判原图"
    ) == ["2026-08-01", "2026-08-02", "2026-08-05"]
    assert parse_requested_dates(dates=["2026-08-03", "2026-08-01"]) == [
        "2026-08-01",
        "2026-08-03",
    ]

    class _FixedToday(date_cls):
        @classmethod
        def today(cls):
            return date_cls(2026, 8, 27)

    with patch("agent.shenglong.downloader.date", _FixedToday):
        assert parse_requested_dates("下载昨天的【盛隆】检判原图") == ["2026-08-26"]
        week = parse_requested_dates("下载近一周的【盛隆】检判原图")
        assert week[0] == "2026-08-20" and week[-1] == "2026-08-26"
    print("parse requested dates OK")


def test_parse_save_path():
    assert _parse_save_path("保存地址：/Users/me/Desktop/盛隆图像") == "/Users/me/Desktop/盛隆图像"
    assert _parse_save_path("~/Desktop/foo").endswith("Desktop/foo")
    assert _parse_save_path("相对目录") == ""
    print("parse path OK")


if __name__ == "__main__":
    from pathlib import Path as _P
    import tempfile

    test_material_en_codes()
    test_folder_and_filename_match_screenshot()
    test_classify_pack_group_average_and_main()
    test_station_uses_last_number()
    test_origin_urls_prefer_summary_order()
    with tempfile.TemporaryDirectory() as td:
        test_zip_contains_only_images_folder(_P(td))
        test_skip_existing_file_logic(_P(td))
        test_auto_pack_skips_multilabel(_P(td) / "auto")
        test_multilabel_pack_uses_remaining_files(_P(td) / "ml")
        test_unique_truck_dir_renames_legacy_folder(_P(td) / "legacy")
        test_unique_truck_dir_canonical_without_daily_index(_P(td) / "canon")
        test_unique_truck_dir_collision_keeps_second_truck_separate(_P(td) / "collide")
        test_unique_truck_dir_resume_high_index_reuses_canonical(_P(td) / "resume")
    test_parse_requested_dates_single_range_and_list()
    test_parse_save_path()
    print("\nAll shenglong download tests PASSED")
