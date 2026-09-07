"""永锋检判原图命名 / 打包 / 续传 / 日期解析 单测（不连网）。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import _parse_save_path
from agent.yongfeng.scrap_dict import get_material_en
from agent.yongfeng.scrap_naming import (
    AVERAGE_TYPE_NAME,
    build_image_filename,
    build_truck_folder_name,
    classify_pack_group,
    extract_origin_image_urls,
    parse_manual_shares,
    resolve_station_code,
)
from agent.yongfeng.downloader import (
    _existing_image,
    _find_dir_by_flow,
    _folder_shares,
    _looks_like_image,
    _try_rename_dir,
    _unique_truck_dir,
    _write_flow_mark,
    parse_requested_dates,
)
from agent.yongfeng.scrap_client import YongfengScrapClient, _token_from_login
from agent.yongfeng.scrap_packager import (
    MULTILABEL_NAME,
    pack_day_datasets,
    pack_multilabel_from_disk,
    write_images_zip,
)
from agent.yongfeng.scrap_remote_sync import format_sync_report, sync_date_folder
from config.settings import settings


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
    print("material en OK")


def test_folder_and_filename_match_convention():
    shares = parse_manual_shares(_avg_result())
    folder = build_truck_folder_name("鲁NG8388", shares, "2026-09-01")
    assert folder == "2026-09-01_鲁NG8388_中废(40)、重废1(30)、重废3(20)、厚剪(10)"

    name = build_image_filename("2026-09-01", "1", 1, shares, 10)
    assert name == (
        "20260901_medium_40_zhongfei1_30_zhongfei3_20_houjian_10_1_1_10.jpg"
    )
    empty = parse_manual_shares([])
    assert build_truck_folder_name("鲁NG8388", empty, "2026-09-01") == (
        "2026-09-01_鲁NG8388_无人工"
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
    assert classify_pack_group(swapped) == ("average", "平均料型")

    main_only = parse_manual_shares(
        [{"steelType": 1, "avgRate": 0.80}, {"steelType": 11, "avgRate": 0.20}]
    )
    kind, stem = classify_pack_group(main_only)
    assert kind == "main"
    assert stem == "重废1"

    ai_percent = parse_manual_shares(
        [{"steelType": 1, "steelRate": 84.8}, {"steelType": 2, "steelRate": 15.2}]
    )
    assert ai_percent[0].pct_int == 85
    print("pack group OK")


def test_station_uses_last_number():
    assert resolve_station_code("1", {"stationNumbers": [1]}) == "1"
    assert resolve_station_code([1, 2], {}) == "2"
    assert resolve_station_code(1, {}) == "1"
    print("station OK")


def test_origin_urls_prefer_summary_skip_render():
    detail = {
        "totalCheckResult": {
            "allOriginImageUrls": ["http://x/late.jpg", "http://x/early_render_preview.jpg"],
            "oneCheckSummaryDTOList": [
                {
                    "accTimestamp": "2026-09-01 10:46:14",
                    "originImageUrl": "http://x/late.jpg",
                },
                {
                    "accTimestamp": "2026-09-01 10:45:09",
                    "originImageUrl": "http://x/early.jpg",
                },
                {
                    "accTimestamp": "2026-09-01 10:44:00",
                    "originImageUrl": "http://x/skip_render_thumb.jpg",
                },
            ],
        }
    }
    assert extract_origin_image_urls(detail) == ["http://x/early.jpg", "http://x/late.jpg"]
    print("origin urls OK")


def test_auto_pack_skips_multilabel(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "keep.jpg"
    img.write_bytes(b"x")
    shares = parse_manual_shares(
        [{"steelType": 1, "avgRate": 0.80}, {"steelType": 11, "avgRate": 0.20}]
    )
    zips = pack_day_datasets(tmp_path, [{"files": [img], "shares": shares}])
    names = {p.name for p in zips}
    assert f"{MULTILABEL_NAME}.zip" not in names
    assert any("实例分割数据集.zip" in n for n in names)
    print("auto pack skips multilabel OK")


def test_multilabel_pack_uses_remaining_files(tmp_path: Path):
    day = tmp_path / "2026-09-01"
    truck = day / "鲁NG8388_重废1(85)、重废2(15)"
    truck.mkdir(parents=True)
    keep = truck / "keep.jpg"
    drop = truck / "drop.jpg"
    keep.write_bytes(b"keep")
    drop.write_bytes(b"drop")
    drop.unlink()
    result = pack_multilabel_from_disk(tmp_path, dates=["2026-09-01"])
    zpath = Path(result["zip_files"][0])
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert names == ["images/keep.jpg"]
    print("multilabel pack reviewed files OK")


def test_zip_contains_only_images_folder(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "20260901_zhongfei1_85_1_1_1.jpg"
    img.write_bytes(b"fake-jpg")
    zpath = write_images_zip(tmp_path / "重废1_实例分割数据集.zip", [img])
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert names == ["images/20260901_zhongfei1_85_1_1_1.jpg"]
    print("zip layout OK")


def test_unique_truck_dir_renames_legacy_folder(tmp_path: Path):
    day = tmp_path / "2026-09-01"
    day.mkdir(parents=True)
    legacy = day / "鲁NG8388_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    legacy.mkdir()
    (legacy / "old.jpg").write_bytes(b"x")
    new_name = "2026-09-01_鲁NG8388_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    resolved = _unique_truck_dir(day, new_name, 1, legacy_name=legacy.name)
    assert resolved.name == new_name
    assert (resolved / "old.jpg").is_file()
    assert not legacy.exists()
    print("legacy folder rename OK")


def test_folder_shares_fallback_to_ai_like_shenglong():
    detail = {
        "manualCheckResultVO": {},
        "totalCheckResult": {
            "steelTypeRateList": [
                {"steelType": 1, "steelRate": 0.848},
                {"steelType": 2, "steelRate": 0.1518},
            ]
        },
    }
    shares = _folder_shares(detail)
    folder = build_truck_folder_name("鲁NG8388", shares, "2026-09-01")
    assert folder == "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    print("AI folder shares OK")


def test_unique_truck_dir_prefers_plain_and_resumes_indexed(tmp_path: Path):
    day = tmp_path / "2026-09-01"
    day.mkdir(parents=True)
    name = "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    first = _unique_truck_dir(day, name, 1)
    assert first.name == name
    first.mkdir()
    (first / "a.jpg").write_bytes(b"x")
    again = _unique_truck_dir(day, name, 1)
    assert again == first

    other = tmp_path / "2026-08-31"
    other.mkdir(parents=True)
    indexed = other / f"{name}_1"
    indexed.mkdir()
    (indexed / "b.jpg").write_bytes(b"y")
    resumed = _unique_truck_dir(other, name, 1)
    assert resumed.name == name
    assert (resumed / "b.jpg").is_file()
    assert not indexed.exists()

    collided = _unique_truck_dir(day, name, 2)
    assert collided.name == f"{name}_2"
    assert collided != first

    unique_second = _unique_truck_dir(day, "2026-09-01_鲁B00000_重废1(80)", 2)
    assert unique_second.name == "2026-09-01_鲁B00000_重废1(80)"
    print("plain folder / indexed resume / collision OK")


def test_flow_mark_resume_renames_indexed_to_canonical(tmp_path: Path):
    day = tmp_path / "2026-09-01"
    day.mkdir(parents=True)
    name = "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    indexed = day / f"{name}_1"
    indexed.mkdir()
    (indexed / "a.jpg").write_bytes(b"x")
    _write_flow_mark(indexed, "flow-aaa")
    found = _find_dir_by_flow(day, "flow-aaa")
    renamed = _try_rename_dir(found, day / name)
    assert renamed.name == name
    assert (renamed / "a.jpg").is_file()
    assert not indexed.exists()
    print("flow mark canonical rename OK")


def test_existing_image_matches_shifted_daily_index(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    truck = tmp_path / "truck"
    truck.mkdir(exist_ok=True)
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    old = truck / "20260831_unknown_0_1_2_3.jpg"
    old.write_bytes(jpeg)
    dest = truck / "20260831_unknown_0_1_9_3.jpg"
    found = _existing_image(truck, dest, "1", 3)
    assert found == old
    html = truck / "20260831_unknown_0_1_9_4.jpg"
    html.write_bytes(b"<html>nope</html>")
    assert _existing_image(truck, html, "1", 4) is None
    assert not html.exists()
    print("existing image skip OK")


def test_parse_requested_dates_single_range_and_list():
    assert parse_requested_dates("下载 2026-09-01 的【永锋】检判原图") == ["2026-09-01"]
    assert parse_requested_dates(
        "下载 2026-08-31、2026-09-01 的【永锋】检判原图"
    ) == ["2026-08-31", "2026-09-01"]
    assert parse_requested_dates("下载 2026-08-31 到 2026-09-02 的【永锋】检判原图") == [
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
    ]
    assert parse_requested_dates(dates=["2026-09-01", "2026-08-31"]) == [
        "2026-08-31",
        "2026-09-01",
    ]
    from datetime import date as date_cls

    class _FixedToday(date_cls):
        @classmethod
        def today(cls):
            return date_cls(2026, 8, 27)

    with patch("agent.yongfeng.downloader.date", _FixedToday):
        assert parse_requested_dates("下载昨天的【永锋】检判原图") == ["2026-08-26"]
        week = parse_requested_dates("下载近7天的【永锋】检判原图")
        assert week[0] == "2026-08-20" and week[-1] == "2026-08-26"
    print("parse requested dates OK")


def test_parse_save_path():
    assert _parse_save_path("保存地址：/Users/me/Desktop/永锋图像") == "/Users/me/Desktop/永锋图像"
    print("parse path OK")


def test_login_token_falls_back_to_satoken_cookie():
    body = {"meta": {"success": True}, "data": {}}
    assert _token_from_login(body, "cookie-satoken") == "cookie-satoken"
    json_body = {"data": {"tokenInfo": {"tokenValue": "json-token"}}}
    assert _token_from_login(json_body, "cookie-satoken") == "json-token"
    assert _token_from_login({"data": {"tokenValue": "flat"}}, "") == "flat"

    client = YongfengScrapClient()

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"meta": {"success": True}, "data": {}}

    client.session.post = lambda *args, **kwargs: _Resp()
    client.session.cookies.set("satoken", "from-cookie")
    client.login()
    assert client.token == "from-cookie"
    print("login cookie fallback OK")


def test_yongfeng_scp_configured_from_site_isolated_from_shenglong(tmp_path: Path):
    yf = settings.yongfeng_scrap
    sl = settings.shenglong
    assert yf.remote_host == "10.233.224.206"
    assert yf.remote_user == "cisdi"
    assert "yf_feigang" in yf.remote_image_root
    assert "sl_feigang" not in yf.remote_image_root
    assert sl.remote_host == "10.180.34.16"
    assert sl.remote_image_root != yf.remote_image_root

    from types import SimpleNamespace

    day = tmp_path / "2026-09-01"
    truck = day / "鲁NG8388_无人工"
    truck.mkdir(parents=True)
    (truck / "a.jpg").write_bytes(b"x")

    empty = SimpleNamespace(
        remote_host="",
        remote_port=22,
        remote_user="",
        remote_image_root="",
        remote_scp_timeout_sec=30,
    )
    with patch("agent.yongfeng.scrap_remote_sync._cfg", return_value=empty):
        with patch("agent.yongfeng.scrap_remote_sync.subprocess.run") as mocked:
            result = sync_date_folder(day)
    mocked.assert_not_called()
    assert result.ok is True
    assert result.skipped is True
    assert "未配置" in result.error

    report = format_sync_report([{
        "date": "2026-09-01",
        "scp_ok": True,
        "scp_skipped": True,
        "scp_error": "未配置远程主机",
    }])
    assert "未配置" in report
    print("scp site config / skip when unconfigured OK")


def test_yongfeng_scp_failure_does_not_raise(tmp_path: Path):
    from types import SimpleNamespace

    day = tmp_path / "2026-09-01"
    truck = day / "鲁NG8388_重废1(80)"
    truck.mkdir(parents=True)
    (truck / "a.jpg").write_bytes(b"x")

    fake_cfg = SimpleNamespace(
        remote_host="10.0.0.1",
        remote_port=22,
        remote_user="test",
        remote_image_root="/tmp/yf",
        remote_scp_timeout_sec=30,
    )
    fail = SimpleNamespace(returncode=1, stdout="", stderr="Permission denied")

    with patch("agent.yongfeng.scrap_remote_sync._cfg", return_value=fake_cfg), patch(
        "agent.yongfeng.scrap_remote_sync.subprocess.run", return_value=fail
    ):
        result = sync_date_folder(day)
    assert result.ok is False
    assert result.skipped is False
    assert "Permission denied" in result.error
    print("scp failure does not raise OK")


def test_looks_like_image_rejects_html_poison():
    assert _looks_like_image(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert not _looks_like_image(b"<html>not a jpeg</html>")
    assert not _looks_like_image(b"")
    print("image magic OK")


def test_query_list_paginates_dedupes_and_skips_pageindex():
    pages = {
        1: {
            "data": {
                "records": [
                    {"flowCode": "a", "carNumber": "鲁A", "stationNumber": [1], "createTime": "t1"},
                    {"flowCode": "a", "carNumber": "鲁A", "stationNumber": [1], "createTime": "t1"},
                ],
                "pages": 2,
                "total": 2,
            }
        },
        2: {
            "data": {
                "records": [
                    {"flowCode": "b", "carNumber": "鲁B", "stationNumber": 1, "createTime": "t2"},
                ],
                "pages": 2,
                "total": 2,
            }
        },
    }

    def fake_request(self, method, endpoint, **kwargs):
        params = kwargs["params"]
        assert "pageIndex" not in params
        assert params["size"] == 200
        return pages[params["current"]]

    client = YongfengScrapClient.__new__(YongfengScrapClient)
    client.cfg = settings.yongfeng_scrap
    with patch.object(YongfengScrapClient, "_request_json", fake_request):
        recs = YongfengScrapClient.query_list_by_date(client, "2026-09-01")
    assert [r.flow_code for r in recs] == ["a", "b"]
    assert recs[0].station_number == [1]
    print("list pagination OK")


if __name__ == "__main__":
    from pathlib import Path as _P
    import tempfile

    test_material_en_codes()
    test_folder_and_filename_match_convention()
    test_folder_shares_fallback_to_ai_like_shenglong()
    test_classify_pack_group_average_and_main()
    test_station_uses_last_number()
    test_origin_urls_prefer_summary_skip_render()
    with tempfile.TemporaryDirectory() as td:
        root = _P(td)
        test_zip_contains_only_images_folder(root / "zip")
        test_auto_pack_skips_multilabel(root / "auto")
        test_multilabel_pack_uses_remaining_files(root / "ml")
        test_unique_truck_dir_renames_legacy_folder(root / "legacy")
        test_unique_truck_dir_prefers_plain_and_resumes_indexed(root / "plain")
        test_flow_mark_resume_renames_indexed_to_canonical(root / "flow")
        test_existing_image_matches_shifted_daily_index(root / "imgskip")
        test_yongfeng_scp_configured_from_site_isolated_from_shenglong(root / "scp")
        test_yongfeng_scp_failure_does_not_raise(root / "scp-fail")
    test_looks_like_image_rejects_html_poison()
    test_query_list_paginates_dedupes_and_skips_pageindex()
    test_parse_requested_dates_single_range_and_list()
    test_parse_save_path()
    test_login_token_falls_back_to_satoken_cookie()
    print("\nAll yongfeng download tests PASSED")
