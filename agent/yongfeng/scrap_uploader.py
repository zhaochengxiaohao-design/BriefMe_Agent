"""把下载产物 datasets/ 里的 zip 上传到赛信金睛视觉AI开发平台。

平台 http://pre-athena-mv.cisdigital.cn（namespace=1），目标是上面
「永锋-」开头的 13 个数据集。上传链路 2026-10-28 用 intern1 真机验证：

  1. POST /api/auth/login/  {username, password} → data.access（JWT）
  2. GET  /api/devel/datasets/?namespace=1&page=1&name=<名>&group=0
         &recursive=1&order=-utc_modify&page_size=20&annotation_category=
         → data[] 精确匹配 name 拿数据集 id
  3. GET  /api/devel/datasets/<id>/upload/ → MinIO STS 临时密钥
         {endpoint, access_key, secret_key, session_token, bucket, key, region}
  4. PUT  {endpoint}/{bucket}/{key}（AWS SigV4，boto3 path-style）
  5. POST /api/devel/dataset-jobs/?namespace=1
         {dataset, type:1, namespace, config:{source:{files:[{key}]},
          options:{mode:1}}}  → data.id 导入任务，平台自动执行
  6. GET  /api/devel/dataset-jobs/<job_id>/ → status 2 运行中 / 3 成功

命名规则（用户定稿，zip 前缀能对上就行）：
  废钢分类数据集.zip          → 永锋-废钢分类
  重废1_实例分割数据集.zip    → 永锋-重废1
  重废1_边缘分割数据集.zip    → 同料型只上传一个，与实例分割内容相同则跳过
  平均料型_实例分割数据集.zip → 平台没有「永锋-平均料型」，跳过并报告
导入只用无标注模式（options 不带 annotation_format）；先传 zip 再由
程序显式建导入任务（手动导入）。单条失败不中断，全部进总结。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config.settings import YongfengDatasetUploadConfig, settings

logger = logging.getLogger(__name__)

# 本地打包命名（见 scrap_packager.py）
CLASSIFICATION_ZIP_NAME = "废钢分类数据集.zip"
INSTANCE_SUFFIX = "_实例分割数据集.zip"
EDGE_SUFFIX = "_边缘分割数据集.zip"
AVERAGE_PREFIX = "平均料型"
# 平台导入页限制：单文件最大 10GB
MAX_ZIP_BYTES = 10 * 1024**3


class YongfengUploadError(Exception):
    """永锋数据集上传链路错误。"""


@dataclass
class ZipPlan:
    """一个 zip 的上传计划。"""

    zip_path: Path
    dataset_name: str = ""  # 目标数据集名，如「永锋-重废1」
    skip: bool = False
    reason: str = ""


@dataclass
class UploadItem:
    """单个 zip 的上传结果。"""

    zip: str
    dataset_name: str = ""
    dataset_id: Optional[int] = None
    job_id: Optional[int] = None
    status: str = "pending"  # uploaded / skipped / failed
    message: str = ""


@dataclass
class UploadReport:
    """一次上传（单日或多日）的汇总。"""

    items: List[UploadItem] = field(default_factory=list)
    days: List[str] = field(default_factory=list)

    @property
    def uploaded(self) -> int:
        return sum(1 for it in self.items if it.status == "uploaded")

    @property
    def skipped(self) -> int:
        return sum(1 for it in self.items if it.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for it in self.items if it.status == "failed")

    def to_lines(self) -> List[str]:
        lines: List[str] = []
        for it in self.items:
            name = Path(it.zip).name
            if it.status == "uploaded":
                lines.append(f"- {name} → {it.dataset_name}（任务 {it.job_id}，导入成功）")
            elif it.status == "skipped":
                lines.append(f"- {name} → ⏭️ {it.message}")
            else:
                lines.append(f"- {name} → ❌ {it.message}")
        return lines


def parse_zip_prefix(zip_name: str) -> Optional[str]:
    """zip 文件名 → 目标数据集的「永锋-」后缀前缀。

    废钢分类数据集.zip → 废钢分类
    重废1_实例分割数据集.zip → 重废1
    重废1_边缘分割数据集.zip → 重废1
    解析不出（非 datasets 打包命名）返回 None。
    """
    name = zip_name.strip()
    if name == CLASSIFICATION_ZIP_NAME:
        return "废钢分类"
    for suffix in (INSTANCE_SUFFIX, EDGE_SUFFIX):
        if name.endswith(suffix):
            prefix = name[: -len(suffix)]
            if prefix:
                return prefix
    return None


def plan_day_zips(datasets_dir: Path) -> List[ZipPlan]:
    """扫描 <日期>/datasets/ 下的 zip，生成上传计划。

    同料型的实例分割包和边缘分割包内容相同，只上传一个
    （优先实例分割包，没有实例分割包才用边缘分割包兜底）。
    """
    if not datasets_dir.is_dir():
        return []
    groups: Dict[str, List[Path]] = {}
    plans: List[ZipPlan] = []
    for zip_path in sorted(datasets_dir.glob("*.zip")):
        prefix = parse_zip_prefix(zip_path.name)
        if prefix is None:
            plans.append(
                ZipPlan(
                    zip_path,
                    skip=True,
                    reason="不是 datasets 打包命名，未上传",
                )
            )
            continue
        groups.setdefault(prefix, []).append(zip_path)
    for prefix, zips in groups.items():
        dataset_name = f"{settings.yongfeng_upload.dataset_name_prefix}{prefix}"

        def _rank(p: Path) -> int:
            return 0 if p.name.endswith(INSTANCE_SUFFIX) else 1

        zips.sort(key=_rank)
        chosen, extras = zips[0], zips[1:]
        plans.append(ZipPlan(chosen, dataset_name=dataset_name))
        for extra in extras:
            plans.append(
                ZipPlan(
                    extra,
                    dataset_name=dataset_name,
                    skip=True,
                    reason="同料型已选另一个压缩包（内容相同只传一个）",
                )
            )
    return plans


class AthenaClient:
    """赛信金睛视觉AI开发平台 API 客户端（含 MinIO 直传与导入任务）。"""

    def __init__(self, cfg: Optional[YongfengDatasetUploadConfig] = None):
        self.cfg = cfg or settings.yongfeng_upload
        self._session = requests.Session()
        self._token: str = ""

    # ---------- 基础请求 ----------

    def login(self) -> str:
        url = f"{self.cfg.base_url}/api/auth/login/"
        resp = self._session.post(
            url,
            json={"username": self.cfg.username, "password": self.cfg.password},
            timeout=self.cfg.login_timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise YongfengUploadError(f"平台登录失败: {body.get('message')}")
        token = (body.get("data") or {}).get("access") or ""
        if not token:
            raise YongfengUploadError("平台登录响应缺少 access token")
        self._token = token
        logger.info("金睛平台登录成功（%s）", self.cfg.username)
        return token

    def _headers(self) -> Dict[str, str]:
        if not self._token:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("get", path, params=params)

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("post", path, payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """带 401 自动重登的 API 请求，返回响应 JSON。"""
        url = f"{self.cfg.base_url}/api/{path.lstrip('/')}"
        for attempt in (1, 2):
            resp = self._session.request(
                method,
                url,
                params=params,
                json=payload,
                headers=self._headers(),
                timeout=self.cfg.request_timeout,
            )
            if resp.status_code == 401 and attempt == 1:
                logger.info("平台 token 过期，重新登录")
                self._token = ""
                continue
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise YongfengUploadError(
                    f"平台接口 {path} 返回异常: {body.get('message')}"
                )
            return body
        raise YongfengUploadError(f"平台接口 {path} 重新登录后仍 401")

    # ---------- 业务步骤 ----------

    def search_dataset_id(self, dataset_name: str) -> Optional[int]:
        """按名称搜索数据集并精确匹配，返回 id；没有精确匹配返回 None。"""
        body = self._get(
            "devel/datasets/",
            params={
                "namespace": self.cfg.namespace,
                "page": 1,
                "name": dataset_name,
                "group": 0,
                "recursive": 1,
                "order": "-utc_modify",
                "page_size": 20,
                "annotation_category": "",
            },
        )
        items = body.get("data") or []
        for it in items:
            if isinstance(it, dict) and it.get("name") == dataset_name:
                return int(it["id"])
        return None

    def get_upload_signature(self, dataset_id: int) -> Dict[str, str]:
        """取 MinIO 直传签名（单文件 key 模式）。"""
        body = self._get(f"devel/datasets/{dataset_id}/upload/")
        data = body.get("data") or {}
        if not data.get("key"):
            raise YongfengUploadError(f"数据集 {dataset_id} 上传签名缺少 key")
        return data

    def put_zip_to_minio(self, signature: Dict[str, str], zip_path: Path) -> None:
        """用 STS 临时密钥把 zip 直传 MinIO（AWS SigV4）。"""
        import boto3
        from botocore.client import Config

        s3 = boto3.client(
            "s3",
            endpoint_url=signature["endpoint"],
            aws_access_key_id=signature["access_key"],
            aws_secret_access_key=signature["secret_key"],
            aws_session_token=signature.get("session_token") or None,
            region_name=signature.get("region") or "us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=30,
                read_timeout=self.cfg.minio_timeout,
                retries={"max_attempts": 2},
            ),
        )
        s3.upload_file(
            str(zip_path),
            signature["bucket"],
            signature["key"],
        )
        logger.info("zip 已直传 MinIO: %s/%s/%s", signature["bucket"], signature["key"], zip_path.name)

    def create_import_job(self, dataset_id: int, key: str) -> int:
        """建数据集导入任务（无标注模式：options 只有 mode=1）。"""
        body = self._post(
            "devel/dataset-jobs/",
            {
                "dataset": dataset_id,
                "type": 1,
                "namespace": self.cfg.namespace,
                "config": {
                    "source": {"files": [{"key": key}]},
                    "options": {"mode": 1},
                },
            },
        )
        data = body.get("data") or {}
        job_id = data.get("id")
        if not job_id:
            raise YongfengUploadError(f"导入任务创建失败: {body.get('message')}")
        return int(job_id)

    def get_job_status(self, job_id: int) -> Dict[str, Any]:
        body = self._get(f"devel/dataset-jobs/{job_id}/")
        data = body.get("data") or {}
        return {
            "status": data.get("status"),
            "duration": data.get("duration"),
            "info": data.get("info"),
        }

    def wait_job(self, job_id: int) -> Dict[str, Any]:
        """轮询导入任务直到结束。status 2=运行中，3=成功，其他=失败。"""
        last: Dict[str, Any] = {}
        for _ in range(self.cfg.job_poll_max_attempts):
            last = self.get_job_status(job_id)
            status = last.get("status")
            if status == 3:
                return last
            if status != 2:
                raise YongfengUploadError(
                    f"导入任务 {job_id} 失败（status={status}，info={last.get('info')}）"
                )
            time.sleep(self.cfg.job_poll_interval)
        raise YongfengUploadError(f"导入任务 {job_id} 等待超时（>{self.cfg.job_poll_max_attempts} 次轮询）")


def upload_one_zip(plan: ZipPlan, client: AthenaClient) -> UploadItem:
    """上传一个 zip 并等待导入任务完成。"""
    item = UploadItem(zip=str(plan.zip_path), dataset_name=plan.dataset_name)
    zip_path = plan.zip_path
    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        item.status = "failed"
        item.message = f"zip {size / 1024**3:.1f}GB 超过平台 10GB 上限"
        return item
    dataset_id = client.search_dataset_id(plan.dataset_name)
    if dataset_id is None:
        item.status = "skipped"
        item.message = f"平台上没有数据集「{plan.dataset_name}」（按名称搜索无精确匹配），未上传"
        return item
    item.dataset_id = dataset_id
    signature = client.get_upload_signature(dataset_id)
    key = signature["key"]
    client.put_zip_to_minio(signature, zip_path)
    job_id = client.create_import_job(dataset_id, key)
    item.job_id = job_id
    try:
        client.wait_job(job_id)
    except Exception as exc:  # noqa: BLE001 导入未确认完成，保留 job_id 供入口锁
        item.status = "failed"
        item.message = str(exc)
        return item
    item.status = "uploaded"
    item.message = "导入成功"
    return item


def upload_day_zips(
    day_dir: Path,
    client: Optional[AthenaClient] = None,
    blocked_datasets: Optional[set] = None,
) -> Dict[str, Any]:
    """上传一个日期目录 datasets/ 里的 zip。失败不中断，逐条汇总。

    blocked_datasets：平台同一数据集入口一次只能上传/导入一个压缩包，
    同时多个会中断之前的。若某入口已有未完成的导入任务，本轮跳过该
    入口的后续 zip（不同入口互不影响，仍正常上传）。
    """
    client = client or AthenaClient()
    blocked = blocked_datasets if blocked_datasets is not None else set()
    datasets_dir = day_dir / "datasets"
    plans = plan_day_zips(datasets_dir)
    items: List[UploadItem] = []
    for plan in plans:
        if plan.skip:
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="skipped",
                    message=plan.reason,
                )
            )
            continue
        if plan.dataset_name in blocked:
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="skipped",
                    message=(
                        "该数据集入口已有未完成的导入任务（平台同入口一次只能"
                        "导入一个包，并发会中断之前的），本轮跳过；"
                        "平台任务结束后重发指令即可补传"
                    ),
                )
            )
            continue
        try:
            items.append(upload_one_zip(plan, client))
        except Exception as exc:  # noqa: BLE001 单条失败不中断
            logger.error("上传 %s 失败: %s", plan.zip_path.name, exc)
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="failed",
                    message=str(exc),
                )
            )
    report = UploadReport(items=items, days=[day_dir.name])
    return {
        "day": day_dir.name,
        "datasets_dir": str(datasets_dir),
        "items": [
            {
                "zip": it.zip,
                "dataset_name": it.dataset_name,
                "dataset_id": it.dataset_id,
                "job_id": it.job_id,
                "status": it.status,
                "message": it.message,
            }
            for it in items
        ],
        "uploaded": report.uploaded,
        "skipped": report.skipped,
        "failed": report.failed,
        "lines": report.to_lines(),
    }


def _discover_dataset_dates(output_dir: Path) -> List[str]:
    """目录下所有含 datasets/ 子目录的日期文件夹名。"""
    if not output_dir.is_dir():
        return []
    dates = []
    for child in sorted(output_dir.iterdir()):
        if child.is_dir() and (child / "datasets").is_dir():
            dates.append(child.name)
    return dates


def upload_zips_for_dates(
    output_dir: str,
    dates: Optional[List[str]] = None,
    client: Optional[AthenaClient] = None,
) -> Dict[str, Any]:
    """上传多个日期的 datasets/ zip。dates 缺省时扫目录下全部日期。"""
    client = client or AthenaClient()
    root = Path(output_dir).expanduser()
    if not dates:
        dates = _discover_dataset_dates(root)
    if not dates:
        raise YongfengUploadError(
            f"{output_dir} 下没有找到含 datasets/ 的日期目录"
        )
    report = UploadReport()
    day_reports: List[Dict[str, Any]] = []
    # 平台约束：同一数据集入口一次只能上传/导入一个压缩包，并发会中断
    # 之前的。记录「导入任务已创建但未确认完成」的入口，后续日期跳过。
    blocked_datasets: set = set()
    for date in dates:
        day_dir = root / date
        if not (day_dir / "datasets").is_dir():
            report.items.append(
                UploadItem(
                    zip=str(day_dir / "datasets"),
                    status="skipped",
                    message=f"目录 {day_dir}/datasets 不存在，跳过",
                )
            )
            day_reports.append({"day": date, "items": [], "lines": report.to_lines()[-1:]})
            continue
        day_report = upload_day_zips(day_dir, client=client, blocked_datasets=blocked_datasets)
        day_reports.append(day_report)
        for it in day_report["items"]:
            report.items.append(
                UploadItem(
                    zip=it["zip"],
                    dataset_name=it["dataset_name"],
                    dataset_id=it["dataset_id"],
                    job_id=it["job_id"],
                    status=it["status"],
                    message=it["message"],
                )
            )
            # 导入任务已建但没等到完成（超时/失败）→ 该入口可能仍有任务
            # 在跑，锁住避免下一天的包把平台上的任务顶掉。
            if it["job_id"] and it["status"] != "uploaded":
                blocked_datasets.add(it["dataset_name"])
    lines = ["✅ 永锋数据集上传完成（金睛平台 pre-athena-mv）"]
    for day_report in day_reports:
        if day_report.get("lines"):
            lines.append(f"【{day_report.get('day')}】")
            lines.extend(day_report["lines"])
    lines.append(
        f"合计：成功 {report.uploaded} 个，跳过 {report.skipped} 个，失败 {report.failed} 个"
    )
    return {
        "output_dir": str(root),
        "dates": dates,
        "days": day_reports,
        "uploaded": report.uploaded,
        "skipped": report.skipped,
        "failed": report.failed,
        "lines": lines,
        "summary_text": "\n".join(lines),
    }


def format_upload_summary(result: Dict[str, Any]) -> str:
    """把 upload_zips_for_dates 的结果整理成给用户看的文本。"""
    text = result.get("summary_text") or ""
    return text or "（上传结果为空）"
