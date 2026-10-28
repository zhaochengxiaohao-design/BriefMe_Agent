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

防重与监督：
- 平台不去重（重复导入会叠图）：每次成功导入记进
  <日期>/datasets/uploaded.txt，重跑时已成功的 zip 自动跳过
- MinIO 直传有概率卡死：超过 upload_timeout（默认 5 分钟）就终止该
  上传，先传其他 zip，全部传完后统一重试一次
- 程序标记失败时同步终止平台上的任务
  （POST devel/dataset-jobs/<id>/stop/，幂等），避免同入口冲突
"""
from __future__ import annotations

import logging
import threading
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
# 成功导入记录（平台不去重，重复导入会叠图；靠本地记录避免重复）
RECORD_FILE_NAME = "uploaded.txt"


def _load_uploaded_names(datasets_dir: Path) -> set:
    """读 <日期>/datasets/uploaded.txt，返回已成功导入的 zip 文件名集合。

    每行格式：zip文件名<TAB>数据集名<TAB>任务号<TAB>时间。
    """
    record = datasets_dir / RECORD_FILE_NAME
    if not record.is_file():
        return set()
    names = set()
    try:
        for line in record.read_text(encoding="utf-8").splitlines():
            first = line.split("\t")[0].strip()
            if first:
                names.add(first)
    except OSError as exc:
        logger.warning("读取上传记录 %s 失败: %s", record, exc)
    return names


def _record_success(zip_path: Path, dataset_name: str, job_id: Optional[int]) -> None:
    """把一次成功导入追加进 zip 所在 datasets/ 的 uploaded.txt。"""
    record = zip_path.parent / RECORD_FILE_NAME
    line = "\t".join(
        [
            zip_path.name,
            dataset_name,
            f"任务 {job_id if job_id is not None else '-'}",
            time.strftime("%Y-%m-%d %H:%M:%S"),
        ]
    )
    try:
        with record.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning("写入上传记录 %s 失败: %s", record, exc)


class YongfengUploadError(Exception):
    """永锋数据集上传链路错误。"""


class EntryBusyError(YongfengUploadError):
    """平台该数据集入口还有导入任务在跑（可能是别的会话留下的）。

    同一入口并发上传/导入会中断之前的，先暂存到最后统一重试。
    """

    def __init__(self, job_id: int):
        super().__init__(f"平台任务 {job_id} 仍在运行，不抢入口")
        self.job_id = int(job_id)


def _run_with_deadline(fn, timeout: float, label: str) -> None:
    """在守护线程里执行 fn，主线程最多等 timeout 秒（超时监督）。

    用于 MinIO 直传这类有概率卡死的调用：超过 timeout 秒没有结果就
    抛 YongfengUploadError 终止该次调用（守护线程放弃，不阻塞进程），
    由上层暂存到最后统一重试；fn 自身抛错则原样转抛。
    """
    done = threading.Event()
    errors: List[BaseException] = []

    def _worker() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 线程内异常转抛主线程
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_worker, daemon=True, name=label)
    worker.start()
    # +1 秒余量：让 fn 内部的墙钟回调先有机会触发
    if not done.wait(timeout + 1.0):
        raise YongfengUploadError(
            f"{label} 超过 {timeout:.0f} 秒无结果，终止该上传（先传其他，最后统一重试）"
        )
    if errors:
        raise errors[0]


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

    @property
    def retry_pending(self) -> int:
        return sum(1 for it in self.items if it.status == "retry_pending")

    def to_lines(self) -> List[str]:
        lines: List[str] = []
        for it in self.items:
            name = Path(it.zip).name
            if it.status == "uploaded":
                lines.append(f"- {name} → {it.dataset_name}（任务 {it.job_id}，导入成功）")
            elif it.status == "skipped":
                lines.append(f"- {name} → ⏭️ {it.message}")
            elif it.status == "retry_pending":
                lines.append(f"- {name} → ⏳ {it.message}")
            else:
                lines.append(f"- {name} → ❌ {it.message}")
        return lines


@dataclass
class PendingRetry:
    """首轮没传上去、留到最后统一重试的 zip。

    blocking_job_id：占用该入口的未完成任务 id（任务超时未确认完成时，
    平台上可能还在跑；重试前先重查它的状态，仍在跑就不抢入口）。
    own_job：该任务是否就是本 zip 自己的（是的话重查成功即导入成功，
    不必重传）。
    """

    plan: ZipPlan
    reason: str = ""
    blocking_job_id: Optional[int] = None
    own_job: bool = False


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

    def find_running_import_job(self, dataset_id: int) -> Optional[int]:
        """查该数据集入口有没有仍在运行的导入任务（跨指令防护）。

        返回在跑任务 id；没有则 None。别的会话/上一轮超时留下的任务
        也会被查到，避免新一轮把平台上还在跑的任务顶掉。
        """
        body = self._get(
            "devel/dataset-jobs/",
            params={
                "namespace": self.cfg.namespace,
                "page": 1,
                "page_size": 20,
                "dataset": dataset_id,
                "type": 1,
                "status": 2,
            },
        )
        items = body.get("data") or []
        for it in items:
            # 服务端已按 status=2 过滤，客户端再核对一次
            if isinstance(it, dict) and it.get("status") == 2 and it.get("id"):
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
        """用 STS 临时密钥把 zip 直传 MinIO（AWS SigV4），带超时监督。

        直传有概率卡死：超过 cfg.upload_timeout（默认 5 分钟）就终止该
        上传（先传其他 zip，最后统一重试）。回调里再挂一道墙钟保险，
        双保险覆盖「有进度但太慢」和「完全卡死」两种情况。
        """
        import boto3
        from botocore.client import Config

        timeout = float(self.cfg.upload_timeout)
        deadline = time.monotonic() + timeout

        def _upload() -> None:
            def _guard(bytes_sent: int) -> None:
                if time.monotonic() > deadline:
                    raise YongfengUploadError(
                        f"zip 直传超过 {timeout:.0f} 秒，终止该上传（先传其他，最后统一重试）"
                    )

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
                Callback=_guard,
            )

        _run_with_deadline(_upload, timeout, f"minio-put:{zip_path.name}")
        logger.info("zip 已直传 MinIO: %s/%s/%s", signature["bucket"], signature["key"], zip_path.name)

    def stop_job(self, dataset_id: int, job_id: int) -> None:
        """终止平台上的导入任务（devel/dataset-jobs/<id>/stop/）。

        已完成/已取消的任务再停止是幂等的（实测返回 200 success）。
        """
        self._post(f"devel/dataset-jobs/{job_id}/stop/", {"dataset": dataset_id})
        logger.info("已终止平台任务 %s（数据集 %s）", job_id, dataset_id)

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
    running_job = client.find_running_import_job(dataset_id)
    if running_job:
        # 平台同入口一次只能导入一个包：别的会话/上一轮留下的任务
        # 还在跑时先不抢入口，交给统一重试轮处理。
        raise EntryBusyError(running_job)
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
    # 平台不去重（重复导入会叠图）：成功一条记一条，重跑时跳过
    _record_success(zip_path, plan.dataset_name, job_id)
    return item


def upload_day_zips(
    day_dir: Path,
    client: Optional[AthenaClient] = None,
    blocked_datasets: Optional[Dict[str, int]] = None,
    deferred: Optional[List[PendingRetry]] = None,
) -> Dict[str, Any]:
    """上传一个日期目录 datasets/ 里的 zip（第一轮）。失败不中断，逐条汇总。

    平台约束：同一数据集入口一次只能上传/导入一个压缩包，同时多个会
    中断之前的；不同入口互不影响。首轮传不上的（网络失败 / 导入任务
    超时 / 入口被未完成任务占用）不立刻下结论，先记入 deferred 暂存，
    等全部日期传完后由 upload_zips_for_dates 统一重试一次。

    blocked_datasets：入口名 → 未完成任务 id（跨日期共享）。
    """
    client = client or AthenaClient()
    blocked = blocked_datasets if blocked_datasets is not None else {}
    deferred = deferred if deferred is not None else []
    datasets_dir = day_dir / "datasets"
    plans = plan_day_zips(datasets_dir)
    # 已成功导入的 zip（uploaded.txt）：平台不去重，重跑时跳过
    uploaded_names = _load_uploaded_names(datasets_dir)
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
        if plan.zip_path.name in uploaded_names:
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="skipped",
                    message=(
                        f"已成功导入过（{RECORD_FILE_NAME} 有记录），跳过；"
                        "平台不去重，避免重复叠图"
                    ),
                )
            )
            continue
        if plan.dataset_name in blocked:
            reason = f"入口被未完成任务 {blocked[plan.dataset_name]} 占用"
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="retry_pending",
                    message=f"{reason}，暂时跳过，最后统一重试",
                )
            )
            deferred.append(
                PendingRetry(
                    plan,
                    reason=reason,
                    blocking_job_id=blocked[plan.dataset_name],
                )
            )
            continue
        try:
            item = upload_one_zip(plan, client)
        except EntryBusyError as exc:
            logger.warning("上传 %s 首轮入口被占: %s", plan.zip_path.name, exc)
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="retry_pending",
                    message=f"首轮入口被占（{exc}），最后统一重试",
                )
            )
            deferred.append(PendingRetry(plan, reason=str(exc), blocking_job_id=exc.job_id))
            continue
        except Exception as exc:  # noqa: BLE001 首轮失败先暂存，最后统一重试
            logger.warning("上传 %s 首轮失败: %s", plan.zip_path.name, exc)
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    status="retry_pending",
                    message=f"首轮失败（{exc}），最后统一重试",
                )
            )
            deferred.append(PendingRetry(plan, reason=str(exc)))
            continue
        if item.status == "failed":
            # 导入任务未确认完成（超时/失败）时平台上可能还在跑，
            # 锁住入口，防止后续同入口的 zip 把它顶掉。
            if item.job_id:
                blocked[item.dataset_name] = item.job_id
            deferred.append(
                PendingRetry(
                    plan,
                    reason=item.message,
                    blocking_job_id=item.job_id,
                    own_job=True,
                )
            )
            item.status = "retry_pending"
            item.message = f"首轮失败（{item.message}），最后统一重试"
        items.append(item)
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
        "retry_pending": report.retry_pending,
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


def _stop_platform_job(
    client: AthenaClient,
    dataset_name: str,
    dataset_id: Optional[int],
    job_id: Optional[int],
) -> bool:
    """程序标记失败时，同步终止平台上的对应任务，避免同入口冲突。

    尽力而为：终止失败只记日志，不影响总结；没有任务号则跳过。
    dataset_id 缺失时按名称现查一次。
    """
    if not job_id:
        return False
    try:
        if dataset_id is None:
            dataset_id = client.search_dataset_id(dataset_name)
        if dataset_id is None:
            logger.warning("找不到数据集 %s，无法终止平台任务 %s", dataset_name, job_id)
            return False
        client.stop_job(dataset_id, job_id)
        return True
    except Exception as exc:  # noqa: BLE001 终止失败不影响总结
        logger.warning("终止平台任务 %s 失败: %s", job_id, exc)
        return False


def _retry_deferred(
    deferred: List[PendingRetry],
    client: AthenaClient,
) -> List[UploadItem]:
    """最后统一重试首轮没传上的 zip（每条只重试一次）。

    重试前先重查占用入口的任务：仍在跑就不抢入口（避免中断）；
    自己的任务其实已经成功，则直接记成功，不必重传。重试仍失败
    的，顺手把平台上的任务终止掉，避免下一轮指令和它冲突。
    """
    retry_items: List[UploadItem] = []
    for pend in deferred:
        item = UploadItem(zip=str(pend.plan.zip_path), dataset_name=pend.plan.dataset_name)
        try:
            if pend.blocking_job_id:
                status = client.get_job_status(pend.blocking_job_id).get("status")
                if status == 2:
                    stopped = _stop_platform_job(
                        client, pend.plan.dataset_name, None, pend.blocking_job_id
                    )
                    item.status = "failed"
                    item.job_id = pend.blocking_job_id
                    item.message = (
                        f"重试时平台上任务 {pend.blocking_job_id} 仍在运行，本轮不抢入口"
                        f"{'，已终止平台任务' if stopped else ''}，请稍后重发指令补传"
                    )
                    retry_items.append(item)
                    continue
                if status == 3 and pend.own_job:
                    # 首轮超时的任务其实已经导入成功
                    item.status = "uploaded"
                    item.job_id = pend.blocking_job_id
                    item.message = "首轮超时的任务重查确认已导入成功"
                    _record_success(
                        Path(pend.plan.zip_path), pend.plan.dataset_name, pend.blocking_job_id
                    )
                    retry_items.append(item)
                    continue
                # 任务已结束（成功/失败）→ 入口空闲，重新上传
            retry_item = upload_one_zip(pend.plan, client)
            if retry_item.status == "uploaded":
                retry_item.message = "重试导入成功"
            else:
                if retry_item.job_id:
                    # 重试后导入任务仍未确认完成：终止平台任务，避免下一轮冲突
                    stopped = _stop_platform_job(
                        client,
                        pend.plan.dataset_name,
                        retry_item.dataset_id,
                        retry_item.job_id,
                    )
                    if stopped:
                        retry_item.message += "，已终止平台任务"
                retry_item.message = f"重试仍失败：{retry_item.message}"
            retry_items.append(retry_item)
        except EntryBusyError as exc:
            stopped = _stop_platform_job(client, pend.plan.dataset_name, None, exc.job_id)
            item.status = "failed"
            item.message = f"重试仍失败：{exc}{'，已终止平台任务' if stopped else ''}"
            retry_items.append(item)
        except Exception as exc:  # noqa: BLE001 重试失败即最终失败
            logger.warning("重试上传 %s 失败: %s", pend.plan.zip_path.name, exc)
            item.status = "failed"
            item.message = f"重试仍失败：{exc}"
            retry_items.append(item)
    return retry_items


def _retry_lines(retry_items: List[UploadItem]) -> List[str]:
    lines: List[str] = []
    for it in retry_items:
        rel = Path(it.zip)
        day = rel.parent.parent.name
        name = rel.name
        if it.status == "uploaded":
            lines.append(f"- {day}/{name} → {it.dataset_name}（任务 {it.job_id}，{it.message}）")
        elif it.status == "skipped":
            lines.append(f"- {day}/{name} → ⏭️ {it.message}")
        else:
            lines.append(f"- {day}/{name} → ❌ {it.message}")
    return lines


def upload_zips_for_dates(
    output_dir: str,
    dates: Optional[List[str]] = None,
    client: Optional[AthenaClient] = None,
) -> Dict[str, Any]:
    """上传多个日期的 datasets/ zip。dates 缺省时扫目录下全部日期。

    两轮制（平台约束：同一数据集入口一次只能上传/导入一个包）：
    第一轮按日期逐个传，传不上的先暂存（入口被占 / 网络失败 / 任务
    超时），不打断其他入口；全部日期传完后，统一重试暂存的每一条；
    重试仍失败才计为失败写进总结。
    """
    client = client or AthenaClient()
    root = Path(output_dir).expanduser()
    if not dates:
        dates = _discover_dataset_dates(root)
    if not dates:
        raise YongfengUploadError(
            f"{output_dir} 下没有找到含 datasets/ 的日期目录"
        )
    day_reports: List[Dict[str, Any]] = []
    main_items: List[UploadItem] = []
    # 入口名 → 未完成任务 id（首轮导入超时未确认完成的入口）
    blocked_datasets: Dict[str, int] = {}
    deferred: List[PendingRetry] = []
    for date in dates:
        day_dir = root / date
        if not (day_dir / "datasets").is_dir():
            main_items.append(
                UploadItem(
                    zip=str(day_dir / "datasets"),
                    status="skipped",
                    message=f"目录 {day_dir}/datasets 不存在，跳过",
                )
            )
            day_reports.append(
                {"day": date, "items": [], "lines": [f"- ⏭️ 目录 {date}/datasets 不存在，跳过"]}
            )
            continue
        day_report = upload_day_zips(
            day_dir, client=client, blocked_datasets=blocked_datasets, deferred=deferred
        )
        day_reports.append(day_report)
        for it in day_report["items"]:
            main_items.append(
                UploadItem(
                    zip=it["zip"],
                    dataset_name=it["dataset_name"],
                    dataset_id=it["dataset_id"],
                    job_id=it["job_id"],
                    status=it["status"],
                    message=it["message"],
                )
            )

    # ===== 第二轮：统一重试 =====
    retry_items = _retry_deferred(deferred, client) if deferred else []

    uploaded = sum(1 for it in main_items if it.status == "uploaded") + sum(
        1 for it in retry_items if it.status == "uploaded"
    )
    skipped = sum(1 for it in main_items if it.status == "skipped") + sum(
        1 for it in retry_items if it.status == "skipped"
    )
    failed = sum(1 for it in retry_items if it.status == "failed")

    lines = ["✅ 永锋数据集上传完成（金睛平台 pre-athena-mv）"]
    for day_report in day_reports:
        if day_report.get("lines"):
            lines.append(f"【{day_report.get('day')}】")
            lines.extend(day_report["lines"])
    if retry_items:
        lines.append("【统一重试】")
        lines.extend(_retry_lines(retry_items))
    lines.append(
        f"合计：成功 {uploaded} 个，跳过 {skipped} 个，失败 {failed} 个"
    )
    return {
        "output_dir": str(root),
        "dates": dates,
        "days": day_reports,
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "retry_items": [
            {
                "zip": it.zip,
                "dataset_name": it.dataset_name,
                "dataset_id": it.dataset_id,
                "job_id": it.job_id,
                "status": it.status,
                "message": it.message,
            }
            for it in retry_items
        ],
        "lines": lines,
        "summary_text": "\n".join(lines),
    }


def format_upload_summary(result: Dict[str, Any]) -> str:
    """把 upload_zips_for_dates 的结果整理成给用户看的文本。"""
    text = result.get("summary_text") or ""
    return text or "（上传结果为空）"
