"""参数化配置：接受凭证参数，不依赖 .env 文件。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR: Path = PROJECT_ROOT / ".cache"
DEFAULT_DOWNLOAD_DIR: Path = PROJECT_ROOT / "downloads"
TOKEN_CACHE_FILE: Path = DEFAULT_CACHE_DIR / "bxsteel_token.json"


@dataclass
class BxSettings:
    base_url: str
    username: str
    password: str
    download_dir: Path = DEFAULT_DOWNLOAD_DIR
    cache_dir: Path = DEFAULT_CACHE_DIR

    @property
    def login_url(self) -> str:
        return f"{self.base_url}/fcs-web/#/login"


def create_settings(
    username: str,
    password: str,
    base_url: str = "http://172.31.1.102:8081",
    download_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> BxSettings:
    dl = Path(download_dir).resolve() if download_dir else DEFAULT_DOWNLOAD_DIR
    cc = Path(cache_dir).resolve() if cache_dir else DEFAULT_CACHE_DIR
    dl.mkdir(parents=True, exist_ok=True)
    cc.mkdir(parents=True, exist_ok=True)
    return BxSettings(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        download_dir=dl,
        cache_dir=cc,
    )
