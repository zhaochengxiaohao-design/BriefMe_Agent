"""对照 Readme / 使用手册的对抗检查（不连网）。

手册车次文件夹：YYYY-MM-DD_车牌_中废(40)、重废1(30)...
顿号枚举不补中间天；A 到 B 才连续；多标签不自动打；scp 失败不中断；
永锋不得写入盛隆 sl_feigang；拦截不得抢走打包带/烧结矿。
"""
from __future__ import annotations

import ast
import re
import sys
import zipfile
from datetime import date as date_cls
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class _FixedToday(date_cls):
    @classmethod
    def today(cls):
        return date_cls(2026, 8, 27)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import _parse_save_path
from agent.image_download_route import (
    is_ambiguous_multilabel_pack,
    is_dual_plant_image_or_pack_request,
    is_shenglong_image_download,
    is_shenglong_multilabel_pack,
    is_yongfeng_image_download,
    is_yongfeng_multilabel_pack,
)
from agent.shenglong.downloader import (
    _unique_truck_dir as sl_unique,
    parse_requested_dates as sl_dates,
)
from agent.shenglong.naming import build_truck_folder_name as sl_folder
from agent.shenglong.naming import parse_manual_shares as sl_shares
from agent.yongfeng.downloader import (
    _unique_truck_dir as yf_unique,
    last_complete_7_days,
    parse_requested_dates as yf_dates,
    resolve_output_dir,
)
from agent.yongfeng.scrap_naming import (
    build_image_filename,
    build_truck_folder_name as yf_folder,
    extract_origin_image_urls,
    parse_manual_shares as yf_shares,
)
from agent.yongfeng.scrap_packager import MULTILABEL_NAME, pack_day_datasets
from agent.yongfeng.scrap_remote_sync import sync_date_folder
from config.settings import settings

_HANDBOOK_FOLDER = re.compile(
    r"^\d{4}-\d{2}-\d{2}_[^_].+_.+\(\d+\)"
)
_TRAILING_DAILY_INDEX = re.compile(r"_\d+$")


def _yf_root() -> Path:
    return Path(__file__).resolve().parent.parent / "agent" / "yongfeng"


def test_handbook_folder_has_date_plate_materials_no_daily_index():
    shares = yf_shares(
        [
            {"steelType": 11, "avgRate": 0.4},
            {"steelType": 1, "avgRate": 0.3},
            {"steelType": 3, "avgRate": 0.2},
            {"steelType": 13, "avgRate": 0.1},
        ]
    )
    yf = yf_folder("鲁NG8388", shares, "2026-09-01")
    sl = sl_folder("桂ND3699", sl_shares(
        [
            {"steelType": 11, "avgRate": 0.4},
            {"steelType": 1, "avgRate": 0.3},
            {"steelType": 3, "avgRate": 0.2},
            {"steelType": 13, "avgRate": 0.1},
        ]
    ), "2026-08-27")
    assert yf == "2026-09-01_鲁NG8388_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    assert sl == "2026-08-27_桂ND3699_中废(40)、重废1(30)、重废3(20)、厚剪(10)"
    for name in (yf, sl):
        assert _HANDBOOK_FOLDER.search(name)
        assert not _TRAILING_DAILY_INDEX.search(name)


def test_new_truck_dir_matches_handbook_not_indexed(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    name = "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    assert yf_unique(tmp_path, name, 3).name == name
    assert sl_unique(tmp_path, name, 6).name == name


def test_same_plate_second_truck_stays_in_own_folder(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    name = "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    first = yf_unique(tmp_path, name, 1)
    first.mkdir()
    (first / "a.jpg").write_bytes(b"x")
    (first / ".briefme_flow").write_text("flow-first\n", encoding="utf-8")
    yf_second = yf_unique(tmp_path, name, 2)
    sl_second = sl_unique(tmp_path, name, 2)
    assert yf_second.name == f"{name}_2"
    assert sl_second.name == f"{name}_2"
    assert (first / "a.jpg").is_file()


def test_dunhao_does_not_fill_and_range_does():
    dunhao = "下载 2026-08-01、2026-08-03、2026-08-05 的【永锋】检判原图"
    assert yf_dates(dunhao) == ["2026-08-01", "2026-08-03", "2026-08-05"]
    assert sl_dates(dunhao.replace("永锋", "盛隆")) == [
        "2026-08-01",
        "2026-08-03",
        "2026-08-05",
    ]
    assert "2026-08-02" not in yf_dates(dunhao)
    assert yf_dates("下载 2026-08-31 到 2026-09-01 的【永锋】检判原图") == [
        "2026-08-31",
        "2026-09-01",
    ]
    mixed = "下载 2026-08-01 到 2026-08-02、2026-08-05 的【永锋】检判原图"
    assert yf_dates(mixed) == ["2026-08-01", "2026-08-02", "2026-08-05"]


def test_near_7_days_excludes_today_like_handbook():
    start, end = last_complete_7_days(date_cls(2026, 8, 27))
    assert (start, end) == ("2026-08-20", "2026-08-26")
    assert end != "2026-08-27"


def test_yesterday_and_near_week_expand_when_no_ymd():
    """手册：无 YYYY-MM-DD 时，昨天/近7天由拦截解析展开；近7天不含今天。"""
    with (
        patch("agent.yongfeng.downloader.date", _FixedToday),
        patch("agent.shenglong.downloader.date", _FixedToday),
    ):
        assert yf_dates("下载昨天的【永锋】检判原图") == ["2026-08-26"]
        assert sl_dates("下载昨日的【盛隆】检判原图") == ["2026-08-26"]
        week = yf_dates("下载近7天的【永锋】检判原图")
        assert week[0] == "2026-08-20"
        assert week[-1] == "2026-08-26"
        assert "2026-08-27" not in week
        assert sl_dates("下载近一周的【盛隆】检判原图") == week
        assert yf_dates("下载昨天 2026-09-01 的【永锋】检判原图") == ["2026-09-01"]


def test_relative_save_path_rejected_absolute_accepted():
    assert _parse_save_path("保存地址：/Users/me/Desktop/永锋图像") == (
        "/Users/me/Desktop/永锋图像"
    )
    assert _parse_save_path("相对目录") == ""
    assert _parse_save_path("保存路径：盛隆图像") == ""


def test_empty_output_dir_raises():
    try:
        resolve_output_dir("")
    except ValueError as exc:
        assert "保存路径" in str(exc)
    else:
        raise AssertionError("empty output_dir must raise")


def test_origin_skips_render_and_minio_is_not_the_download_api():
    detail = {
        "totalCheckResult": {
            "allOriginImageUrls": [
                "http://10.233.224.206:9000/waste-water-discharg/a_origin.jpg",
                "http://10.233.224.206:9000/waste-water-discharg/a_render_img.jpg",
            ],
            "oneCheckSummaryDTOList": [
                {
                    "accTimestamp": "2",
                    "originImageUrl": (
                        "http://vision.lg.china-yongfeng.com/srape-steel/oss/"
                        "waste-water-discharg/b_render_render_img.jpg"
                        "?origin=http://10.233.224.206:9000"
                    ),
                },
                {
                    "accTimestamp": "1",
                    "originImageUrl": (
                        "http://10.233.224.206:9000/waste-water-discharg/b_origin.jpg"
                    ),
                },
            ],
        }
    }
    urls = extract_origin_image_urls(detail)
    assert urls == ["http://10.233.224.206:9000/waste-water-discharg/b_origin.jpg"]
    assert all("_render_" not in u for u in urls)


def test_filename_keeps_station_truck_image_and_english_materials():
    shares = yf_shares([{"steelType": 1, "avgRate": 0.85}, {"steelType": 2, "avgRate": 0.15}])
    name = build_image_filename("2026-09-01", "1", 1, shares, 3)
    assert name == "20260901_zhongfei1_85_zhongfei2_15_1_1_3.jpg"


def test_auto_pack_never_writes_multilabel(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    shares = yf_shares([{"steelType": 1, "avgRate": 0.8}, {"steelType": 11, "avgRate": 0.2}])
    zips = pack_day_datasets(tmp_path, [{"files": [img], "shares": shares}])
    names = {p.name for p in zips}
    assert f"{MULTILABEL_NAME}.zip" not in names
    assert any("实例分割数据集.zip" in n for n in names)
    assert any("边缘分割数据集.zip" in n for n in names)


def test_empty_day_does_not_create_datasets_dir(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    assert pack_day_datasets(tmp_path, []) == []
    assert not (tmp_path / "datasets").exists()


def test_average_type_pack_when_main_second_diff_le_15(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "b.jpg"
    img.write_bytes(b"x")
    shares = yf_shares([{"steelType": 1, "avgRate": 0.40}, {"steelType": 2, "avgRate": 0.38}])
    names = {p.name for p in pack_day_datasets(tmp_path, [{"files": [img], "shares": shares}])}
    assert "平均料型_实例分割数据集.zip" in names
    assert "重废1_实例分割数据集.zip" not in names


def test_yongfeng_scp_never_targets_shenglong_sl_feigang_or_datasets(tmp_path: Path):
    yf = settings.yongfeng_scrap
    sl = settings.shenglong
    assert yf.remote_host != sl.remote_host or "yf_feigang" in yf.remote_image_root
    assert "sl_feigang" not in (yf.remote_image_root or "")
    assert sl.remote_image_root.endswith("sl_feigang/test_images_full_car")
    assert sl.remote_host == "10.180.34.16"
    assert sl.remote_user == "cisdi"

    day = tmp_path / "2026-09-01"
    truck = day / "2026-09-01_鲁NG8388_重废1(85)、重废2(15)"
    truck.mkdir(parents=True)
    (truck / "a.jpg").write_bytes(b"x")
    (day / "datasets").mkdir()
    (day / "datasets" / f"{MULTILABEL_NAME}.zip").write_bytes(b"z")

    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        proc = SimpleNamespace(returncode=0, stdout="", stderr="")
        return proc

    with patch("agent.yongfeng.scrap_remote_sync.subprocess.run", side_effect=fake_run):
        result = sync_date_folder(day)
    assert result.ok is True
    scp_calls = [c for c in calls if c and c[0] == "scp"]
    assert scp_calls
    joined = " ".join(scp_calls[0])
    assert str(day / "datasets") not in scp_calls[0]
    assert "sl_feigang" not in joined
    assert "yf_feigang" in joined
    assert str(truck) in scp_calls[0]


def test_yongfeng_scrap_modules_do_not_import_shenglong():
    for rel in (
        "scrap_dict.py",
        "scrap_naming.py",
        "scrap_client.py",
        "scrap_packager.py",
        "scrap_remote_sync.py",
        "downloader.py",
        "scrap_models.py",
    ):
        tree = ast.parse((_yf_root() / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "shenglong" not in alias.name, rel
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "shenglong" not in node.module, rel


def test_sintering_entry_is_not_scrap_downloader():
    main = (_yf_root() / "main.py").read_text(encoding="utf-8")
    assert "accuracy" in main.lower() or "compute_accuracy" in main
    assert "iter_download_images" not in main
    down = (_yf_root() / "downloader.py").read_text(encoding="utf-8")
    assert "python -m agent.yongfeng.downloader" in down
    assert "不要占用烧结矿入口" in down


def test_route_adversaries_handbook_examples():
    assert is_yongfeng_image_download("下载 2026-08-26 的【永锋】检判原图")
    assert is_shenglong_image_download("下载 2026-08-26 的【盛隆】检判原图")
    assert is_shenglong_image_download("3000网站图像下载")
    assert is_shenglong_image_download("MINIO图像下载")
    assert is_yongfeng_image_download("下载永锋 MINIO图像下载 2026-09-01")
    assert not is_shenglong_image_download("下载永锋 MINIO图像下载 2026-09-01")
    both_legacy = "下载【永锋】【盛隆】3000网站图像下载"
    assert is_dual_plant_image_or_pack_request(both_legacy)
    assert not is_yongfeng_image_download(both_legacy)
    assert not is_shenglong_image_download(both_legacy)
    assert not is_yongfeng_image_download("下载昨天打包带的异常图片")
    assert not is_yongfeng_image_download("下载永锋打包带异常图片")
    assert not is_yongfeng_image_download(
        "生成 2026-04-01 到 2026-04-07 的烧结矿颗粒度准确率报表"
    )
    assert not is_yongfeng_image_download("下载检判原图")
    assert not is_shenglong_image_download("下载检判原图")
    both = "下载 2026-09-01 的【永锋】和【盛隆】检判原图"
    assert not is_yongfeng_image_download(both)
    assert not is_shenglong_image_download(both)
    assert is_ambiguous_multilabel_pack("确认打包多标签")
    assert not is_shenglong_multilabel_pack("确认打包多标签")
    assert is_shenglong_multilabel_pack(
        "确认打包保存目录下已筛完的【盛隆】废钢多标签分类数据集"
    )
    assert is_yongfeng_multilabel_pack(
        "确认打包保存目录下已筛完的【永锋】废钢多标签分类数据集"
    )


def test_zip_layout_only_images_prefix(tmp_path: Path):
    from agent.yongfeng.scrap_packager import write_images_zip

    tmp_path.mkdir(parents=True, exist_ok=True)
    img = tmp_path / "20260901_zhongfei1_85_1_1_1.jpg"
    img.write_bytes(b"fake")
    zpath = write_images_zip(tmp_path / "重废1_实例分割数据集.zip", [img])
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert names == ["images/20260901_zhongfei1_85_1_1_1.jpg"]


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as _P

    test_handbook_folder_has_date_plate_materials_no_daily_index()
    test_dunhao_does_not_fill_and_range_does()
    test_near_7_days_excludes_today_like_handbook()
    test_yesterday_and_near_week_expand_when_no_ymd()
    test_relative_save_path_rejected_absolute_accepted()
    test_empty_output_dir_raises()
    test_origin_skips_render_and_minio_is_not_the_download_api()
    test_filename_keeps_station_truck_image_and_english_materials()
    test_yongfeng_scrap_modules_do_not_import_shenglong()
    test_sintering_entry_is_not_scrap_downloader()
    test_route_adversaries_handbook_examples()
    with tempfile.TemporaryDirectory() as td:
        root = _P(td)
        test_new_truck_dir_matches_handbook_not_indexed(root / "dirs")
        test_same_plate_second_truck_stays_in_own_folder(root / "collide")
        test_empty_day_does_not_create_datasets_dir(root / "empty")
        test_auto_pack_never_writes_multilabel(root / "ml")
        test_average_type_pack_when_main_second_diff_le_15(root / "avg")
        test_yongfeng_scp_never_targets_shenglong_sl_feigang_or_datasets(root / "scp")
        test_zip_layout_only_images_prefix(root / "zip")
    print("All handbook adversarial tests PASSED")
