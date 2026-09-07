"""永锋废钢检判系统客户端；独立于盛隆客户端。

只走 http://vision.lg.china-yongfeng.com/srape-steel ，Cookie 名 satoken。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
import urllib3

from config.settings import settings
from .scrap_models import YongfengRecord

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

PAGE_SIZE = 200
MAX_PAGES = 500
LOGIN_RETRIES = 3
LOGIN_RETRY_DELAY = 1.5


def _token_from_login(body: dict, cookie_value: str | None) -> str:
    """JSON tokenInfo 优先，其次 Cookie satoken / data.tokenValue。"""
    data = body.get("data") or {}
    token_info = data.get("tokenInfo") or {}
    return (
        str(token_info.get("tokenValue") or "").strip()
        or str(cookie_value or "").strip()
        or str(data.get("tokenValue") or "").strip()
    )


class YongfengScrapClient:
    def __init__(self) -> None:
        cfg = settings.yongfeng_scrap
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
        })
        self.token: str | None = None

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "YongfengScrapClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def login(self) -> None:
        last_err: Exception | None = None
        for attempt in range(1, LOGIN_RETRIES + 1):
            try:
                resp = self.session.post(
                    f"{self.base_url}{self.cfg.login_endpoint}",
                    params={"employeeId": self.cfg.employee_id, "password": self.cfg.password},
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": "0"},
                    data=b"",
                    timeout=60,
                    verify=False,
                )
                resp.raise_for_status()
                body = resp.json()
                if not (body.get("meta") or {}).get("success"):
                    raise RuntimeError(f"永锋登录失败: {body}")
                self.token = _token_from_login(body, self.session.cookies.get(self.cfg.cookie_name))
                if not self.token:
                    raise RuntimeError("永锋登录成功但未返回 token / satoken")
                self.session.cookies.set(self.cfg.cookie_name, self.token)
                logger.info("永锋废钢系统登录成功 token=%s…", self.token[:8])
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("永锋登录尝试 %d/%d 失败: %s", attempt, LOGIN_RETRIES, exc)
                if attempt < LOGIN_RETRIES:
                    time.sleep(LOGIN_RETRY_DELAY)
        raise RuntimeError(f"永锋登录失败（已重试{LOGIN_RETRIES}次）: {last_err}")

    def _headers(self) -> dict[str, str]:
        return {"token": self.token or ""}

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        _retried: bool = False,
        **kwargs: Any,
    ) -> dict:
        if not self.token:
            self.login()
        try:
            resp = self.session.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=self._headers(),
                timeout=60,
                verify=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            if not _retried:
                logger.warning("永锋请求中断，重登后重试: %s", exc)
                self.login()
                return self._request_json(method, endpoint, _retried=True, **kwargs)
            raise
        if resp.status_code in (401, 403) and not _retried:
            logger.warning("永锋 token 失效（HTTP %s），重新登录后重试", resp.status_code)
            self.login()
            return self._request_json(method, endpoint, _retried=True, **kwargs)
        resp.raise_for_status()
        body = resp.json()
        meta = body.get("meta") or {}
        if not meta.get("success"):
            code = meta.get("code")
            msg = str(meta.get("message") or "")
            if not _retried and (
                code in (401, 403)
                or "未登录" in msg
                or ("登录" in msg and "失效" in msg)
            ):
                logger.warning("永锋 token 疑似失效，重新登录后重试: %s", msg)
                self.login()
                return self._request_json(method, endpoint, _retried=True, **kwargs)
            raise RuntimeError(f"永锋接口失败: {meta.get('message')}")
        return body

    def query_list_by_date(self, date_str: str) -> list[YongfengRecord]:
        """查某一天全部车次（按 data.pages / total 翻完）。"""
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59"
        all_records: list[YongfengRecord] = []
        seen_flows: set[str] = set()
        page = 1
        while page <= MAX_PAGES:
            body = self._request_json(
                "GET",
                self.cfg.list_endpoint,
                params={
                    "current": page,
                    "size": PAGE_SIZE,
                    "startTime": start_time,
                    "endTime": end_time,
                },
            )
            data = body.get("data") or {}
            records = data.get("records") or []
            new_on_page = 0
            for item in records:
                rec = YongfengRecord.from_dict(item)
                if rec.flow_code and rec.flow_code in seen_flows:
                    continue
                if rec.flow_code:
                    seen_flows.add(rec.flow_code)
                all_records.append(rec)
                new_on_page += 1
            total_pages = int(data.get("pages") or 1)
            total = int(data.get("total") or 0)
            logger.info(
                "永锋列表 %s 第 %d/%d 页, 本页 %d 条, 累计 %d 条",
                date_str, page, total_pages, len(records), len(all_records),
            )
            if page >= total_pages or not records or new_on_page == 0:
                break
            if total and len(all_records) >= total:
                break
            page += 1
        return all_records

    def get_detail_by_flow(
        self,
        flow_code: str,
        station_number: Optional[int | str] = None,
    ) -> dict:
        params: dict[str, Any] = {"flowCode": flow_code}
        if isinstance(station_number, (list, tuple)) and station_number:
            params["stationNumber"] = station_number[-1]
        elif station_number not in (None, "", 0):
            params["stationNumber"] = station_number
        body = self._request_json("GET", self.cfg.detail_endpoint, params=params)
        return body.get("data") or {}
