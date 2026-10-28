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
  废钢杂质数据集.zip          → 永锋废钢杂质分割（固定 id，默认 28392）
  重废1_实例分割数据集.zip    → 永锋-重废1
  重废1_边缘分割数据集.zip    → 同料型只上传一个，与实例分割内容相同则跳过
  平均料型_实例分割数据集.zip → 平台没有「永锋-平均料型」，跳过并报告
导入只用无标注模式（options 不带 annotation_format）；先传 zip 再由
程序显式建导入任务（手动导入）。单条失败不中断，全部进总结。

防重与监督：
- 平台不去重（重复导入会叠图）：成功记进 <日期>/datasets/uploaded.txt
  （按文件名）；建任务后写 upload_pending.txt，超时中断下次可重查补记
- MinIO 直传卡死：超过 upload_timeout（默认 5 分钟）终止该条并统一重试
- 重试轮只终止本程序管辖入口（「永锋-」前缀 + 杂质入口）上、自己建的任务
- 空 / 损坏 zip、平均料型包在计划阶段跳过
- 登录与 API 对 5xx/网络抖动有限次退避重试
"""
from __future__ import annotations

import logging
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config.settings import YongfengDatasetUploadConfig, settings

logger = logging.getLogger(__name__)

# 本地打包命名（见 scrap_packager.py）
CLASSIFICATION_ZIP_NAME = "废钢分类数据集.zip"
IMPURITY_ZIP_NAME = "废钢杂质数据集.zip"
INSTANCE_SUFFIX = "_实例分割数据集.zip"
EDGE_SUFFIX = "_边缘分割数据集.zip"
AVERAGE_PREFIX = "平均料型"
# 平台导入页限制：单文件最大 10GB
MAX_ZIP_BYTES = 10 * 1024**3
# 成功导入记录（平台不去重，重复导入会叠图；靠本地记录避免重复）
# 每个 <日期>/datasets/ 各自一份，按 zip 文件名去重。
RECORD_FILE_NAME = "uploaded.txt"
# 已建导入任务、尚未确认成功的本地登记（防超时后重跑叠图）
PENDING_FILE_NAME = "upload_pending.txt"


def _managed_entry(dataset_name: str) -> bool:
    """是否属于本功能管辖的永锋数据集入口（仅这些入口允许 stop）。"""
    if not dataset_name:
        return False
    prefix = (settings.yongfeng_upload.dataset_name_prefix or "永锋-").strip()
    if dataset_name.startswith(prefix):
        return True
    impurity = (settings.yongfeng_upload.impurity_dataset_name or "").strip()
    return bool(impurity) and dataset_name == impurity


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
    _clear_pending_zip(zip_path)


def _load_pending(datasets_dir: Path) -> Dict[str, Dict[str, Any]]:
    """读 upload_pending.txt → {zip文件名: {dataset_name, job_id, dataset_id}}。"""
    record = datasets_dir / PENDING_FILE_NAME
    if not record.is_file():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for line in record.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3 or not parts[0]:
                continue
            try:
                job_id = int(parts[2].replace("任务", "").strip())
            except ValueError:
                continue
            dataset_id = None
            if len(parts) >= 4 and parts[3].isdigit():
                dataset_id = int(parts[3])
            out[parts[0]] = {
                "dataset_name": parts[1],
                "job_id": job_id,
                "dataset_id": dataset_id,
            }
    except OSError as exc:
        logger.warning("读取待确认记录 %s 失败: %s", record, exc)
    return out


def _record_pending(
    zip_path: Path,
    dataset_name: str,
    job_id: int,
    dataset_id: Optional[int],
) -> None:
    """建导入任务后立刻登记，等确认成功再清掉。"""
    _clear_pending_zip(zip_path)
    record = zip_path.parent / PENDING_FILE_NAME
    line = "\t".join(
        [
            zip_path.name,
            dataset_name,
            str(job_id),
            str(dataset_id if dataset_id is not None else ""),
            time.strftime("%Y-%m-%d %H:%M:%S"),
        ]
    )
    try:
        with record.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning("写入待确认记录 %s 失败: %s", record, exc)


def _clear_pending_zip(zip_path: Path) -> None:
    """从 upload_pending.txt 去掉该 zip 的行。"""
    record = zip_path.parent / PENDING_FILE_NAME
    if not record.is_file():
        return
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if not ln.startswith(zip_path.name + "\t")]
        if kept:
            record.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            record.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("清理待确认记录 %s 失败: %s", record, exc)


def _reconcile_pending(
    client: AthenaClient,
    datasets_dir: Path,
) -> tuple[set, set]:
    """对照平台重查上一轮未确认的任务。

    返回 (已补记成功的 zip 名集合, 仍在跑的 zip 名集合)。
    仍在跑的不要再传；已成功的写入 uploaded.txt；已失败的清 pending 允许重传。
    """
    pending = _load_pending(datasets_dir)
    confirmed: set = set()
    still_running: set = set()
    for zip_name, info in pending.items():
        job_id = int(info["job_id"])
        try:
            status = client.get_job_status(job_id).get("status")
        except Exception as exc:  # noqa: BLE001 查不到就留着下次再问
            logger.warning("重查待确认任务 %s 失败: %s", job_id, exc)
            still_running.add(zip_name)
            continue
        zip_path = datasets_dir / zip_name
        if status == 3:
            _record_success(zip_path, str(info.get("dataset_name") or ""), job_id)
            confirmed.add(zip_name)
        elif status == 2:
            still_running.add(zip_name)
        else:
            _clear_pending_zip(zip_path)
    return confirmed, still_running


class YongfengUploadError(Exception):
    """永锋数据集上传链路错误。"""


class EntryBusyError(YongfengUploadError):
    """平台该数据集入口还有导入任务在跑（可能是别的会话留下的）。

    同一入口并发上传/导入会中断之前的，先暂存到最后统一重试。
    """

    def __init__(self, job_id: int):
        super().__init__(f"平台任务 {job_id} 仍在运行，不抢入口")
        self.job_id = int(job_id)


def _run_with_deadline(
    fn,
    timeout: float,
    label: str,
    abort: Optional[threading.Event] = None,
) -> None:
    """在守护线程里执行 fn，主线程最多等 timeout 秒（超时监督）。

    超时后置位 abort，让直传 Callback / 短 read_timeout 尽快结束工作线程，
    避免主流程已放弃后后台仍长时间占着带宽。
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
        if abort is not None:
            abort.set()
        # 再等一小会，让 Callback / socket 超时把工作线程收掉
        done.wait(3.0)
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


def _normalize_prefix(prefix: str) -> str:
    """全角罗马数字归一化：本地打包用 Ⅰ/Ⅱ/Ⅲ，平台数据集名用 1/2/3。"""
    return (
        prefix.replace("Ⅰ", "1")
        .replace("Ⅱ", "2")
        .replace("Ⅲ", "3")
        .replace("Ⅳ", "4")
        .replace("Ⅴ", "5")
    )


def parse_zip_prefix(zip_name: str) -> Optional[str]:
    """zip 文件名 → 目标数据集的「永锋-」后缀前缀。

    废钢分类数据集.zip → 废钢分类
    废钢杂质数据集.zip → None（由 plan_day_zips 单独映射到固定入口）
    重废1_实例分割数据集.zip → 重废1
    统料Ⅰ_实例分割数据集.zip → 统料1（全角罗马数字归一化为半角）
    重废1_边缘分割数据集.zip → 重废1
    解析不出（非 datasets 打包命名）返回 None。
    """
    name = zip_name.strip()
    if name == CLASSIFICATION_ZIP_NAME:
        return "废钢分类"
    if name == IMPURITY_ZIP_NAME:
        return None
    for suffix in (INSTANCE_SUFFIX, EDGE_SUFFIX):
        if name.endswith(suffix):
            prefix = name[: -len(suffix)]
            if prefix:
                return _normalize_prefix(prefix)
    return None


def _zip_skip_reason(zip_path: Path) -> Optional[str]:
    """空文件或损坏/非 zip → 跳过原因；可用则返回 None。"""
    try:
        size = zip_path.stat().st_size
    except OSError:
        return "无法读取文件，跳过"
    if size <= 0:
        return "空 zip（0 字节），跳过"
    if not zipfile.is_zipfile(zip_path):
        return "不是合法 zip（损坏或伪后缀），跳过"
    return None


def plan_day_zips(datasets_dir: Path) -> List[ZipPlan]:
    """扫描 <日期>/datasets/ 下的 zip，生成上传计划。

    同料型的实例分割包和边缘分割包内容相同，只上传一个
    （优先实例分割包，没有实例分割包才用边缘分割包兜底）。
    废钢杂质数据集.zip 单独映射到平台「永锋废钢杂质分割」。
    """
    if not datasets_dir.is_dir():
        return []
    groups: Dict[str, List[Path]] = {}
    plans: List[ZipPlan] = []
    for zip_path in sorted(datasets_dir.glob("*.zip")):
        bad = _zip_skip_reason(zip_path)
        if bad:
            plans.append(ZipPlan(zip_path, skip=True, reason=bad))
            continue
        if zip_path.name == IMPURITY_ZIP_NAME:
            plans.append(
                ZipPlan(
                    zip_path,
                    dataset_name=settings.yongfeng_upload.impurity_dataset_name,
                )
            )
            continue
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
        if prefix == AVERAGE_PREFIX:
            plans.append(
                ZipPlan(
                    zip_path,
                    dataset_name=f"{settings.yongfeng_upload.dataset_name_prefix}{prefix}",
                    skip=True,
                    reason="平台没有平均料型数据集，跳过",
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
        last_exc: Optional[BaseException] = None
        for attempt in range(1, 7):
            try:
                resp = self._session.post(
                    url,
                    json={"username": self.cfg.username, "password": self.cfg.password},
                    timeout=self.cfg.login_timeout,
                )
                if resp.status_code in (502, 503, 504):
                    raise requests.HTTPError(
                        f"{resp.status_code} Server Error", response=resp
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
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status not in (502, 503, 504) and not isinstance(
                    exc, (requests.ConnectionError, requests.Timeout)
                ):
                    raise
                wait = min(60.0, 2.0 ** attempt)
                logger.warning(
                    "平台登录暂不可用（%s），%.0fs 后重试 %d/6", exc, wait, attempt
                )
                time.sleep(wait)
        raise YongfengUploadError(f"平台登录多次重试仍失败: {last_exc}")

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
        """带 401 自动重登 + 5xx/网络抖动退避重试的 API 请求。"""
        url = f"{self.cfg.base_url}/api/{path.lstrip('/')}"
        auth_retried = False
        last_exc: Optional[BaseException] = None
        for attempt in range(1, 8):
            try:
                resp = self._session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.cfg.request_timeout,
                )
                if resp.status_code == 401 and not auth_retried:
                    logger.info("平台 token 过期，重新登录")
                    self._token = ""
                    auth_retried = True
                    continue
                if resp.status_code in (502, 503, 504):
                    raise requests.HTTPError(
                        f"{resp.status_code} Server Error for url: {resp.url}",
                        response=resp,
                    )
                resp.raise_for_status()
                body = resp.json()
                if body.get("code") != 0:
                    raise YongfengUploadError(
                        f"平台接口 {path} 返回异常: {body.get('message')}"
                    )
                return body
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status in (502, 503, 504) or isinstance(
                    exc, (requests.ConnectionError, requests.Timeout)
                )
                if not retryable:
                    raise
                wait = min(60.0, 2.0 ** attempt)
                logger.warning(
                    "平台接口 %s 暂不可用（%s），%.0fs 后重试 %d/7",
                    path,
                    exc,
                    wait,
                    attempt,
                )
                time.sleep(wait)
        raise YongfengUploadError(f"平台接口 {path} 多次重试仍失败: {last_exc}")

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
        上传（先传其他 zip，最后统一重试）。abort 事件 + 短 read_timeout +
        Callback 墙钟，尽量让工作线程在主流程放弃后尽快停下来。
        """
        import boto3
        from botocore.client import Config

        timeout = float(self.cfg.upload_timeout)
        deadline = time.monotonic() + timeout
        abort = threading.Event()
        # HTTP 读超时压到监督上限附近，避免主线程已超时后台还卡 30 分钟
        read_timeout = max(30.0, min(float(self.cfg.minio_timeout), timeout + 30.0))

        def _upload() -> None:
            def _guard(_bytes_sent: int) -> None:
                if abort.is_set() or time.monotonic() > deadline:
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
                    read_timeout=read_timeout,
                    retries={"max_attempts": 2},
                ),
            )
            s3.upload_file(
                str(zip_path),
                signature["bucket"],
                signature["key"],
                Callback=_guard,
            )

        _run_with_deadline(
            _upload, timeout, f"minio-put:{zip_path.name}", abort=abort
        )
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
    cfg = settings.yongfeng_upload
    # 杂质入口平台名不含「永锋-」前缀，用配置的固定 id，避免搜名失败
    if (
        plan.dataset_name == cfg.impurity_dataset_name
        and cfg.impurity_dataset_id
    ):
        dataset_id = cfg.impurity_dataset_id
    else:
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
    # 建任务后立刻落 pending：即便 wait 超时/进程中断，下次也能重查避免叠图
    _record_pending(zip_path, plan.dataset_name, job_id, dataset_id)
    try:
        client.wait_job(job_id)
    except Exception as exc:  # noqa: BLE001 导入未确认完成，保留 pending + job_id
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
    # 上一轮超时未确认的任务：先问平台，成功则补记 uploaded.txt
    confirmed, still_running = _reconcile_pending(client, datasets_dir)
    # 已成功导入的 zip（uploaded.txt）：平台不去重，重跑时跳过
    uploaded_names = _load_uploaded_names(datasets_dir) | confirmed
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
        if plan.zip_path.name in still_running:
            pending_info = _load_pending(datasets_dir).get(plan.zip_path.name) or {}
            job_id = pending_info.get("job_id")
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    job_id=job_id,
                    status="skipped",
                    message=(
                        f"平台任务 {job_id} 仍在导入中（{PENDING_FILE_NAME}），"
                        "本轮不重复上传，稍后重发指令即可"
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
            # 与「自己超时」一样锁入口，避免同日/跨日再次撞上
            blocked[plan.dataset_name] = exc.job_id
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
            if item.job_id:
                # 导入任务未确认完成（超时等）：平台上可能还在跑，锁入口并暂存重试
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
            # 无 job_id 的失败（如超 10GB）重试无意义，保持 failed
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


def _wait_job_not_running(
    client: AthenaClient,
    job_id: Optional[int],
    *,
    attempts: int = 5,
    interval: float = 1.0,
) -> None:
    """stop 之后短轮询，直到任务不再是 status=2（或查不到/次数用尽）。"""
    if not job_id:
        return
    for _ in range(max(1, attempts)):
        try:
            status = client.get_job_status(job_id).get("status")
        except Exception:  # noqa: BLE001 查失败就当已空闲，交给后续 find_running
            return
        if status != 2:
            return
        time.sleep(interval)


def _stop_platform_job(
    client: AthenaClient,
    dataset_name: str,
    dataset_id: Optional[int],
    job_id: Optional[int],
    *,
    own_job: bool = False,
) -> bool:
    """仅终止「本功能管辖的永锋入口 + 本程序自己建的任务」。

    别人会话留下的任务、非永锋-入口一律不动。
    """
    if not job_id or not own_job or not _managed_entry(dataset_name):
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

    - 自己的任务已成功 → 补记成功，不重传
    - 自己的任务仍在跑 → 只对永锋管辖入口 stop，再重传
    - 别人的任务仍在跑 → 不 stop，本轮失败，请用户稍后再发
    """
    retry_items: List[UploadItem] = []
    for pend in deferred:
        item = UploadItem(zip=str(pend.plan.zip_path), dataset_name=pend.plan.dataset_name)
        try:
            if pend.blocking_job_id:
                status = client.get_job_status(pend.blocking_job_id).get("status")
                if status == 3 and pend.own_job:
                    item.status = "uploaded"
                    item.job_id = pend.blocking_job_id
                    item.message = "首轮超时的任务重查确认已导入成功"
                    _record_success(
                        Path(pend.plan.zip_path), pend.plan.dataset_name, pend.blocking_job_id
                    )
                    retry_items.append(item)
                    continue
                if status == 2:
                    if pend.own_job and _managed_entry(pend.plan.dataset_name):
                        # 自己的任务还在跑：停掉管辖入口上的任务，再重传
                        _stop_platform_job(
                            client,
                            pend.plan.dataset_name,
                            None,
                            pend.blocking_job_id,
                            own_job=True,
                        )
                        # stop 后平台状态可能短暂仍为 2，稍等再传，避免立刻 EntryBusy
                        _wait_job_not_running(client, pend.blocking_job_id, attempts=5)
                        # fall through → upload_one_zip
                    else:
                        item.status = "failed"
                        item.job_id = pend.blocking_job_id
                        item.message = (
                            f"重试时平台上任务 {pend.blocking_job_id} 仍在运行，"
                            "本轮不抢入口（未终止他人任务），请稍后重发指令补传"
                        )
                        retry_items.append(item)
                        continue
                # 任务已结束（成功但非 own / 失败）→ 入口空闲，重新上传
            retry_item = upload_one_zip(pend.plan, client)
            if retry_item.status == "uploaded":
                retry_item.message = "重试导入成功"
            else:
                if retry_item.job_id:
                    stopped = _stop_platform_job(
                        client,
                        pend.plan.dataset_name,
                        retry_item.dataset_id,
                        retry_item.job_id,
                        own_job=True,
                    )
                    if stopped:
                        retry_item.message += "，已终止本入口平台任务"
                retry_item.message = f"重试仍失败：{retry_item.message}"
            retry_items.append(retry_item)
        except EntryBusyError as exc:
            # 他人任务占入口：绝不 stop
            item.status = "failed"
            item.message = f"重试仍失败：{exc}（未终止他人任务）"
            retry_items.append(item)
        except Exception as exc:  # noqa: BLE001 重试失败即最终失败
            logger.warning("重试上传 %s 失败: %s", pend.plan.zip_path.name, exc)
            item.status = "failed"
            item.message = f"重试仍失败：{exc}"
            retry_items.append(item)
    return retry_items


def _format_item_line(it: UploadItem) -> str:
    name = Path(it.zip).name
    if it.status == "uploaded":
        return f"- {name} → {it.dataset_name}（任务 {it.job_id}，{it.message or '导入成功'}）"
    if it.status == "skipped":
        return f"- {name} → ⏭️ {it.message}"
    if it.status == "retry_pending":
        return f"- {name} → ⏳ {it.message}"
    return f"- {name} → ❌ {it.message}"


def _merge_final_items(
    main_items: List[UploadItem],
    retry_items: List[UploadItem],
) -> List[UploadItem]:
    """用重试结果覆盖首轮 retry_pending，得到最终状态列表（去重总结）。"""
    by_zip: Dict[str, UploadItem] = {}
    for it in main_items:
        by_zip[it.zip] = it
    for it in retry_items:
        by_zip[it.zip] = it
    return list(by_zip.values())


def _build_upload_result(
    root: Path,
    dates: List[str],
    main_items: List[UploadItem],
    retry_items: List[UploadItem],
    missing_dates: Optional[set] = None,
) -> Dict[str, Any]:
    """汇总最终态：按日 lines + 与旧版 Gradio 一致的总结措辞。"""
    missing_dates = set(missing_dates or ())
    final_items = _merge_final_items(main_items, retry_items)
    uploaded = sum(1 for it in final_items if it.status == "uploaded")
    skipped = sum(1 for it in final_items if it.status == "skipped") + len(missing_dates)
    failed = sum(1 for it in final_items if it.status == "failed")

    by_day: Dict[str, List[UploadItem]] = {}
    for it in final_items:
        path = Path(it.zip)
        day = path.parent.parent.name if path.parent.name == "datasets" else path.parent.name
        by_day.setdefault(day, []).append(it)

    final_day_reports: List[Dict[str, Any]] = []
    lines: List[str] = []
    for date in dates:
        if date in missing_dates:
            day_lines = [f"- ⏭️ 目录 {date}/datasets 不存在，跳过"]
            day_items: List[UploadItem] = []
        else:
            day_items = by_day.get(date) or []
            day_lines = (
                [_format_item_line(it) for it in day_items]
                if day_items
                else [f"- ⏭️ 目录 {date}/datasets 不存在，跳过"]
            )
        final_day_reports.append(
            {
                "day": date,
                "items": [
                    {
                        "zip": it.zip,
                        "dataset_name": it.dataset_name,
                        "dataset_id": it.dataset_id,
                        "job_id": it.job_id,
                        "status": it.status,
                        "message": it.message,
                    }
                    for it in day_items
                ],
                "lines": day_lines,
            }
        )
        lines.append(f"【{date}】")
        lines.extend(day_lines)
    for day, day_items in by_day.items():
        if day in dates:
            continue
        day_lines = [_format_item_line(it) for it in day_items]
        final_day_reports.append(
            {
                "day": day,
                "items": [
                    {
                        "zip": it.zip,
                        "dataset_name": it.dataset_name,
                        "dataset_id": it.dataset_id,
                        "job_id": it.job_id,
                        "status": it.status,
                        "message": it.message,
                    }
                    for it in day_items
                ],
                "lines": day_lines,
            }
        )
        lines.append(f"【{day}】")
        lines.extend(day_lines)

    # 与旧版 Gradio 收尾句一致（工具路径的 summary_text 也用这句）
    lines.append(
        f"全部日期处理完成：成功 {uploaded} 个，跳过 {skipped} 个，失败 {failed} 个。"
    )
    return {
        "output_dir": str(root),
        "dates": dates,
        "days": final_day_reports,
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


def iter_upload_zips_for_dates(
    output_dir: str,
    dates: Optional[List[str]] = None,
    client: Optional[AthenaClient] = None,
):
    """上传编排生成器：每完成一天首轮 yield day 事件，最后 yield done。

    跨日共用入口锁 + 统一重试；供 Gradio 逐日刷聊天，工具路径用
    upload_zips_for_dates 取最终结果即可。
    """
    client = client or AthenaClient()
    root = Path(output_dir).expanduser()
    if not dates:
        dates = _discover_dataset_dates(root)
    if not dates:
        raise YongfengUploadError(
            f"{output_dir} 下没有找到含 datasets/ 的日期目录"
        )
    main_items: List[UploadItem] = []
    missing_dates: set = set()
    blocked_datasets: Dict[str, int] = {}
    deferred: List[PendingRetry] = []
    for date in dates:
        day_dir = root / date
        if not (day_dir / "datasets").is_dir():
            missing_dates.add(date)
            day_report = {
                "day": date,
                "items": [],
                "lines": [f"- ⏭️ 目录 {date}/datasets 不存在，跳过"],
            }
            yield {"type": "day", "day_report": day_report}
            continue
        day_report = upload_day_zips(
            day_dir, client=client, blocked_datasets=blocked_datasets, deferred=deferred
        )
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
        yield {"type": "day", "day_report": day_report}

    retry_items = _retry_deferred(deferred, client) if deferred else []
    result = _build_upload_result(
        root, dates, main_items, retry_items, missing_dates=missing_dates
    )
    yield {"type": "done", "result": result}


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
    result: Optional[Dict[str, Any]] = None
    for event in iter_upload_zips_for_dates(output_dir, dates=dates, client=client):
        if event.get("type") == "done":
            result = event["result"]
    if result is None:
        raise YongfengUploadError("上传未返回结果")
    return result


def format_upload_summary(result: Dict[str, Any]) -> str:
    """把 upload_zips_for_dates 的结果整理成给用户看的文本。"""
    text = result.get("summary_text") or ""
    return text or "（上传结果为空）"
