"""把下载产物 datasets/ 里的 zip 上传到赛信金睛视觉AI开发平台。

平台 http://pre-athena-mv.cisdigital.cn（namespace=1），目标为 6 个料型入口、
废钢分类和废钢杂质。上传链路如下：

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
  6. GET  /api/devel/dataset-jobs/<job_id>/ → status 1 等待 / 2 运行 / 3 成功

命名规则（用户定稿，zip 前缀能对上就行）：
  废钢分类数据集.zip          → 永锋-废钢分类
  废钢杂质数据集.zip          → 永锋废钢杂质分割（固定 id，默认 28392）
  重废1.zip / 重废1_*.zip    → 永锋-重废1
  平均料型 / 平均料型_*.zip   → 平台没有「永锋-平均料型」，跳过并报告
导入只用无标注模式（options 不带 annotation_format）；先传 zip 再由
程序显式建导入任务（手动导入）。单条失败不中断，全部进总结。

防重与监督：
- 平台不去重：每包查询目标入口的图片名，仅导入新增图片
- 提交导入前登记对象存储 key；响应不明时按 key 核对原任务
- 平台报告的导入数与本次提交数量一致，且能查到全部提交图片，才写 uploaded.txt
- MinIO 直传卡死：超过 upload_timeout（默认 5 分钟）终止该条并统一重试
- 等待或运行中的任务保留并重查，避免取消后部分导入造成叠图
- 空 / 损坏 zip、平均料型包在计划阶段跳过
- 登录与 API 对 5xx/网络抖动有限次退避重试
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import zipfile
import zlib
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import requests

from config.settings import YongfengDatasetUploadConfig, settings

logger = logging.getLogger(__name__)

# 本地打包命名（见 scrap_packager.py）
CLASSIFICATION_ZIP_NAME = "废钢分类数据集.zip"
IMPURITY_ZIP_NAME = "废钢杂质数据集.zip"
AVERAGE_PREFIX = "平均料型"
# 有编号的统料必须先于不带编号的统料匹配。
MATERIAL_PREFIXES = ("重废1", "重废2", "重废3", "统料1", "统料2", "统料", AVERAGE_PREFIX)
# 平台导入页限制：单文件最大 10GB
MAX_ZIP_BYTES = 10 * 1024**3
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
# 每个 <日期>/datasets/ 各自一份，供用户核对任务。
RECORD_FILE_NAME = "uploaded.txt"
# 已建导入任务、尚未确认成功的本地登记（防超时后重跑叠图）
PENDING_FILE_NAME = "upload_pending.txt"
UPLOAD_LOG_NAME = "upload_progress.log"
ACTIVE_JOB_STATUSES = {1, 2}  # 平台状态 1 为等待中，不是失败
FAILED_JOB_STATUSES = {4, 5, 6, 7}
UPLOAD_LOCK_PATH = Path.home() / ".cache" / "briefme" / "yongfeng-upload.lock"


def _append_upload_log(root: Path, message: str) -> None:
    """保留上传过程，聊天页面关闭后仍可检查最终结果。"""
    try:
        with (root / UPLOAD_LOG_NAME).open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError as exc:
        logger.warning("写入上传日志失败: %s", exc)


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
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        raise YongfengRecordError(f"写入上传记录 {record} 失败，保留待确认任务: {exc}") from exc
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
            if not line.strip():
                continue
            if len(parts) < 3 or not parts[0]:
                raise YongfengRecordError(f"待确认记录 {record} 格式无效，无法安全重传")
            try:
                job_id = int(parts[2].replace("任务", "").strip())
            except ValueError as exc:
                raise YongfengRecordError(f"待确认记录 {record} 缺少有效任务号，无法安全重传") from exc
            dataset_id = None
            if len(parts) >= 4 and parts[3].isdigit():
                dataset_id = int(parts[3])
            names = None
            if len(parts) >= 8 and parts[7]:
                try:
                    names = json.loads(parts[7])
                except ValueError as exc:
                    raise YongfengRecordError(f"待确认记录 {record} 的图片清单无效") from exc
                if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
                    raise YongfengRecordError(f"待确认记录 {record} 的图片清单无效")
            out[parts[0]] = {
                "dataset_name": parts[1],
                "job_id": job_id,
                "dataset_id": dataset_id,
                "object_key": parts[5] if len(parts) >= 6 else "",
                "image_count": int(parts[6]) if len(parts) >= 7 and parts[6].isdigit() else None,
                "image_names": names,
            }
    except OSError as exc:
        raise YongfengRecordError(f"读取待确认记录 {record} 失败，停止上传以免重复: {exc}") from exc
    return out


def _record_pending(
    zip_path: Path,
    dataset_name: str,
    job_id: int,
    dataset_id: Optional[int],
    object_key: str = "",
    image_count: Optional[int] = None,
    image_names: Optional[List[str]] = None,
) -> None:
    """提交前先登记 key，收到任务号后追加一行，保留原行直到确认完成。"""
    record = zip_path.parent / PENDING_FILE_NAME
    line = "\t".join(
        [
            zip_path.name,
            dataset_name,
            str(job_id),
            str(dataset_id if dataset_id is not None else ""),
            time.strftime("%Y-%m-%d %H:%M:%S"),
            object_key,
            "" if image_count is None else str(image_count),
            "" if image_names is None else json.dumps(image_names, ensure_ascii=False),
        ]
    )
    try:
        with record.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        stage = f"平台任务 {job_id} 已创建" if job_id else "尚未提交导入任务"
        raise YongfengRecordError(
            f"{stage}，无法写入待确认记录 {record}: {exc}"
        ) from exc


def _clear_pending_zip(zip_path: Path) -> None:
    """从 upload_pending.txt 去掉该 zip 的行。"""
    record = zip_path.parent / PENDING_FILE_NAME
    if not record.is_file():
        return
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if not ln.startswith(zip_path.name + "\t")]
        if kept:
            with tempfile.TemporaryDirectory(dir=record.parent, prefix=".upload-ledger-") as work:
                replacement = Path(work) / record.name
                replacement.write_text("\n".join(kept) + "\n", encoding="utf-8")
                replacement.replace(record)
        else:
            record.unlink(missing_ok=True)
    except OSError as exc:
        raise YongfengRecordError(f"清理待确认记录 {record} 失败：{exc}") from exc


def _incomplete_import(result: Dict[str, Any], expected: Optional[int]) -> str:
    """返回无法确认全量导入的原因。"""
    info = result.get("info")
    if not isinstance(info, dict):
        return "平台未返回导入数量，无法确认全部图片"
    success, total = info.get("success"), info.get("total")
    if type(success) is not int or type(total) is not int or success < 0 or total < 0:
        return "平台未返回有效的导入数量，无法确认全部图片"
    planned = total if expected is None else expected
    if success < max(total, planned):
        return f"部分导入，成功 {success}/{max(total, planned)} 张"
    if not (success == total == planned and success > 0):
        return f"导入数量不一致，平台报告 {success}/{total} 张，提交 {planned} 张"
    return ""


def _already_imported_images(result: Dict[str, Any]) -> Optional[int]:
    info = result.get("info")
    success = info.get("success") if isinstance(info, dict) else None
    return success if type(success) is int and success >= 0 else None


def _platform_image_names(
    client: AthenaClient, dataset_id: int, zip_path: Path, names: List[str],
) -> set[str]:
    date = zip_path.parent.parent.name.replace("-", "")
    # 非标准图片名用完整列表核对，避免日期筛选把已上传的图过滤掉。
    if not (len(date) == 8 and date.isdigit() and all(date in name for name in names)):
        date = ""
    return client.list_dataset_image_names(dataset_id, date)


def _settle_pending(
    client: AthenaClient, zip_path: Path, info: Dict[str, Any], result: Dict[str, Any],
) -> str:
    job_id = info["job_id"]
    status = result.get("status")
    if status != 3 and status not in FAILED_JOB_STATUSES:
        return f"平台任务 {job_id} 仍在运行或状态暂时无法确认，本轮未重复上传"

    expected = info["image_count"]
    problem = _incomplete_import(result, expected) if status == 3 else "导入失败"
    imported = _already_imported_images(result)
    if imported is None or (expected is not None and imported > expected):
        return f"平台任务 {job_id} {problem}，导入张数无法确认"

    if imported:
        names = info["image_names"]
        if names is None:
            # 旧记录没有已提交的图片清单，只能恢复可核对的全量导入。
            if problem or not zip_path.is_file():
                return f"平台任务 {job_id} {problem or '已完成'}，旧记录缺少提交图片清单"
            with zipfile.ZipFile(zip_path) as archive:
                names = list(_image_members(archive))
        dataset_id = info["dataset_id"] or _dataset_id_for_name(client, info["dataset_name"])
        if not dataset_id or len(set(names) & _platform_image_names(client, dataset_id, zip_path, names)) < imported:
            return f"平台任务 {job_id} {problem or '已完成'}，已导入的图片尚未全部查到"

    if problem:
        _clear_pending_zip(zip_path)
    else:
        _record_success(zip_path, info["dataset_name"], job_id)
    return ""


def _reconcile_pending(
    client: AthenaClient,
    datasets_dir: Path,
) -> Dict[str, str]:
    """对照平台重查上一轮未确认的任务。

    完成后补记成功，返回各未完成包的原因。
    """
    pending = _load_pending(datasets_dir)
    unresolved: Dict[str, str] = {}
    for zip_name, info in pending.items():
        job_id = info["job_id"]
        zip_path = datasets_dir / zip_name
        try:
            if not job_id:
                job_id = client.find_import_job_by_key(info["dataset_id"], info["object_key"])
                if not job_id:
                    unresolved[zip_name] = "创建导入任务的结果仍待确认，本轮未重新创建任务"
                    continue
                _record_pending(
                    zip_path, info["dataset_name"], job_id, info["dataset_id"],
                    info["object_key"], info["image_count"], info["image_names"],
                )
                info["job_id"] = job_id
            result = client.get_job_status(job_id)
            reason = _settle_pending(client, zip_path, info, result)
            if reason:
                unresolved[zip_name] = reason
        except YongfengRecordError:
            raise
        except Exception as exc:  # noqa: BLE001 查不到就留着下次再问
            logger.warning("重查待确认任务 %s 失败: %s", job_id, exc)
            unresolved[zip_name] = f"任务状态暂时无法确认，本轮未重新上传：{exc}"
    return unresolved


class YongfengUploadError(Exception):
    """永锋数据集上传链路错误。"""


class YongfengRecordError(YongfengUploadError):
    """无法可靠保存上传记录，不得自动重传。"""


class ImportCreationUnconfirmed(YongfengUploadError):
    """平台可能已经接收创建请求。"""


class EntryBusyError(YongfengUploadError):
    """平台该数据集入口还有导入任务在跑（可能是别的会话留下的）。

    同一入口并发上传/导入会中断之前的，先暂存到最后统一重试。
    """

    def __init__(self, job_id: int):
        super().__init__(f"平台任务 {job_id} 仍在运行，不抢入口")
        self.job_id = int(job_id)


@contextmanager
def _upload_lock():
    """在同一账户下串行化上传，覆盖不同保存目录和 BriefMe 进程。"""
    UPLOAD_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with UPLOAD_LOCK_PATH.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise YongfengUploadError("另一批永锋上传正在运行，请完成后再发起上传") from exc
        yield


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
    recheck_job: bool = False


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
                lines.append(f"- {name} → {it.dataset_name}（任务 {it.job_id}，{it.message}）")
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
    """按 ZIP 开头的料型选择数据集，忽略其余文件名。"""
    name = zip_name.strip()
    if name == CLASSIFICATION_ZIP_NAME:
        return "废钢分类"
    if not name.endswith(".zip"):
        return None
    stem = _normalize_prefix(name[:-4])
    return next((prefix for prefix in MATERIAL_PREFIXES if stem.startswith(prefix)), None)


def _image_members(archive: zipfile.ZipFile) -> Dict[str, zipfile.ZipInfo]:
    images = {}
    for member in archive.infolist():
        name = PurePosixPath(member.filename)
        if member.is_dir() or not member.file_size or name.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if name.name in images:
            raise YongfengUploadError(f"压缩包含有同名图片 {name.name}，无法安全导入")
        images[name.name] = member
    return images


def _zip_skip_reason(zip_path: Path) -> Optional[str]:
    """空文件或损坏/非 zip → 跳过原因；可用则返回 None。"""
    try:
        size = zip_path.stat().st_size
    except OSError:
        return "无法读取文件，跳过"
    if size <= 0:
        return "空 zip（0 字节），跳过"
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if not _image_members(archive):
                return "压缩包没有可导入的图片，跳过"
            # 能读取目录不代表图像数据完整；验证 CRC 后再占用料型名额。
            if archive.testzip() is not None:
                return "压缩包内容损坏（CRC 校验失败），跳过"
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error):
        return "不是合法 zip（损坏或无法读取），跳过"
    except YongfengUploadError as exc:
        return str(exc)
    return None


def plan_day_zips(datasets_dir: Path) -> List[ZipPlan]:
    """扫描 <日期>/datasets/ 下的 zip，生成上传计划。

    同料型的多个包内容相同，只选一份。有损坏或空包时选可用的包。
    废钢杂质数据集.zip 单独映射到平台「永锋废钢杂质分割」。
    """
    if not datasets_dir.is_dir():
        return []
    plans: List[ZipPlan] = []
    selected_names = set()
    for zip_path in sorted(datasets_dir.glob("*.zip")):
        if zip_path.name == IMPURITY_ZIP_NAME:
            dataset_name = settings.yongfeng_upload.impurity_dataset_name
        else:
            prefix = parse_zip_prefix(zip_path.name)
            if prefix is None:
                plans.append(ZipPlan(zip_path, skip=True, reason="不是 datasets 打包命名，未上传"))
                continue
            dataset_name = f"{settings.yongfeng_upload.dataset_name_prefix}{prefix}"
            if prefix == AVERAGE_PREFIX:
                plans.append(ZipPlan(zip_path, dataset_name, skip=True, reason="平台没有平均料型数据集，跳过"))
                continue
        reason = "同料型已选另一份压缩包" if dataset_name in selected_names else _zip_skip_reason(zip_path)
        plans.append(ZipPlan(zip_path, dataset_name, skip=bool(reason), reason=reason or ""))
        if not reason:
            selected_names.add(dataset_name)
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

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        *,
        retry_network: bool = True,
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
                if not isinstance(body, dict):
                    raise ValueError(f"平台接口 {path} 返回格式错误")
                if body.get("code") != 0:
                    raise YongfengUploadError(
                        f"平台接口 {path} 返回异常: {body.get('message')}"
                    )
                return body
            except requests.RequestException as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if not retry_network and (status is None or status == 408 or status >= 500):
                    raise ImportCreationUnconfirmed(str(exc)) from exc
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

    def _iter_list_items(self, path: str, params: Dict[str, Any]):
        page_size = 100
        seen_ids = set()
        for page in range(1, 1001):
            body = self._get(
                path, params={**params, "namespace": self.cfg.namespace, "page": page, "page_size": page_size}
            )
            rows = body.get("data")
            if not isinstance(rows, list):
                raise YongfengUploadError(f"平台接口 {path} 的列表格式有误")
            for row in rows:
                row_id = row.get("id") if isinstance(row, dict) else None
                if not row_id or row_id in seen_ids:
                    raise YongfengUploadError(f"平台接口 {path} 分页数据不完整或重复")
                seen_ids.add(row_id)
                yield row
            if len(rows) < page_size:
                return
        raise YongfengUploadError(f"平台接口 {path} 列表过长，无法确认完整结果")

    def list_dataset_image_names(self, dataset_id: int, date: str) -> set[str]:
        """用平台已有的日期片段搜索，返回该数据集中的实际文件名。"""
        names = set()
        params = {"search": date.replace("-", "")} if date else {}
        for row in self._iter_list_items(
            f"devel/datasets/{dataset_id}/images/", params
        ):
            name = row.get("name")
            if not isinstance(name, str) or not name:
                raise YongfengUploadError("平台图片记录缺少文件名，无法安全补传")
            names.add(name)
        return names

    def find_import_job_by_key(self, dataset_id: int, key: str) -> Optional[int]:
        """按本次 MinIO 上传的唯一 key 找任务，避免选中同数据集里的其他任务。"""
        if not dataset_id or not key:
            return None
        for row in self._iter_list_items(
            "devel/dataset-jobs/", {"dataset": dataset_id, "type": 1}
        ):
            source = (row.get("config") or {}).get("source") or {}
            if any(f.get("key") == key for f in source.get("files") or []):
                return int(row["id"])
        return None

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
        for status in sorted(ACTIVE_JOB_STATUSES):
            body = self._get(
                "devel/dataset-jobs/",
                params={
                    "namespace": self.cfg.namespace,
                    "page": 1,
                    "page_size": 20,
                    "dataset": dataset_id,
                    "type": 1,
                    "status": status,
                },
            )
            items = body.get("data") or []
            for it in items:
                if isinstance(it, dict) and it.get("status") == status and it.get("id"):
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

    def create_import_job(self, dataset_id: int, key: str) -> int:
        """建数据集导入任务（无标注模式：options 只有 mode=1）。"""
        payload = {
            "dataset": dataset_id,
            "type": 1,
            "namespace": self.cfg.namespace,
            "config": {
                "source": {"files": [{"key": key}]},
                "options": {"mode": 1},
            },
        }
        try:
            body = self._request("post", "devel/dataset-jobs/", payload=payload, retry_network=False)
            data = body.get("data")
            job_id = int(data.get("id") or 0) if isinstance(data, dict) else 0
            if job_id <= 0:
                raise ImportCreationUnconfirmed("平台响应缺少有效任务号")
            return job_id
        except (ImportCreationUnconfirmed, ValueError, TypeError) as exc:
            logger.warning("创建导入任务的结果待确认（%s），按 ZIP 的 key 查原任务", exc)
            for attempt in range(3):
                try:
                    job_id = self.find_import_job_by_key(dataset_id, key)
                except Exception as lookup_error:
                    logger.warning("查询原导入任务失败: %s", lookup_error)
                    break
                if job_id:
                    logger.info("已找回导入任务 %s，本次沿用原任务", job_id)
                    return job_id
                if attempt < 2:
                    time.sleep(min(1, self.cfg.job_poll_interval))
            raise ImportCreationUnconfirmed(
                f"创建导入任务的结果待确认（{exc}），已保留记录，本轮未重复提交"
            ) from exc

    def get_job_status(self, job_id: int) -> Dict[str, Any]:
        body = self._get(f"devel/dataset-jobs/{job_id}/")
        data = body.get("data") or {}
        return {
            "status": data.get("status"),
            "duration": data.get("duration"),
            "info": data.get("info"),
        }

    def wait_job(self, job_id: int) -> Dict[str, Any]:
        """轮询导入任务直到结束：1 等待，2 运行，3 成功。"""
        last: Dict[str, Any] = {}
        for _ in range(self.cfg.job_poll_max_attempts):
            last = self.get_job_status(job_id)
            status = last.get("status")
            if status == 3:
                return last
            if status in FAILED_JOB_STATUSES:
                raise YongfengUploadError(
                    f"导入任务 {job_id} 失败（status={status}，info={last.get('info')}）"
                )
            time.sleep(self.cfg.job_poll_interval)
        raise YongfengUploadError(f"导入任务 {job_id} 等待超时（>{self.cfg.job_poll_max_attempts} 次轮询）")


def _dataset_id_for_name(client: AthenaClient, dataset_name: str) -> Optional[int]:
    cfg = settings.yongfeng_upload
    # 杂质入口平台名不含「永锋-」前缀，用配置的固定 id，避免搜名失败
    if dataset_name == cfg.impurity_dataset_name and cfg.impurity_dataset_id:
        return cfg.impurity_dataset_id
    return client.search_dataset_id(dataset_name)


def upload_one_zip(plan: ZipPlan, client: AthenaClient) -> UploadItem:
    """核对目标入口，只提交尚未存在的图片，等待导入完成。"""
    item = UploadItem(zip=str(plan.zip_path), dataset_name=plan.dataset_name)
    zip_path = plan.zip_path
    dataset_id = _dataset_id_for_name(client, plan.dataset_name)
    if dataset_id is None:
        item.status = "failed"
        item.message = f"平台上没有数据集「{plan.dataset_name}」（按名称搜索无精确匹配），未上传"
        return item
    item.dataset_id = dataset_id
    running_job = client.find_running_import_job(dataset_id)
    if running_job:
        # 平台同入口一次只能导入一个包：别的会话/上一轮留下的任务
        # 还在跑时先不抢入口，交给统一重试轮处理。
        raise EntryBusyError(running_job)

    with zipfile.ZipFile(zip_path) as archive, ExitStack() as work:
        images = _image_members(archive)
        if not images:
            item.status = "failed"
            item.message = "压缩包没有可导入的图片"
            return item
        existing = _platform_image_names(client, dataset_id, zip_path, list(images))
        missing = {name: entry for name, entry in images.items() if name not in existing}
        if not missing:
            item.status = "skipped"
            item.message = "已成功导入过，平台已有包内全部图片"
            return item

        upload_path = zip_path
        if len(missing) < len(archive.infolist()):
            folder = work.enter_context(tempfile.TemporaryDirectory(dir=zip_path.parent, prefix=".upload-"))
            upload_path = Path(folder) / zip_path.name
            with zipfile.ZipFile(upload_path, "w", compression=zipfile.ZIP_DEFLATED) as delta:
                for member in missing.values():
                    with archive.open(member) as src, delta.open(member.filename, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst)
            logger.info("%s 已有 %s 张，仅补传 %s 张", zip_path.name, len(images) - len(missing), len(missing))

        size = upload_path.stat().st_size
        if size > MAX_ZIP_BYTES:
            item.status = "failed"
            item.message = f"zip {size / 1024**3:.1f}GB 超过平台 10GB 上限"
            return item
        signature = client.get_upload_signature(dataset_id)
        key = signature["key"]
        client.put_zip_to_minio(signature, upload_path)

    count = len(missing)
    names = list(missing)
    # ZIP 直传可能耗时数分钟，再查一次平台，防止期间有其他导入。
    if missing.keys() & _platform_image_names(client, dataset_id, zip_path, names):
        raise YongfengUploadError("直传期间目标入口新增了包内图片，重新核对后补传")
    running_job = client.find_running_import_job(dataset_id)
    if running_job:
        raise EntryBusyError(running_job)
    # 请求结果可能丢失。先记下对象 key，即使进程中断也能查同一个任务。
    _record_pending(zip_path, plan.dataset_name, 0, dataset_id, key, count, names)
    try:
        job_id = client.create_import_job(dataset_id, key)
    except ImportCreationUnconfirmed as exc:
        item.status = "failed"
        item.message = str(exc)
        return item
    except (requests.HTTPError, YongfengUploadError):
        _clear_pending_zip(zip_path)
        raise
    except Exception as exc:  # 响应未知时保留 key，并且不重新提交
        item.status = "failed"
        item.message = f"创建导入任务的结果待确认，本轮未重复提交：{exc}"
        return item
    item.job_id = job_id
    _record_pending(zip_path, plan.dataset_name, job_id, dataset_id, key, count, names)
    try:
        result = client.wait_job(job_id)
    except Exception as exc:  # noqa: BLE001 导入未确认完成，保留 pending + job_id
        item.status = "failed"
        item.message = str(exc)
        item.recheck_job = True
        return item

    problem = _incomplete_import(result, count)
    if problem:
        item.status = "failed"
        item.message = f"平台任务 {job_id} {problem}，保留待确认记录"
        return item
    try:
        visible = _platform_image_names(client, dataset_id, zip_path, names)
    except Exception as exc:  # 查询失败不能清除已提交任务的记录。
        item.status = "failed"
        item.message = f"任务 {job_id} 已结束，图片核对失败，保留待确认记录：{exc}"
        return item
    if not missing.keys() <= visible:
        item.status = "failed"
        item.message = f"任务 {job_id} 已结束，平台尚未查到全部提交图片，保留待确认记录"
        return item
    item.status = "uploaded"
    item.message = f"导入成功，本次 {count} 张"
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
    unresolved = _reconcile_pending(client, datasets_dir)
    pending = _load_pending(datasets_dir) if unresolved else {}
    pending_by_dataset = {
        row["dataset_name"]: name for name, row in pending.items() if name in unresolved
    }
    for dataset_name in pending_by_dataset:
        blocked.setdefault(dataset_name, 0)
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
        pending_name = plan.zip_path.name
        if pending_name not in unresolved:
            pending_name = pending_by_dataset.get(plan.dataset_name)
        if pending_name in unresolved:
            pending_info = pending.get(pending_name) or {}
            job_id = pending_info.get("job_id")
            items.append(
                UploadItem(
                    zip=str(plan.zip_path),
                    dataset_name=plan.dataset_name,
                    job_id=job_id or None,
                    status="failed",
                    message=unresolved[pending_name],
                )
            )
            continue
        if blocked.get(plan.dataset_name) == 0:
            items.append(UploadItem(
                zip=str(plan.zip_path), dataset_name=plan.dataset_name, status="failed",
                message="同入口的导入结果仍待确认，本轮未重新提交",
            ))
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
        except YongfengRecordError as exc:
            blocked[plan.dataset_name] = 0
            items.append(UploadItem(zip=str(plan.zip_path), dataset_name=plan.dataset_name,
                                    status="failed", message=str(exc)))
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
            if item.recheck_job and item.job_id:
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
            elif plan.zip_path.name in _load_pending(datasets_dir):
                blocked[plan.dataset_name] = 0
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


def _retry_deferred(
    deferred: List[PendingRetry],
    client: AthenaClient,
    uncertain_targets: set[str],
) -> List[UploadItem]:
    """最后统一重试首轮没传上的 zip（每条只重试一次）。

    - 自己的任务已成功 → 补记成功，不重传
    - 任务仍在等待或运行 → 不取消、不重传，请用户稍后重查
    """
    retry_items: List[UploadItem] = []
    for pend in deferred:
        item = UploadItem(zip=str(pend.plan.zip_path), dataset_name=pend.plan.dataset_name)
        try:
            if item.dataset_name in uncertain_targets:
                item.status = "failed"
                item.message = "同入口的导入结果仍待确认，本轮未重新提交"
                retry_items.append(item)
                continue
            if pend.blocking_job_id:
                result = client.get_job_status(pend.blocking_job_id)
                status = result.get("status")
                if status == 3 and pend.own_job:
                    item.job_id = pend.blocking_job_id
                    pending = _load_pending(pend.plan.zip_path.parent).get(pend.plan.zip_path.name) or {}
                    problem = _incomplete_import(result, pending.get("image_count"))
                    if not problem:
                        problem = (
                            _settle_pending(client, pend.plan.zip_path, pending, result)
                            if pending else "本地待确认记录缺失"
                        )
                    if problem:
                        item.status = "failed"
                        item.message = f"平台任务 {item.job_id} {problem}，保留待确认记录"
                        uncertain_targets.add(item.dataset_name)
                    else:
                        item.status = "uploaded"
                        item.message = "首轮超时的任务重查确认已导入成功"
                    retry_items.append(item)
                    continue
                if status in ACTIVE_JOB_STATUSES:
                    item.status = "failed"
                    item.job_id = pend.blocking_job_id
                    item.message = (
                        f"重试时平台上任务 {pend.blocking_job_id} 仍在运行或等待，"
                        "本轮不抢入口（未终止他人任务或本任务），请稍后重发指令核对"
                    )
                    retry_items.append(item)
                    continue
                elif status != 3 and status not in FAILED_JOB_STATUSES:
                    item.status = "failed"
                    item.job_id = pend.blocking_job_id
                    item.message = f"平台任务 {pend.blocking_job_id} 状态 {status} 无法确认，未重传"
                    retry_items.append(item)
                    continue
                if pend.own_job and status in FAILED_JOB_STATUSES:
                    imported = _already_imported_images(result)
                    if imported != 0:
                        item.status = "failed"
                        item.job_id = pend.blocking_job_id
                        detail = f"已部分导入 {imported} 张" if imported else "导入张数无法确认"
                        item.message = f"平台任务 {item.job_id} 失败，{detail}，本轮未重传"
                        uncertain_targets.add(item.dataset_name)
                        retry_items.append(item)
                        continue
                    _clear_pending_zip(pend.plan.zip_path)
            retry_item = upload_one_zip(pend.plan, client)
            if retry_item.status == "uploaded":
                retry_item.message = "重试导入成功"
            elif retry_item.status != "skipped":
                retry_item.message = f"重试仍失败：{retry_item.message}"
            retry_items.append(retry_item)
            if pend.plan.zip_path.name in _load_pending(pend.plan.zip_path.parent):
                uncertain_targets.add(item.dataset_name)
        except EntryBusyError as exc:
            # 他人任务占入口：绝不 stop
            item.status = "failed"
            item.message = f"重试仍失败：{exc}（未终止他人任务）"
            retry_items.append(item)
        except Exception as exc:  # noqa: BLE001 重试失败即最终失败
            logger.warning("重试上传 %s 失败: %s", pend.plan.zip_path.name, exc)
            item.status = "failed"
            item.message = f"重试仍失败：{exc}"
            uncertain_targets.add(item.dataset_name)
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
    missing_dates: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """汇总最终态：按日 lines + 与旧版 Gradio 一致的总结措辞。"""
    missing_dates = missing_dates or {}
    final_items = _merge_final_items(main_items, retry_items)
    uploaded = sum(1 for it in final_items if it.status == "uploaded")
    skipped = sum(1 for it in final_items if it.status == "skipped")
    failed = sum(1 for it in final_items if it.status == "failed") + len(missing_dates)

    by_day: Dict[str, List[UploadItem]] = {}
    for it in final_items:
        path = Path(it.zip)
        day = path.parent.parent.name if path.parent.name == "datasets" else path.parent.name
        by_day.setdefault(day, []).append(it)

    final_day_reports: List[Dict[str, Any]] = []
    lines: List[str] = []
    for date in dates:
        if date in missing_dates:
            day_lines = [f"- ❌ 目录 {date}/datasets {missing_dates[date]}，未上传"]
            day_items: List[UploadItem] = []
        else:
            day_items = by_day.get(date) or []
            day_lines = (
                [_format_item_line(it) for it in day_items]
                if day_items
                else [f"- ❌ 目录 {date}/datasets 没有可处理的压缩包"]
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
    with _upload_lock():
        yield from _iter_upload_zips_for_dates(output_dir, dates, client or AthenaClient())


def _iter_upload_zips_for_dates(
    output_dir: str, dates: Optional[List[str]], client: AthenaClient,
):
    root = Path(output_dir).expanduser()
    if not dates:
        dates = _discover_dataset_dates(root)
    if not dates:
        raise YongfengUploadError(
            f"{output_dir} 下没有找到含 datasets/ 的日期目录"
        )
    _append_upload_log(root, f"开始上传：{', '.join(dates)}")
    main_items: List[UploadItem] = []
    missing_dates: Dict[str, str] = {}
    blocked_datasets: Dict[str, int] = {}
    deferred: List[PendingRetry] = []
    for date in dates:
        day_dir = root / date
        datasets_dir = day_dir / "datasets"
        if not datasets_dir.is_dir() or not any(datasets_dir.glob("*.zip")):
            reason = "不存在" if not datasets_dir.is_dir() else "没有压缩包"
            missing_dates[date] = reason
            day_report = {
                "day": date,
                "items": [],
                "lines": [f"- ❌ 目录 {date}/datasets {reason}，未上传"],
            }
            _append_upload_log(root, f"【{date}】\n" + "\n".join(day_report["lines"]))
            yield {"type": "day", "day_report": day_report}
            continue
        day_report = upload_day_zips(
            day_dir, client=client, blocked_datasets=blocked_datasets, deferred=deferred
        )
        _append_upload_log(root, f"【{date}】首轮\n" + "\n".join(day_report["lines"]))
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

    uncertain_targets = {name for name, job_id in blocked_datasets.items() if not job_id}
    retry_items = _retry_deferred(deferred, client, uncertain_targets) if deferred else []
    result = _build_upload_result(
        root, dates, main_items, retry_items, missing_dates=missing_dates
    )
    _append_upload_log(root, "最终结果\n" + result["summary_text"])
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
