"""废钢检判系统 HTTP 客户端

联调确认：
  POST /fcs/auth/login  form-urlencoded  Body: employeeId=xxx&password=xxx
    → 设置 Cookie satoken=<uuid>
  GET /fcs/intelligence/intelliTaskInfo/page?pageIndex=&pageSize=&startTime=&endTime=
  GET /fcs/intelligence/intelliTaskInfo/getCheckDetail?flowCode=<uuid>

认证：Cookie satoken + Header token（值相同）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from agent.scrap.calculator import aggregate_daily, calc_truck
from agent.scrap.models import DailyScrapStats, ScrapRecord, TruckStat
from config.settings import settings

logger = logging.getLogger(__name__)


class ScrapAPIError(RuntimeError):
    """废钢 API 调用失败"""


class ScrapClient:
    """废钢检判 HTTP 客户端 —— 自己管理登录闭环"""

    MAX_RETRIES = 3
    RETRY_DELAY = 1.5
    PAGE_SIZE = 200  # 一次拉 200 条，单日最多几十辆车，绝大多数一页搞定

    def __init__(self) -> None:
        cfg = settings.scrap
        self.base_url = cfg.base_url.rstrip("/")
        self.cfg = cfg
        self._sa_token: Optional[str] = None

        transport = httpx.HTTPTransport(retries=2)
        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{self.base_url}/fcs-web/",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ScrapClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    #  登录
    # ------------------------------------------------------------------
    def login(self) -> None:
        """登录并保存 satoken。重复调用会强制重新登录。"""
        url = f"{self.base_url}{self.cfg.login_endpoint}"
        logger.info("废钢系统登录: %s (employeeId=%s)", url, self.cfg.employee_id)

        last_err: Optional[Exception] = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = self._client.post(
                    url,
                    data={
                        "employeeId": self.cfg.employee_id,
                        "password": self.cfg.password,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                body = resp.json()
                meta = body.get("meta") or {}
                if not meta.get("success"):
                    raise ScrapAPIError(
                        f"登录失败: {meta.get('message')} (code={meta.get('code')})"
                    )

                token = self._client.cookies.get("satoken")
                if not token:
                    data = body.get("data") or {}
                    token = data.get("tokenValue") or data.get("token")
                if not token:
                    raise ScrapAPIError("登录成功但未取到 satoken")

                self._sa_token = token
                logger.info("废钢系统登录成功 satoken=%s…", token[:8])
                return
            except Exception as e:
                last_err = e
                logger.warning(
                    "登录尝试 %d/%d 失败: %s", attempt, self.MAX_RETRIES, e
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)

        raise ScrapAPIError(
            f"废钢系统登录失败（已重试{self.MAX_RETRIES}次）: {last_err}\n"
            "请确认 VPN 连接稳定、账号密码正确。"
        )

    def _ensure_login(self) -> None:
        if not self._sa_token:
            self.login()

    def _auth_headers(self) -> Dict[str, str]:
        return {"token": self._sa_token or ""}

    # ------------------------------------------------------------------
    #  列表查询（分页拉全）
    # ------------------------------------------------------------------
    def query_list_by_date(self, date_str: str) -> List[ScrapRecord]:
        """查某一天全部记录（自动翻页）"""
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59"
        return self._query_list_range(start_time, end_time)

    def _query_list_range(
        self, start_time: str, end_time: str
    ) -> List[ScrapRecord]:
        self._ensure_login()
        url = f"{self.base_url}{self.cfg.list_endpoint}"
        all_records: List[ScrapRecord] = []
        page = 1
        while True:
            params = {
                "pageIndex": page,
                "pageSize": self.PAGE_SIZE,
                "startTime": start_time,
                "endTime": end_time,
            }
            resp = self._request_authed("GET", url, params=params)
            body = resp.json()
            meta = body.get("meta") or {}
            if not meta.get("success"):
                raise ScrapAPIError(
                    f"列表查询失败: {meta.get('message')}"
                )
            data = body.get("data") or {}
            records = data.get("records") or []
            for item in records:
                all_records.append(ScrapRecord.from_list_item(item))

            total_pages = int(data.get("pages") or 1)
            logger.info(
                "废钢列表 %s~%s 第 %d/%d 页, 累计 %d 条",
                start_time[:10], end_time[:10], page, total_pages, len(all_records),
            )
            if page >= total_pages or not records:
                break
            page += 1

        return all_records

    # ------------------------------------------------------------------
    #  详情查询
    # ------------------------------------------------------------------
    def get_detail_by_flow(self, flow_code: str) -> Dict[str, Any]:
        """获取一辆车的完整检判详情"""
        self._ensure_login()
        url = f"{self.base_url}{self.cfg.detail_endpoint}"
        resp = self._request_authed("GET", url, params={"flowCode": flow_code})
        body = resp.json()
        meta = body.get("meta") or {}
        if not meta.get("success"):
            raise ScrapAPIError(
                f"详情查询失败({flow_code}): {meta.get('message')}"
            )
        return body.get("data") or {}

    def _request_authed(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
        _retried: bool = False,
    ) -> httpx.Response:
        """带 token 的请求；token 失效时自动重登一次"""
        resp = self._client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            return resp
        meta = body.get("meta") if isinstance(body, dict) else None
        if meta and not meta.get("success"):
            code = meta.get("code")
            msg = str(meta.get("message") or "")
            if not _retried and (
                code in (401, 403)
                or "未登录" in msg
                or "登录" in msg and "失效" in msg
            ):
                logger.warning("检测到 token 失效，重新登录后重试: %s", msg)
                self.login()
                return self._request_authed(
                    method, url, params=params, json_body=json_body, _retried=True
                )
        return resp

    # ------------------------------------------------------------------
    #  一日完整统计：列表 → 逐条详情 → 计算
    # ------------------------------------------------------------------
    def build_daily_stats(
        self, date_str: str, include_details: bool = True
    ) -> DailyScrapStats:
        """拉取一天数据，计算单车指标并汇总"""
        records = self.query_list_by_date(date_str)
        trucks: List[TruckStat] = []
        for rec in records:
            try:
                detail = self.get_detail_by_flow(rec.flow_code) if include_details else {}
            except Exception as e:
                logger.warning("详情获取失败 %s (%s): %s", rec.car_number, rec.flow_code, e)
                detail = {}
            stat = calc_truck(
                date_str=date_str,
                car_number=rec.car_number,
                station_number=rec.station_number,
                flow_code=rec.flow_code,
                detail_data=detail,
                exclude_steel_types=tuple(self.cfg.exclude_steel_types),
            )
            trucks.append(stat)
        return aggregate_daily(date_str, trucks)

    def build_range_stats(
        self, start_date: str, end_date: str
    ) -> List[DailyScrapStats]:
        """[start_date, end_date] 闭区间每日聚合"""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        out: List[DailyScrapStats] = []
        cur = start
        while cur <= end:
            out.append(self.build_daily_stats(cur.strftime("%Y-%m-%d")))
            cur += timedelta(days=1)
        return out

    # ------------------------------------------------------------------
    #  图片下载（MinIO 直连，无需认证）
    # ------------------------------------------------------------------
    def download_image(self, url: str, save_path: Path) -> bool:
        """返回是否下载成功"""
        try:
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
            resp.raise_for_status()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            logger.warning("图片下载失败 %s: %s", url, e)
            return False


def _smoke() -> None:
    """命令行 smoke：python -m agent.scrap.client --smoke"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--date", default="2026-04-15")
    p.add_argument("--limit", type=int, default=3)
    args = p.parse_args()
    if not args.smoke:
        p.print_help()
        return

    with ScrapClient() as cli:
        cli.login()
        records = cli.query_list_by_date(args.date)
        print(f"{args.date} 共 {len(records)} 条记录")
        for rec in records[: args.limit]:
            detail = cli.get_detail_by_flow(rec.flow_code)
            print("=" * 60)
            print(f"车牌: {rec.car_number}  工位: {rec.station_number}  flow={rec.flow_code}")
            manual = (detail.get("manualCheck") or {}).get("manualResults", "")
            print(f"人工: {manual}")
            for ai in detail.get("steelTypeRateDTOList") or []:
                print(f"  AI: type={ai.get('steelType')} level={ai.get('steelLevel')} rate={ai.get('steelRate')}")


if __name__ == "__main__":
    _smoke()
