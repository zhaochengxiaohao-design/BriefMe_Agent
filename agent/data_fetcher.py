"""
永锋视觉系统 API 客户端

F12 Headers 确认:
  - 登录: GET  /packing-tape/api/record/doLogin?account=xxx&password=xxx
  - 认证: Cookie (packing tape token=xxx)，登录后自动设置
  - 历史: POST /packing-tape/api/record/query-condition
  - 响应: data.records 列表
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import time
from datetime import datetime, timedelta

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

RESULT_NORMAL = "NORMAL"
RESULT_ABNORMAL = "ABNORMAL"


@dataclass
class DailyStats:
    """每日钢卷生产统计"""

    date: str
    total: int
    normal: int
    abnormal: int
    unrecognized: int
    over_5_strips: int
    abnormal_diff_1: int  # 异常中已打数与应打数差值=1的数量
    abnormal_diff_gt1: int  # 异常中已打数与应打数差值>1的数量


class VisionAPIClient:
    """永锋视觉系统 HTTP 客户端（Cookie 认证）"""

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

    def __init__(self) -> None:
        self.base_url = settings.vision.base_url.rstrip("/")
        self._logged_in = False
        transport = httpx.HTTPTransport(retries=3)
        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{settings.vision.base_url}/",
            },
        )

    # ------------------------------------------------------------------
    #  登录 (GET, Cookie 认证)
    # ------------------------------------------------------------------
    def _ensure_login(self) -> None:
        if self._logged_in:
            return

        cfg = settings.vision

        # 先访问主页，获取 Istio Envoy 的 sticky session Cookie
        logger.info("获取 session cookie...")
        try:
            self._client.get(f"{self.base_url}/")
        except Exception:
            pass

        url = f"{self.base_url}{cfg.login_endpoint}"
        logger.info("正在登录视觉系统: %s", url)

        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = self._client.get(
                    url,
                    params={
                        "account": cfg.username,
                        "password": cfg.password,
                    },
                )
                if resp.status_code == 502:
                    raise httpx.HTTPStatusError(
                        "502 Bad Gateway", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()

                if not data.get("success"):
                    raise RuntimeError(f"登录失败: {data.get('message')}")

                self._logged_in = True
                logger.info("视觉系统登录成功")
                return
            except Exception as e:
                last_err = e
                logger.warning("登录尝试 %d/%d 失败: %s", attempt, self.MAX_RETRIES, e)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)

        raise RuntimeError(
            f"视觉系统登录失败（重试{self.MAX_RETRIES}次）: {last_err}\n"
            "可能原因: VPN 连接不稳定，请确认 aTrust 已连接后重试。"
        )

    # ------------------------------------------------------------------
    #  获取历史记录 (POST, Cookie 认证自动携带)
    # ------------------------------------------------------------------
    def get_history_records(self, date_str: str) -> List[Dict[str, Any]]:
        """
        获取指定日期的全部历史记录。

        Args:
            date_str: 日期，格式 YYYY-MM-DD
        """
        self._ensure_login()

        url = f"{self.base_url}{settings.vision.history_endpoint}"
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59"

        payload = {
            "currPage": 1,
            "pageSize": 9999,
            "startTime": start_time,
            "endTime": end_time,
            "orderItemList": [{"column": "gmt_create", "asc": False}],
        }

        logger.info("查询 %s 的历史记录", date_str)
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            logger.warning("API 返回失败: %s", data.get("message"))
            return []

        records = data.get("data", {}).get("records", [])
        logger.info("获取到 %s 的 %d 条记录", date_str, len(records))
        return records

    # ------------------------------------------------------------------
    #  统计汇总
    # ------------------------------------------------------------------
    def get_daily_stats(self, date_str: str) -> Optional[DailyStats]:
        """
        F12 确认的字段:
          result             → NORMAL / ABNORMAL / 其他=未识别
          detectPackQuantity → 已打数
        """
        records = self.get_history_records(date_str)
        if not records:
            return None

        total = len(records)
        normal = 0
        abnormal = 0
        unrecognized = 0
        over_5 = 0
        abnormal_diff_1 = 0
        abnormal_diff_gt1 = 0

        for r in records:
            status = r.get("result", "")
            detect = r.get("detectPackQuantity", 0)
            plan = r.get("planPackQuantity", 0)

            if status == RESULT_NORMAL:
                normal += 1
            elif status == RESULT_ABNORMAL:
                abnormal += 1
                try:
                    diff = abs(int(detect) - int(plan))
                    if diff == 1:
                        abnormal_diff_1 += 1
                    elif diff > 1:
                        abnormal_diff_gt1 += 1
                except (ValueError, TypeError):
                    pass
            else:
                unrecognized += 1

            try:
                if int(detect) > 5:
                    over_5 += 1
            except (ValueError, TypeError):
                pass

        return DailyStats(
            date=date_str,
            total=total,
            normal=normal,
            abnormal=abnormal,
            unrecognized=unrecognized,
            over_5_strips=over_5,
            abnormal_diff_1=abnormal_diff_1,
            abnormal_diff_gt1=abnormal_diff_gt1,
        )

    def get_date_range_stats(
        self, start_date: str, end_date: str
    ) -> List[Optional[DailyStats]]:
        """查询日期范围内每天的统计，返回列表"""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        results: List[Optional[DailyStats]] = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            stats = self.get_daily_stats(date_str)
            results.append(stats)
            current += timedelta(days=1)

        return results

    # ------------------------------------------------------------------
    #  下载异常图片（差值>1）
    # ------------------------------------------------------------------
    def download_abnormal_images(
        self, date_str: str, base_dir: str = "downloads"
    ) -> Dict[str, Any]:
        """
        下载指定日期中异常且差值>1的钢卷原图和渲染图。

        F12 确认字段: images (原图URL), renderImageUrl (渲染图URL)
        保存到: {base_dir}/{date}/钢卷号_序号_原图.jpg / _渲染图.jpg
        """
        records = self.get_history_records(date_str)

        targets = []
        for r in records:
            if r.get("result") != RESULT_ABNORMAL:
                continue
            try:
                diff = abs(
                    int(r.get("detectPackQuantity", 0))
                    - int(r.get("planPackQuantity", 0))
                )
            except (ValueError, TypeError):
                continue
            if diff > 1:
                targets.append(r)

        if not targets:
            return {"date": date_str, "target_count": 0, "downloaded": 0, "path": ""}

        out_dir = Path(base_dir) / date_str
        out_dir.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        for idx, r in enumerate(targets, 1):
            coil = r.get("coilNumber", "unknown")
            prefix = f"{coil}_{idx}"

            img_url = r.get("images") or ""
            render_url = r.get("renderImageUrl") or ""

            if img_url:
                downloaded += self._download_file(
                    img_url, out_dir / f"{prefix}_原图.jpg"
                )
            if render_url:
                downloaded += self._download_file(
                    render_url, out_dir / f"{prefix}_渲染图.jpg"
                )

        logger.info(
            "%s 差值>1异常钢卷: %d个, 下载图片: %d张, 保存至: %s",
            date_str, len(targets), downloaded, out_dir,
        )
        return {
            "date": date_str,
            "target_count": len(targets),
            "downloaded": downloaded,
            "path": str(out_dir.resolve()),
        }

    def _download_file(self, url: str, save_path: Path) -> int:
        """下载单个文件，成功返回1，失败返回0"""
        try:
            resp = self._client.get(url, timeout=60.0)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
            return 1
        except Exception as e:
            logger.warning("下载失败 %s: %s", url, e)
            return 0
