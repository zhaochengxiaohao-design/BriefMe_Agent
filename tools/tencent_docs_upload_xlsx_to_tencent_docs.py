import argparse
import base64
import hashlib
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from config.settings import settings

BASE_URL = "https://docs.qq.com/openapi/drive/v2"
DEFAULT_DOWNLOAD_DIR = PACKAGE_ROOT / "downloads" / "yongfeng"
DEFAULT_TARGET_FOLDER_ID = "JOVGVfapiCoO"

SUPPORTED_UPLOAD_TYPES = {"drive"}


def _headers(content_type: str = "application/json") -> dict[str, str]:
    return {
        "Client-Id": settings.tencent_docs.client_id,
        "Open-Id": settings.tencent_docs.open_id,
        "Accept": "application/json",
        "Content-Type": content_type,
    }


def _assert_config() -> None:
    missing = []
    if not settings.tencent_docs.access_token:
        missing.append("TENCENT_DOCS_ACCESS_TOKEN")
    if not settings.tencent_docs.client_id:
        missing.append("TENCENT_DOCS_CLIENT_ID")
    if not settings.tencent_docs.open_id:
        missing.append("TENCENT_DOCS_OPEN_ID")
    if missing:
        raise ValueError(f"缺少腾讯文档配置: {', '.join(missing)}")


def _request(client: httpx.Client, method: str, url: str, content_type: str = "application/json", **kwargs) -> dict[str, Any]:
    headers = _headers(content_type)
    headers["Access-Token"] = settings.tencent_docs.access_token
    resp = client.request(method, url, headers=headers, timeout=120, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP请求失败 (HTTP {resp.status_code}): {resp.text[:300]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"非JSON响应: {resp.text[:300]}")
    ret = data.get("ret", 0)
    if ret != 0:
        raise RuntimeError(f"API请求失败 (ret: {ret}): {data.get('msg', '未知错误')} | url={url}")
    return data


def _latest_xlsx(download_dir: Path) -> Path:
    candidates = [p for p in download_dir.rglob("*.xlsx") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"未在目录中找到xlsx文件: {download_dir}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pre_import(client: httpx.Client, file_path: Path, upload_type: str = "drive") -> dict[str, Any]:
    url = f"{BASE_URL}/files/upload"
    payload = {
        "fileMD5": _file_md5(file_path),
        "fileName": file_path.name,
        "fileSize": file_path.stat().st_size,
    }
    if upload_type:
        payload["uploadType"] = upload_type
    return _request(client, "POST", url, content_type="application/x-www-form-urlencoded", data=payload)


def upload_to_cos(client: httpx.Client, cos_put_url: str, custom_header: dict[str, str], file_path: Path) -> None:
    headers = dict(custom_header or {})
    if "Content-Type" not in headers:
        guess, _ = mimetypes.guess_type(file_path.name)
        headers["Content-Type"] = guess or "application/octet-stream"
    with file_path.open("rb") as f:
        resp = client.put(cos_put_url, content=f, headers=headers, timeout=300)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"上传到COS失败 (HTTP {resp.status_code}): {resp.text[:300]}")


def async_import(client: httpx.Client, file_path: Path, cos_file_key: str) -> dict[str, Any]:
    url = f"{BASE_URL}/files/async-import"
    payload = {
        "fileMD5": _file_md5(file_path),
        "fileName": file_path.name,
        "COSFileKey": cos_file_key,
    }
    return _request(client, "POST", url, content_type="application/x-www-form-urlencoded", data=payload)


def query_progress(client: httpx.Client, progress_query_id: str) -> dict[str, Any]:
    url = f"{BASE_URL}/files/import-progress"
    return _request(client, "GET", url, params={"progressQueryID": progress_query_id})


def move_file(client: httpx.Client, file_id: str, target_folder_id: str, parent_folder_id: str = "/") -> dict[str, Any]:
    url = f"{BASE_URL}/files/{file_id}/move"
    payload = {
        "targetFolderID": target_folder_id,
        "parentFolderID": parent_folder_id,
    }
    return _request(
        client,
        "PATCH",
        url,
        content_type="application/x-www-form-urlencoded",
        data=payload,
    )


def wait_for_import(client: httpx.Client, progress_query_id: str, timeout_seconds: int = 600, interval_seconds: int = 5) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = query_progress(client, progress_query_id)
        data = result.get("data", {})
        progress = int(data.get("progress", 0) or 0)
        print(json.dumps({"progress": progress, "title": data.get("title"), "type": data.get("type")}, ensure_ascii=False))
        if progress >= 100 and data.get("ID"):
            return data
        time.sleep(interval_seconds)
    raise TimeoutError(f"导入超时，progressQueryID={progress_query_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="腾讯文档上传冒烟测试")
    parser.add_argument("--xlsx", default=None, help="本地Excel文件路径；不传则自动选择 downloads/yongfeng 下最新的xlsx")
    args = parser.parse_args()

    _assert_config()

    xlsx_path = Path(args.xlsx).expanduser().resolve() if args.xlsx else _latest_xlsx(DEFAULT_DOWNLOAD_DIR)
    if not xlsx_path.exists():
        raise FileNotFoundError(xlsx_path)

    with httpx.Client(follow_redirects=True) as client:
        pre = pre_import(client, xlsx_path)
        data = pre.get("data", {})
        print(json.dumps({
            "status": "smoke_pre_import_ok",
            "file": str(xlsx_path),
            "fileMD5": _file_md5(xlsx_path),
            "cosFileKey": data.get("COSFileKey"),
            "hasCustomHeader": bool(data.get("CustomHeader")),
            "cosPutUrlPresent": bool(data.get("COSPutURL")),
            "message": "仅验证预导入接口和参数校验，不执行上传和导入",
        }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
