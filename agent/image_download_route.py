"""盛隆 / 永锋检判原图的 Gradio 关键词拦截（不走 LLM）。

必须互斥：永锋只认带「永锋」的检判原图；盛隆只认带「盛隆」的打包。
「永锋 + MINIO/3000」走永锋 srape-steel。不要用打包带异常图抢检判原图。
不带厂名的「确认打包多标签」要反问，不要默认盛隆。
"""
from __future__ import annotations


def _has_yongfeng(message: str) -> bool:
    return "永锋" in message or "【永锋】" in message


def _has_shenglong(message: str) -> bool:
    return "盛隆" in message or "【盛隆】" in message


def mentions_both_plants(message: str) -> bool:
    return _has_yongfeng(message) and _has_shenglong(message)


def is_yongfeng_multilabel_pack(message: str) -> bool:
    if mentions_both_plants(message):
        return False
    if "多标签" in message and ("打包" in message or "确认打包" in message):
        return _has_yongfeng(message)
    return False


def is_shenglong_multilabel_pack(message: str) -> bool:
    if is_yongfeng_multilabel_pack(message) or mentions_both_plants(message):
        return False
    if "多标签" in message and ("打包" in message or "确认打包" in message):
        return _has_shenglong(message)
    return False


def is_ambiguous_multilabel_pack(message: str) -> bool:
    if "多标签" not in message:
        return False
    if "打包" not in message and "确认打包" not in message:
        return False
    return not _has_yongfeng(message) and not _has_shenglong(message)


def _legacy_image_download_shortcut(message: str) -> bool:
    return "MINIO图像下载" in message or "3000网站图像下载" in message


def is_dual_plant_image_or_pack_request(message: str) -> bool:
    """两厂同句的原图/快捷语/多标签，拦截层直接拒绝，不交给 LLM。"""
    if not mentions_both_plants(message):
        return False
    return (
        "检判原图" in message
        or "智能判级照片" in message
        or "多标签" in message
        or _legacy_image_download_shortcut(message)
        or "图像下载" in message
        or "图片下载" in message
    )


def is_yongfeng_image_download(message: str) -> bool:
    if is_yongfeng_multilabel_pack(message) or mentions_both_plants(message):
        return False
    if "烧结" in message or "颗粒度" in message or "打包带" in message:
        return False
    if not _has_yongfeng(message):
        return False
    if "检判原图" in message or "智能判级照片" in message:
        return True
    # 习惯用语：永锋 + MINIO/3000 仍走永锋 srape-steel，绝不进盛隆 3000。
    if _legacy_image_download_shortcut(message):
        return True
    if "图像下载" in message or "图片下载" in message:
        return True
    return False


def is_shenglong_image_download(message: str) -> bool:
    if (
        is_shenglong_multilabel_pack(message)
        or is_yongfeng_image_download(message)
        or mentions_both_plants(message)
    ):
        return False
    # 只带永锋时，MINIO/3000 快捷词不得落到盛隆。
    if _has_yongfeng(message) and not _has_shenglong(message):
        return False
    if _legacy_image_download_shortcut(message):
        return True
    if "检判原图" in message and _has_shenglong(message):
        return True
    if _has_shenglong(message) and (
        "图像下载" in message or "图片下载" in message or "智能判级照片" in message
    ):
        return True
    return False
