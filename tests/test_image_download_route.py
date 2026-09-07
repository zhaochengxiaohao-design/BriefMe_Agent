"""永锋 / 盛隆检判原图 Gradio 拦截的对抗用例（不连网、不启 Gradio）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.image_download_route import (
    is_ambiguous_multilabel_pack,
    is_dual_plant_image_or_pack_request,
    is_shenglong_image_download,
    is_shenglong_multilabel_pack,
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


if __name__ == "__main__":
    test_yongfeng_shortcut_hits_and_does_not_steal_packing_tape()
    test_shenglong_keeps_old_intercept()
    test_multilabel_is_mutually_exclusive()
    test_both_plants_are_refused_by_intercept()
    print("All image download route tests PASSED")
