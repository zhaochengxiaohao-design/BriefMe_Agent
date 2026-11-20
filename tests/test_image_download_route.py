"""永锋 / 盛隆检判原图路由检查（不连网、不启 Gradio）。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.image_download_route import (
    is_ambiguous_multilabel_pack,
    is_dual_plant_image_or_pack_request,
    is_shenglong_image_download,
    is_shenglong_multilabel_pack,
    is_yongfeng_dataset_upload,
    is_yongfeng_image_download,
    is_yongfeng_multilabel_pack,
    mentions_both_plants,
)


def test_yongfeng_shortcut_hits_and_does_not_steal_packing_tape():
    assert is_yongfeng_image_download("下载 2026-09-01 的【永锋】检判原图")
    assert is_yongfeng_image_download("下载昨天的永锋智能判级照片")
    assert not is_yongfeng_image_download("下载昨天打包带的异常图片")
    assert not is_yongfeng_image_download("下载永锋打包带异常图片")
    assert not is_yongfeng_image_download("生成 2026-04-01 到 2026-04-07 的烧结矿颗粒度准确率报表")
    assert not is_yongfeng_image_download("下载 2026-09-01 的【盛隆】检判原图")
    assert not is_yongfeng_image_download("下载检判原图")
    assert is_yongfeng_image_download("下载永锋 MINIO图像下载 2026-09-01")
    assert is_yongfeng_image_download("下载永锋 3000网站图像下载 2026-09-01")
    assert not is_shenglong_image_download("下载永锋 MINIO图像下载 2026-09-01")
    assert not is_shenglong_image_download("下载永锋 3000网站图像下载 2026-09-01")


def test_shenglong_keeps_old_intercept():
    assert is_shenglong_image_download("下载 2026-08-01 的【盛隆】检判原图")
    assert is_shenglong_image_download("3000网站图像下载")
    assert is_shenglong_image_download("MINIO图像下载")
    assert is_shenglong_image_download("3000网站图像下载 2026-08-01")
    assert not is_shenglong_image_download("下载 2026-09-01 的【永锋】检判原图")
    assert not is_shenglong_image_download("下载检判原图")


def test_yongfeng_upload_recognizes_downloaded_image_packages():
    for message in (
        "上传 2026-11-17 的永锋检判原图数据集",
        "上传 2026-11-17 已下载的永锋数据集",
        "把下载好的永锋智能判级照片压缩包导入金睛",
    ):
        assert is_yongfeng_dataset_upload(message)
        assert not is_yongfeng_image_download(message)


def test_yongfeng_combined_download_and_upload_starts_with_download():
    for message in (
        "下载并上传 2026-11-17 的永锋检判原图数据集",
        "下载完永锋检判原图再上传数据集",
    ):
        assert not is_yongfeng_dataset_upload(message)
        assert is_yongfeng_image_download(message)


def test_multilabel_is_mutually_exclusive():
    yf = "确认打包保存目录下已筛完的【永锋】废钢多标签分类数据集"
    sl = "确认打包保存目录下已筛完的【盛隆】废钢多标签分类数据集"
    assert is_yongfeng_multilabel_pack(yf)
    assert not is_shenglong_multilabel_pack(yf)
    assert is_shenglong_multilabel_pack(sl)
    assert not is_yongfeng_multilabel_pack(sl)
    assert not is_shenglong_multilabel_pack("确认打包多标签")
    assert not is_yongfeng_multilabel_pack("确认打包多标签")
    assert is_ambiguous_multilabel_pack("确认打包多标签")
    assert not is_ambiguous_multilabel_pack(yf)
    assert not is_ambiguous_multilabel_pack(sl)
    assert not is_yongfeng_image_download(yf)
    assert not is_shenglong_image_download(sl)


def test_both_plants_are_refused_by_intercept():
    msg = "下载 2026-09-01 的【永锋】和【盛隆】检判原图"
    assert mentions_both_plants(msg)
    assert not is_yongfeng_image_download(msg)
    assert not is_shenglong_image_download(msg)
    pack = "确认打包【永锋】【盛隆】废钢多标签分类数据集"
    assert not is_yongfeng_multilabel_pack(pack)
    assert not is_shenglong_multilabel_pack(pack)
    shortcut = "下载【永锋】【盛隆】3000网站图像下载"
    assert is_dual_plant_image_or_pack_request(shortcut)
    assert not is_yongfeng_image_download(shortcut)
    assert not is_shenglong_image_download(shortcut)
    minio = "下载【永锋】【盛隆】MINIO图像下载"
    assert is_dual_plant_image_or_pack_request(minio)
    assert not is_yongfeng_image_download(minio)
    assert not is_shenglong_image_download(minio)


def test_yongfeng_download_and_upload_dispatch_when_packing_vpn_is_unavailable():
    from agent.core import SteelCoilAgent

    def completion(message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    cases = [
        ("download_yongfeng_images", "_tool_download_yongfeng_images"),
        ("upload_yongfeng_datasets", "_tool_upload_yongfeng_datasets"),
        ("get_daily_stats", "_tool_get_daily_stats"),
    ]
    for tool, handler in cases:
        agent = SteelCoilAgent()
        agent.vpn = Mock()
        agent.vpn.check_connectivity.return_value = False
        agent.vpn.get_connection_prompt.return_value = "请连接打包带 VPN"
        agent.client = Mock()
        call = SimpleNamespace(
            id="tool-1",
            function=SimpleNamespace(
                name=tool,
                arguments='{"output_dir": "/images", "dates": ["2026-11-16"]}',
            ),
        )
        agent.client.chat.completions.create.side_effect = [
            completion(SimpleNamespace(content="", tool_calls=[call])),
            completion(SimpleNamespace(content="已处理")),
        ]
        run_tool = Mock(return_value={"summary_text": "已处理"})
        setattr(agent, handler, run_tool)
        reply, session = agent.chat("执行当前业务", {"messages": []})

        if tool == "get_daily_stats":
            agent.vpn.check_connectivity.assert_called_once()
            run_tool.assert_not_called()
            assert session["vpn_state"] == "waiting_code"
        else:
            agent.vpn.check_connectivity.assert_not_called()
            run_tool.assert_called_once()
            assert reply == "已处理"


if __name__ == "__main__":
    test_yongfeng_shortcut_hits_and_does_not_steal_packing_tape()
    test_shenglong_keeps_old_intercept()
    test_yongfeng_upload_recognizes_downloaded_image_packages()
    test_yongfeng_combined_download_and_upload_starts_with_download()
    test_multilabel_is_mutually_exclusive()
    test_both_plants_are_refused_by_intercept()
    test_yongfeng_download_and_upload_dispatch_when_packing_vpn_is_unavailable()
    print("All image download route tests PASSED")
