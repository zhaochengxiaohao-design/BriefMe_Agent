"""参数化配置：接受凭证参数，不依赖 .env 文件。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR: Path = PROJECT_ROOT / ".cache"
DEFAULT_DOWNLOAD_DIR: Path = PROJECT_ROOT / "downloads" / "yongyou"
STORAGE_STATE_FILE: Path = DEFAULT_CACHE_DIR / "yongyou_storage.json"


@dataclass
class YYSettings:
    base_url: str
    record_url: str
    username: str
    password: str
    download_dir: Path
    cache_dir: Path


def create_settings(
    username: str,
    password: str,
    base_url: str = "http://172.26.46.12:8890",
    download_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> YYSettings:
    base_url = base_url.rstrip("/")
    record_url = f"{base_url}/imp-ib-iv-igs-fe/ibd/igs/scheme/#/record"

    dl = Path(download_dir).resolve() if download_dir else DEFAULT_DOWNLOAD_DIR
    cc = Path(cache_dir).resolve() if cache_dir else DEFAULT_CACHE_DIR
    dl.mkdir(parents=True, exist_ok=True)
    cc.mkdir(parents=True, exist_ok=True)

    return YYSettings(
        base_url=base_url,
        record_url=record_url,
        username=username,
        password=password,
        download_dir=dl,
        cache_dir=cc,
    )
