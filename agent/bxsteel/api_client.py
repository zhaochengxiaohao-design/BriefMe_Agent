"""镔鑫系统 HTTP API 客户端。

登录：POST /fcs/auth/login  (form-encoded: employeeId + password)
    响应 data.tokenInfo.tokenValue 用作后续请求的 `token` 请求头。
列表：GET /fcs/intelligence/intelliTaskInfo/page
    参数 pageIndex / pageSize / startTime / endTime (YYYY-MM-DD HH:mm:ss)
详情：GET /fcs/intelligence/intelliTaskInfo/getCheckDetail?flowCode=<uuid>
图片：GET http://172.31.1.102:19000/scrape-steel/.../*.jpg （无需鉴权）
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """API 返回 meta.success=False 时抛出。"""


@dataclass
class ApiClient:
    base_url: str
    token: Optional[str] = None
    timeout_s: float = 20.0
    session: requests.Session = field(default_factory=requests.Session)
    retries: int = 3
    retry_sleep_s: float = 1.0

    def login(self, username: str, password: str) -> str:
        url = f"{self.base_url}/fcs/auth/login"
        resp = self.session.post(
            url,
            data={"employeeId": username, "password": password},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("meta", {}).get("success"):
            raise ApiError(f"登录失败：{payload.get('meta')}")
        token = (
            payload.get("data", {}).get("tokenInfo", {}).get("tokenValue")
        )
        if not token:
            raise ApiError(f"登录响应缺少 tokenValue：{payload}")
        self.token = token
        logger.info(
            "登录成功：user=%s name=%s",
            username,
            payload.get("data", {}).get("userFullName"),
        )
        return token

    def load_token(self, token_file: Path) -> bool:
        try:
            obj = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        tok = obj.get("token")
        if not tok:
            return False
        self.token = tok
        return True

    def save_token(self, token_file: Path) -> None:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(
            json.dumps({"token": self.token, "saved_at": time.time()}),
            encoding="utf-8",
        )

    def ensure_login(
        self,
        username: str,
        password: str,
        token_file: Optional[Path] = None,
    ) -> str:
        if token_file is not None and self.load_token(token_file):
            try:
                self._request(
                    "GET",
                    "/fcs/intelligence/intelliTaskInfo/page",
                    params={"pageIndex": 1, "pageSize": 1},
                )
                logger.info("复用缓存 token")
                return self.token or ""
            except (ApiError, requests.HTTPError) as exc:
                logger.info("缓存 token 无效（%s），重新登录", exc)
                self.token = None

        self.login(username, password)
        if token_file is not None:
            self.save_token(token_file)
        return self.token or ""

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {"accept": "application/json"}
        if self.token:
            headers["token"] = self.token

        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=self.timeout_s,
                )
                resp.raise_for_status()
                payload = resp.json()
                meta = payload.get("meta", {})
                if not meta.get("success"):
                    raise ApiError(
                        f"{method} {path} -> meta={meta}"
                    )
                return payload.get("data")
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.HTTPError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "请求失败 (attempt %d/%d) %s %s: %s",
                    attempt,
                    self.retries,
                    method,
                    path,
                    exc,
                )
                time.sleep(self.retry_sleep_s * attempt)
        raise RuntimeError(f"请求失败：{method} {path}: {last_exc}")

    def list_judgments(
        self,
        page_index: int = 1,
        page_size: int = 50,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageIndex": page_index,
            "pageSize": page_size,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        data = self._request(
            "GET", "/fcs/intelligence/intelliTaskInfo/page", params=params
        )
        return data or {}

    def iter_judgments_by_date(
        self,
        start_time: str,
        end_time: str,
        page_size: int = 50,
    ) -> Iterator[dict[str, Any]]:
        page_index = 1
        while True:
            page = self.list_judgments(
                page_index=page_index,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
            records = page.get("records") or []
            if not records:
                return
            for rec in records:
                yield rec
            total = page.get("total") or 0
            if page_index * page_size >= total:
                return
            page_index += 1

    def get_check_detail(self, flow_code: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/fcs/intelligence/intelliTaskInfo/getCheckDetail",
            params={"flowCode": flow_code},
        )
        return data or {}

    def download_image(self, url: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout_s, stream=True)
                resp.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                size = 0
                with tmp.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
                            size += len(chunk)
                tmp.replace(dest)
                return size
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                logger.warning(
                    "下载失败 (attempt %d/%d) %s: %s", attempt, self.retries, url, exc
                )
                time.sleep(self.retry_sleep_s * attempt)
        raise RuntimeError(f"下载失败：{url}")
