"""永锋数据集上传（金睛平台）命名映射 / 上传计划 / 链路编排 单测（不连网）。

运行：python tests/test_yongfeng_upload.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.image_download_route import is_yongfeng_dataset_upload
from agent.yongfeng.scrap_uploader import (
    CLASSIFICATION_ZIP_NAME,
    IMPURITY_ZIP_NAME,
    YongfengUploadError,
    AthenaClient,
    upload_one_zip,
    upload_zips_for_dates,
    parse_zip_prefix,
    plan_day_zips,
)
from config.settings import settings


@pytest.fixture(autouse=True)
def _isolate_upload_lock(tmp_path, monkeypatch):
    from agent.yongfeng import scrap_uploader

    monkeypatch.setattr(scrap_uploader, "UPLOAD_LOCK_PATH", tmp_path / "upload.lock")


def _mkzip(directory: Path, name: str, *images: str) -> Path:
    import zipfile

    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    with zipfile.ZipFile(p, "w") as archive:
        for image in images or ("test.jpg",):
            archive.writestr(f"images/{image}", b"synthetic image")
    return p


def test_parse_zip_prefix():
    assert parse_zip_prefix("废钢分类数据集.zip") == "废钢分类"
    # 杂质包由 plan_day_zips 单独映射，不走「永锋-」前缀
    assert parse_zip_prefix("废钢杂质数据集.zip") is None
    assert parse_zip_prefix("重废1_实例分割数据集.zip") == "重废1"
    assert parse_zip_prefix("重废1_边缘分割数据集.zip") == "重废1"
    assert parse_zip_prefix("重废1.zip") == "重废1"
    assert parse_zip_prefix("重废1_其他名称.zip") == "重废1"
    assert parse_zip_prefix("重废2补下的原图.zip") == "重废2"
    assert parse_zip_prefix("统料_实例分割数据集.zip") == "统料"
    assert parse_zip_prefix("统料1厚剪_边缘分割数据集.zip") == "统料1"
    # 本地打包用全角罗马数字，平台数据集名用半角数字
    assert parse_zip_prefix("统料Ⅰ_实例分割数据集.zip") == "统料1"
    assert parse_zip_prefix("重废Ⅱ_边缘分割数据集.zip") == "重废2"
    assert parse_zip_prefix("统料Ⅱ_实例分割数据集.zip") == "统料2"
    assert parse_zip_prefix("重废Ⅲ_实例分割数据集.zip") == "重废3"
    assert parse_zip_prefix("平均料型_实例分割数据集.zip") == "平均料型"
    assert parse_zip_prefix("别的文件.zip") is None
    assert parse_zip_prefix("车次图片.zip") is None
    print("parse_zip_prefix OK")


def test_plan_day_zips_routes_by_material_prefix():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "2026-09-01" / "datasets"
        _mkzip(datasets, "重废1_01边缘分割数据集.zip")
        _mkzip(datasets, "重废1_99实例分割数据集.zip")
        _mkzip(datasets, "废钢分类数据集.zip")
        _mkzip(datasets, "废钢杂质数据集.zip")
        _mkzip(datasets, "平均料型_实例分割数据集.zip")
        plans = plan_day_zips(datasets)
        by_name = {p.zip_path.name: p for p in plans}
        assert len(plans) == 5
        first = by_name["重废1_01边缘分割数据集.zip"]
        alternate = by_name["重废1_99实例分割数据集.zip"]
        assert first.dataset_name == alternate.dataset_name == "永锋-重废1"
        assert not first.skip
        assert alternate.skip and "同料型" in alternate.reason
        # 分类固定映射
        cls = by_name[CLASSIFICATION_ZIP_NAME]
        assert not cls.skip and cls.dataset_name == "永锋-废钢分类"
        # 杂质：固定平台入口（不含「永锋-」前缀）
        impurity = by_name[IMPURITY_ZIP_NAME]
        assert not impurity.skip
        assert impurity.dataset_name == settings.yongfeng_upload.impurity_dataset_name
        # 平均料型：平台没有对应入口，计划阶段直接跳过
        avg = by_name["平均料型_实例分割数据集.zip"]
        assert avg.skip and "平均料型" in avg.reason
    print("plan_day_zips material prefix OK")


def test_plan_day_zips_accepts_unfamiliar_suffix():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "重废Ⅱ_补下的原图.zip")
        plans = plan_day_zips(datasets)
        assert len(plans) == 1 and not plans[0].skip
        assert plans[0].dataset_name == "永锋-重废2"
    print("plan_day_zips unfamiliar suffix OK")


def test_plan_day_zips_ignores_non_zip_and_unknown_names():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "重废3_实例分割数据集.zip")
        (datasets / "notes.txt").write_text("x")
        _mkzip(datasets, "乱命名.zip")
        plans = plan_day_zips(datasets)
        assert len(plans) == 2
        unknown = [p for p in plans if p.zip_path.name == "乱命名.zip"][0]
        assert unknown.skip
        chosen = [p for p in plans if p.zip_path.name == "重废3_实例分割数据集.zip"][0]
        assert not chosen.skip and chosen.dataset_name == "永锋-重废3"
    print("plan_day_zips ignore non-zip OK")


def test_plan_day_zips_skips_empty_and_corrupt():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        datasets.mkdir(parents=True)
        empty = datasets / "统料_实例分割数据集.zip"
        empty.write_bytes(b"")
        corrupt = datasets / "重废1_实例分割数据集.zip"
        corrupt.write_bytes(b"not-a-zip")
        ok = _mkzip(datasets, "废钢分类数据集.zip")
        plans = plan_day_zips(datasets)
        by_name = {p.zip_path.name: p for p in plans}
        assert by_name[empty.name].skip and "空 zip" in by_name[empty.name].reason
        assert by_name[corrupt.name].skip and "合法 zip" in by_name[corrupt.name].reason
        assert not by_name[ok.name].skip
    print("plan_day_zips skip empty/corrupt OK")


def test_plan_uses_next_valid_zip_when_first_material_zip_has_no_images(tmp_path):
    from zipfile import ZipFile

    datasets = tmp_path / "datasets"
    datasets.mkdir()
    empty = datasets / "重废2_01.zip"
    with ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "no images")
    valid = _mkzip(datasets, "重废Ⅱ_02.zip")
    plans = {plan.zip_path: plan for plan in plan_day_zips(datasets)}

    assert plans[empty].skip
    assert not plans[valid].skip
    assert plans[valid].dataset_name == "永锋-重废2"


def test_plan_uses_next_zip_when_first_material_zip_has_corrupt_payload(tmp_path):
    from zipfile import ZipFile

    damaged = _mkzip(tmp_path, "重废2_01.zip", "same.jpg")
    valid = _mkzip(tmp_path, "重废Ⅱ_02.zip", "same.jpg")
    data = damaged.read_bytes()
    damaged.write_bytes(data.replace(b"synthetic image", b"corrupted image", 1))
    with ZipFile(damaged) as archive:
        assert archive.namelist() == ["images/same.jpg"]
        assert archive.testzip() == "images/same.jpg"

    plans = {plan.zip_path: plan for plan in plan_day_zips(tmp_path)}
    assert plans[damaged].skip and "CRC 校验失败" in plans[damaged].reason
    assert not plans[valid].skip and plans[valid].dataset_name == "永锋-重废2"


class _FakeClient(AthenaClient):
    """按脚本回放的假客户端（不发任何网络请求）。"""

    def __init__(
        self, dataset_id: Optional[int] = 42, statuses: List[int] = None,
        *, dataset_ids: Optional[dict[str, int]] = None,
    ):
        from dataclasses import replace
        from config.settings import settings as _settings

        self.cfg = replace(_settings.yongfeng_upload, job_poll_interval=0)
        self.dataset_id = dataset_id
        self.dataset_ids = dataset_ids or {}
        self.statuses = list(statuses or [2, 3])
        self.calls: List[str] = []
        self.job_payload: Dict[str, Any] = {}
        self.put_args: tuple = ()
        self.staged_images: set[str] = set()
        self.imported_images: Dict[int, set[str]] = {}
        self.submitted_jobs: dict[int, tuple[int, set[str]]] = {}

    def search_dataset_id(self, dataset_name: str) -> Optional[int]:
        self.calls.append(f"search:{dataset_name}")
        return self.dataset_ids.get(dataset_name, self.dataset_id)

    def find_running_import_job(self, dataset_id: int) -> Optional[int]:
        self.calls.append(f"busy:{dataset_id}")
        return None

    def stop_job(self, dataset_id: int, job_id: int) -> None:
        self.calls.append(f"stop:{dataset_id}:{job_id}")
        self.statuses.clear()

    def get_upload_signature(self, dataset_id: int) -> Dict[str, str]:
        self.calls.append(f"signature:{dataset_id}")
        return {
            "endpoint": "http://fss.example",
            "access_key": "ak",
            "secret_key": "sk",
            "session_token": "st",
            "bucket": "athena-pre",
            "key": "tmp/uploads/datasets/abc123",
            "region": "local",
        }

    def put_zip_to_minio(self, signature: Dict[str, str], zip_path: Path) -> None:
        import zipfile

        self.calls.append("put")
        self.put_args = (signature["bucket"], signature["key"], Path(zip_path).name)
        with zipfile.ZipFile(zip_path) as archive:
            self.staged_images = {Path(name).name for name in archive.namelist()}

    def create_import_job(self, dataset_id: int, key: str) -> int:
        self.calls.append(f"job:{dataset_id}:{key}")
        self.job_payload = {
            "dataset": dataset_id,
            "config": {
                "source": {"files": [{"key": key}]},
                "options": {"mode": 1},
            },
        }
        job_id = 1001 + len(self.submitted_jobs)
        self.submitted_jobs[job_id] = (dataset_id, self.staged_images.copy())
        return job_id

    def get_job_status(self, job_id: int) -> Dict[str, Any]:
        status = self.statuses.pop(0) if self.statuses else 3
        submitted = self.submitted_jobs.get(job_id)
        count = 1
        if submitted:
            dataset_id, images = submitted
            count = len(images)
            if status == 3:
                self.imported_images.setdefault(dataset_id, set()).update(images)
        return {"status": status, "duration": 1, "info": {"total": count, "success": count}}

    def list_dataset_image_names(self, dataset_id: int, date: str) -> set[str]:
        self.calls.append(f"list:{dataset_id}:{date}")
        return self.imported_images.get(dataset_id, set())


def test_upload_one_zip_success_flow():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        z = _mkzip(datasets, "重废1_实例分割数据集.zip")
        plan = plan_day_zips(datasets)[0]
        client = _FakeClient(dataset_id=42, statuses=[2, 3])
        item = upload_one_zip(plan, client)
        assert item.status == "uploaded"
        assert item.dataset_id == 42
        assert item.job_id == 1001
        assert client.calls[0] == "search:永锋-重废1"
        assert client.calls.index("busy:42") < client.calls.index("signature:42")
        assert next(i for i, c in enumerate(client.calls) if c.startswith("list:42:")) < client.calls.index("put")
        assert client.calls.index("put") < client.calls.index("job:42:tmp/uploads/datasets/abc123")
        # 无标注模式：options 里不允许出现 annotation_format
        assert client.job_payload["config"]["options"] == {"mode": 1}
        assert "annotation_format" not in client.job_payload["config"]["options"]
        assert item.zip == str(z)
    print("upload_one_zip success flow OK")


def test_upload_one_zip_fails_when_dataset_missing():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "重废3_实例分割数据集.zip")
        plan = plan_day_zips(datasets)[0]
        assert not plan.skip
        client = _FakeClient(dataset_id=None)
        item = upload_one_zip(plan, client)
        assert item.status == "failed"
        assert "平台上没有数据集" in item.message
        # 未发生任何直传/建任务
        assert client.calls == ["search:永锋-重废3"]
    print("upload_one_zip missing dataset OK")


def test_upload_zips_for_dates_aggregates():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d1 = root / "2026-09-01" / "datasets"
        _mkzip(d1, "废钢分类数据集.zip")
        _mkzip(d1, "重废1_实例分割数据集.zip", "material.jpg")
        _mkzip(d1, "重废1_边缘分割数据集.zip", "material.jpg")
        (root / "2026-09-02").mkdir()  # 无 datasets/
        result = upload_zips_for_dates(str(root), dates=["2026-09-01", "2026-09-02"], client=_FakeClient())
        assert result["uploaded"] == 2
        assert result["skipped"] == 1  # 同料型只选一个压缩包
        assert result["failed"] == 1  # 09-02 无 datasets 目录
        text = result["summary_text"]
        assert "永锋-废钢分类" in text
        assert "永锋-重废1" in text
        assert "2026-09-02" in text
        assert text.count("导入成功") == 2
        assert "全部日期处理完成：成功 2 个，跳过 1 个，失败 1 个。" in text
        assert "最终结果" in (root / "upload_progress.log").read_text(encoding="utf-8")
        assert "✅ 永锋数据集上传完成" not in text
    print("upload_zips_for_dates aggregation OK")


def test_unconfirmed_pending_is_not_reported_as_success():
    from agent.yongfeng.scrap_uploader import _record_pending

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _record_pending(path, "永锋-重废1", 555, 42)

        class UnknownClient(_FakeClient):
            def get_job_status(self, job_id):
                raise RuntimeError("状态查询失败")

        client = UnknownClient()
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["failed"] == 1
        assert "状态暂时无法确认" in result["summary_text"]
        assert "put" not in client.calls


def test_waiting_job_is_polled_until_success(monkeypatch):
    from agent.yongfeng import scrap_uploader

    monkeypatch.setattr(scrap_uploader.time, "sleep", lambda _: None)
    client = _FakeClient(statuses=[1, 2, 3])
    assert client.wait_job(1001)["status"] == 3


def test_waiting_pending_job_is_not_reuploaded():
    from agent.yongfeng.scrap_uploader import _record_pending

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        folder = root / "2026-09-01" / "datasets"
        path = _mkzip(folder, "重废1_实例分割数据集.zip")
        _record_pending(path, "永锋-重废1", 555, 42)
        client = _FakeClient(statuses=[1])
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["failed"] == 1
        assert (folder / "upload_pending.txt").is_file()
        assert "put" not in client.calls


def test_waiting_platform_job_blocks_entry():
    client = _FakeClient()
    calls = []

    def get(path, params=None):
        calls.append(params["status"])
        return {"data": [{"id": 555, "status": 1}] if params["status"] == 1 else []}

    client._get = get
    assert AthenaClient.find_running_import_job(client, 42) == 555
    assert calls == [1]


def test_success_record_failure_preserves_pending_and_does_not_retry(monkeypatch):
    from agent.yongfeng import scrap_uploader

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        folder = root / "2026-09-01" / "datasets"
        _mkzip(folder, "重废1_实例分割数据集.zip")
        original = Path.open

        def failing_open(path, *args, **kwargs):
            if path.name == "uploaded.txt":
                raise OSError("无法写入")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", failing_open)
        client = _FakeClient()
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["failed"] == 1
        assert (folder / scrap_uploader.PENDING_FILE_NAME).is_file()
        assert client.calls.count("put") == 1


def test_pending_record_failure_prevents_job_submission(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        folder = root / "2026-09-01" / "datasets"
        _mkzip(folder, "重废1_实例分割数据集.zip")
        original = Path.open

        def failing_open(path, *args, **kwargs):
            if path.name == "upload_pending.txt":
                raise OSError("无法写入")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", failing_open)
        client = _FakeClient()
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["failed"] == 1
        assert "尚未提交导入任务" in result["summary_text"]
        assert client.calls.count("put") == 1
        assert not any(call.startswith("job:") for call in client.calls)


def test_settings_upload_credentials():
    cfg = settings.yongfeng_upload
    assert cfg.base_url.startswith("http")
    assert cfg.username == "intern1"
    assert cfg.password
    assert cfg.namespace == 1
    assert cfg.dataset_name_prefix == "永锋-"
    assert cfg.impurity_dataset_name == "永锋废钢杂质分割"
    assert cfg.impurity_dataset_id == 28392
    print("settings upload credentials OK")


def test_upload_one_zip_impurity_uses_fixed_id():
    """杂质包不走搜名，直接用配置的固定 dataset id。"""
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, IMPURITY_ZIP_NAME)
        plan = plan_day_zips(datasets)[0]
        assert plan.dataset_name == "永锋废钢杂质分割"
        client = _FakeClient(dataset_id=999, statuses=[2, 3])
        item = upload_one_zip(plan, client)
        assert item.status == "uploaded"
        assert item.dataset_id == 28392
        assert not any(c.startswith("search:") for c in client.calls)
        assert client.calls.index("busy:28392") < client.calls.index("signature:28392")
        assert next(i for i, c in enumerate(client.calls) if c.startswith("list:28392:")) < client.calls.index("put")
        assert client.calls.index("put") < client.calls.index("job:28392:tmp/uploads/datasets/abc123")
    print("upload_one_zip impurity fixed id OK")


def test_same_dataset_entry_defers_when_stuck():
    """平台约束：同一数据集入口一次只能导入一个包。首轮任务超时后，
    入口被锁，后续同入口的 zip 暂存到最后统一重试（不是直接跳过）。"""
    from agent.yongfeng.scrap_uploader import upload_day_zips, PendingRetry
    from pathlib import Path as P
    import tempfile

    class _StuckClient(_FakeClient):
        def __init__(self):
            super().__init__(dataset_id=42)
            self._stuck_used = False

        def wait_job(self, job_id: int) -> Dict[str, Any]:
            if not self._stuck_used:
                self._stuck_used = True
                raise YongfengUploadError(f"导入任务 {job_id} 等待超时")
            return super().wait_job(job_id)

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        d1 = root / "2026-09-01" / "datasets"
        _mkzip(d1, "重废1_实例分割数据集.zip")
        d2 = root / "2026-09-02" / "datasets"
        _mkzip(d2, "重废1_实例分割数据集.zip")
        _mkzip(d2, "废钢分类数据集.zip")
        client = _StuckClient()
        blocked: Dict[str, int] = {}
        deferred: List = []
        r1 = upload_day_zips(root / "2026-09-01", client=client, blocked_datasets=blocked, deferred=deferred)
        # 首轮超时：暂存待重试，失败条目保留 job_id
        assert r1["items"][0]["status"] == "retry_pending"
        assert r1["items"][0]["job_id"] == 1001
        assert "统一重试" in r1["items"][0]["message"]
        assert blocked == {"永锋-重废1": 1001}
        assert len(deferred) == 1 and deferred[0].own_job
        r2 = upload_day_zips(root / "2026-09-02", client=client, blocked_datasets=blocked, deferred=deferred)
        by_name = {P(it["zip"]).name: it for it in r2["items"]}
        # 同入口（永锋-重废1）被锁：暂存待重试，不再直传
        assert by_name["重废1_实例分割数据集.zip"]["status"] == "retry_pending"
        assert "入口被未完成任务 1001 占用" in by_name["重废1_实例分割数据集.zip"]["message"]
        assert "search:永锋-重废1" not in client.calls[1:]  # 没有再搜索/直传
        # 不同入口（永锋-废钢分类）不受影响，正常上传成功
        assert by_name["废钢分类数据集.zip"]["status"] == "uploaded"
        assert len(deferred) == 2
        assert deferred[1].own_job is False and deferred[1].blocking_job_id == 1001
    print("same-dataset entry defer OK")


def test_retry_round_leaves_own_job_running():
    """自己的任务仍在跑：保留待确认记录，不取消或重复导入。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _StuckStillRunningClient(_FakeClient):
        def wait_job(self, job_id: int) -> Dict[str, Any]:
            if job_id == 1001:
                raise YongfengUploadError(f"导入任务 {job_id} 等待超时")
            return super().wait_job(job_id)

        def get_job_status(self, job_id: int) -> Dict[str, Any]:
            if job_id == 1001:
                return {"status": 2}
            return super().get_job_status(job_id)

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "废钢分类数据集.zip")
        client = _StuckStillRunningClient()
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01", "2026-09-02"], client=client
        )
        assert result["uploaded"] == 1
        assert result["failed"] == 1
        text = result["summary_text"]
        assert "【统一重试】" not in text  # 总结已合并为最终态
        assert "仍在运行或等待" in text
        stops = [c for c in client.calls if c.startswith("stop:")]
        assert stops == []
        assert client.calls.count("put") == 2
    print("retry leaves own job running OK")


def test_retry_round_rechecks_own_job_and_recovers():
    """首轮超时的任务若其实已成功，重查确认即可，不必重传；
    入口释放后，其他天被暂存的同入口 zip 重传成功。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _RecoveredClient(_FakeClient):
        def __init__(self):
            super().__init__(dataset_id=42, statuses=[3])
            self._stuck_used = False

        def wait_job(self, job_id: int) -> Dict[str, Any]:
            if not self._stuck_used:
                self._stuck_used = True
                raise YongfengUploadError(f"导入任务 {job_id} 等待超时")
            return super().wait_job(job_id)

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "重废1_实例分割数据集.zip", "day-two-material.jpg")
        _mkzip(root / "2026-09-02" / "datasets", "废钢分类数据集.zip", "day-two-classification.jpg")
        client = _RecoveredClient()
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01", "2026-09-02"], client=client
        )
        assert result["uploaded"] == 3  # 09-01 重废1(重查) + 09-02 重废1(重传) + 09-02 分类
        assert result["failed"] == 0
        text = result["summary_text"]
        assert "重查确认已导入成功" in text
        assert "重试导入成功" in text
        assert "【统一重试】" not in text
        assert text.count("⏳") == 0  # 最终总结不含首轮暂存行
        # 09-01 的重废1 没有重传：put 次数 = 09-01首轮 + 09-02分类 + 09-02重废1重试 = 3
        assert client.calls.count("put") == 3
    print("retry recheck own job OK")


def test_first_round_network_error_retries_once():
    """首轮网络失败 → 暂存 → 最后统一重试一次 → 重试成功。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _FlakyPutClient(_FakeClient):
        def __init__(self):
            super().__init__(dataset_id=42)
            self._put_failed = False

        def put_zip_to_minio(self, signature: Dict[str, str], zip_path: Path) -> None:
            if not self._put_failed:
                self._put_failed = True
                raise YongfengUploadError("MinIO 直传中断（网络）")
            super().put_zip_to_minio(signature, zip_path)

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01"], client=_FlakyPutClient()
        )
        assert result["uploaded"] == 1
        assert result["failed"] == 0
        text = result["summary_text"]
        # 最终总结只保留重试成功态，不再夹带首轮 ⏳ 行
        assert "重试导入成功" in text
        assert "⏳" not in text
    print("first-round network error retry OK")


def test_cross_run_busy_entry_waits_not_interrupts():
    """别人会话留下的在跑任务：不抢入口、不 stop，记失败让用户稍后补传。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _ForeignJobRunningClient(_FakeClient):
        """平台上残留一个在跑的导入任务 23700（别的会话留下）。"""

        def __init__(self):
            super().__init__(dataset_id=42, statuses=[2])
            self.foreign_job = 23700

        def find_running_import_job(self, dataset_id: int) -> Optional[int]:
            self.calls.append(f"busy:{dataset_id}")
            return self.foreign_job

        def wait_job(self, job_id: int) -> Dict[str, Any]:
            return {"status": 3}

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        client = _ForeignJobRunningClient()
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["uploaded"] == 0
        assert result["failed"] == 1
        text = result["summary_text"]
        assert "仍在运行" in text
        assert "未终止他人任务" in text
        # 绝不 stop 别人的任务
        stops = [c for c in client.calls if c.startswith("stop:")]
        assert stops == []
        assert "put" not in client.calls
        assert not any(c.startswith("job:") for c in client.calls)
    print("cross-run busy entry OK")


def test_pending_record_recovers_across_runs():
    """wait 超时后进程中断：pending 留下；下次重查已成功则补记 uploaded，不再重传。"""
    from agent.yongfeng.scrap_uploader import (
        upload_zips_for_dates,
        RECORD_FILE_NAME,
        PENDING_FILE_NAME,
        _record_pending,
    )
    from pathlib import Path as P
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        datasets = root / "2026-09-01" / "datasets"
        z = _mkzip(datasets, "重废1_实例分割数据集.zip")
        # 模拟上一轮：已建任务 555，但 wait 超时未写 uploaded.txt
        _record_pending(z, "永锋-重废1", 555, 42)

        class _RecoverClient(_FakeClient):
            def get_job_status(self, job_id: int) -> Dict[str, Any]:
                self.calls.append(f"status:{job_id}")
                return {"status": 3, "duration": 1, "info": {"total": 1, "success": 1}}

        client = _RecoverClient(dataset_id=42)
        client.imported_images[42] = {"test.jpg"}
        result = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert result["uploaded"] == 0
        assert result["skipped"] == 1
        assert "已成功导入过" in result["summary_text"]
        assert "put" not in client.calls
        assert (datasets / RECORD_FILE_NAME).is_file()
        assert not (datasets / PENDING_FILE_NAME).exists()
    print("pending record recovers across runs OK")


def test_run_with_deadline_supervision():
    """直传超时监督：超时抛 YongfengUploadError，快路径不受影响。"""
    from agent.yongfeng.scrap_uploader import _run_with_deadline
    import time as _time

    # 快速完成：不超时
    _run_with_deadline(lambda: None, 1.0, "fast")
    # 卡死（远超时限）：抛错且很快返回
    t0 = _time.monotonic()
    try:
        _run_with_deadline(lambda: _time.sleep(30), 0.2, "hung")
        raise AssertionError("应当超时")
    except YongfengUploadError as exc:
        assert "终止该上传" in str(exc)
    assert _time.monotonic() - t0 < 5
    # fn 自身抛错：原样转抛
    def _boom():
        raise RuntimeError("网络中断")
    try:
        _run_with_deadline(_boom, 1.0, "boom")
        raise AssertionError("应当转抛")
    except RuntimeError:
        pass
    print("run_with_deadline supervision OK")


def test_success_record_and_platform_name_check_on_rerun():
    """成功导入记进 datasets/uploaded.txt，重跑时以平台图片名核对。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates, RECORD_FILE_NAME
    from pathlib import Path as P
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        d = root / "2026-09-01" / "datasets"
        _mkzip(d, "废钢分类数据集.zip")
        client = _FakeClient(dataset_id=42)
        r1 = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert r1["uploaded"] == 1
        record = d / RECORD_FILE_NAME
        assert record.is_file()
        content = record.read_text(encoding="utf-8")
        assert "废钢分类数据集.zip" in content
        assert "永锋-废钢分类" in content
        assert "任务 1001" in content
        # 重跑同一日期时已核实全部图片在平台
        r2 = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert r2["uploaded"] == 0
        assert r2["skipped"] == 1
        assert "已成功导入过" in r2["summary_text"]
        assert client.calls.count("put") == 1  # 只直传过一次
        assert len([c for c in client.calls if c.startswith("job:")]) == 1
    print("upload success record and rerun OK")


def test_repacked_zip_only_sends_platform_missing_images(tmp_path):
    folder = tmp_path / "2026-09-01" / "datasets"
    name = "废钢分类数据集.zip"
    client = _FakeClient()
    _mkzip(folder, name, "old.jpg")
    assert upload_zips_for_dates(str(tmp_path), client=client)["uploaded"] == 1

    _mkzip(folder, name, "old.jpg", "added.jpg")
    report = upload_zips_for_dates(str(tmp_path), client=client)
    assert report["uploaded"] == 1
    assert client.staged_images == {"added.jpg"}
    assert client.imported_images[42] == {"old.jpg", "added.jpg"}
    assert upload_zips_for_dates(str(tmp_path), client=client)["skipped"] == 1
    assert client.calls.count("put") == 2


def test_repacked_material_with_changed_suffix_only_uploads_missing_images(tmp_path):
    folder = tmp_path / "2026-09-01" / "datasets"
    first = _mkzip(folder, "重废1_实例分割数据集.zip", "old.jpg")
    client = _FakeClient()
    assert upload_zips_for_dates(str(tmp_path), client=client)["uploaded"] == 1

    first.unlink()
    _mkzip(folder, "重废1补下的照片.zip", "old.jpg", "added.jpg")
    assert upload_zips_for_dates(str(tmp_path), client=client)["uploaded"] == 1
    assert client.staged_images == {"added.jpg"}


def test_first_upload_checks_platform_without_local_record(tmp_path):
    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废1_补下的原图.zip", "already-imported.jpg", "new.jpg")
    client = _FakeClient()
    client.imported_images[42] = {"already-imported.jpg"}
    assert not (folder / "uploaded.txt").exists()

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 1
    assert client.staged_images == {"new.jpg"}
    assert client.calls.count("put") == 1
    assert "list:42:" in client.calls


def test_same_material_zips_submit_once_and_other_material_uploads(tmp_path):
    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废Ⅱ_01边缘.zip", "shared.jpg", "other.jpg")
    _mkzip(folder, "重废Ⅱ_02实例.zip", "shared.jpg", "other.jpg")
    _mkzip(folder, "重废Ⅲ任意后缀.zip", "another-material.jpg")
    client = _FakeClient(dataset_ids={"永锋-重废2": 42, "永锋-重废3": 43})

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 2
    assert report["skipped"] == 1
    assert client.imported_images == {42: {"shared.jpg", "other.jpg"}, 43: {"another-material.jpg"}}
    assert client.calls.count("put") == 2
    items = report["days"][0]["items"]
    first_material = [item for item in items if item["dataset_name"] == "永锋-重废2"]
    assert [item["status"] for item in first_material].count("uploaded") == 1
    assert next(item for item in items if "01边缘" in item["zip"])["status"] == "uploaded"


def test_rechecks_platform_if_new_images_arrive_during_direct_upload(tmp_path):
    class ConcurrentPlatformChangeClient(_FakeClient):
        def put_zip_to_minio(self, signature, zip_path):
            super().put_zip_to_minio(signature, zip_path)
            self.imported_images.setdefault(42, set()).add("20260901_newly-present.jpg")

    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废1_补图.zip", "20260901_newly-present.jpg", "20260901_missing.jpg")
    client = ConcurrentPlatformChangeClient()

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 1
    assert report["failed"] == 0
    assert client.staged_images == {"20260901_missing.jpg"}
    assert client.calls.count("put") == 2
    assert len(client.submitted_jobs) == 1
    assert "list:42:20260901" in client.calls


def test_retry_reports_skip_when_all_images_arrive_during_direct_upload(tmp_path):
    class ConcurrentPlatformChangeClient(_FakeClient):
        def put_zip_to_minio(self, signature, zip_path):
            super().put_zip_to_minio(signature, zip_path)
            self.imported_images.setdefault(42, set()).update(self.staged_images)

    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废1_补图.zip", "20260901_newly-present.jpg")
    client = ConcurrentPlatformChangeClient()

    report = upload_zips_for_dates(str(tmp_path), client=client)
    assert report["skipped"] == 1 and report["failed"] == 0
    assert "平台已有包内全部图片" in report["summary_text"]
    assert "重试仍失败" not in report["summary_text"]
    assert client.calls.count("put") == 1 and not client.submitted_jobs


def test_partial_import_next_run_only_sends_missing_images(tmp_path):
    class PartialClient(_FakeClient):
        def get_job_status(self, job_id):
            if job_id == 1001:
                dataset_id, _ = self.submitted_jobs[job_id]
                self.imported_images.setdefault(dataset_id, set()).add("one.jpg")
                return {"status": 3, "info": {"total": 2, "success": 1}}
            return super().get_job_status(job_id)

    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废1_补图.zip", "one.jpg", "two.jpg")
    client = PartialClient()

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 0
    assert report["failed"] == 1
    assert "部分导入，成功 1/2 张" in report["summary_text"]
    assert (folder / "upload_pending.txt").is_file()
    assert client.calls.count("put") == 1

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 1
    assert report["failed"] == 0
    assert client.staged_images == {"two.jpg"}
    assert client.imported_images[42] == {"one.jpg", "two.jpg"}
    assert not (folder / "upload_pending.txt").exists()


def test_failed_task_without_counts_keeps_pending(tmp_path):
    from agent.yongfeng.scrap_uploader import _record_pending

    class UnknownCountClient(_FakeClient):
        def get_job_status(self, job_id):
            return {"status": 4, "info": None}

    folder = tmp_path / "2026-09-01" / "datasets"
    path = _mkzip(folder, "重废1_补图.zip", "one.jpg", "two.jpg")
    _record_pending(path, "永锋-重废1", 555, 42)
    client = UnknownCountClient()
    client.imported_images[42] = {"one.jpg"}

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 0
    assert report["failed"] == 1
    assert (folder / "upload_pending.txt").is_file()
    assert "put" not in client.calls


def test_concurrent_upload_from_another_root_is_rejected_before_submission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    put_started = Event()
    release_put = Event()

    class HeldUploadClient(_FakeClient):
        def put_zip_to_minio(self, signature, zip_path):
            super().put_zip_to_minio(signature, zip_path)
            put_started.set()
            assert release_put.wait(timeout=5)

    first_root, second_root = tmp_path / "first", tmp_path / "second"
    for root in (first_root, second_root):
        _mkzip(root / "2026-09-01" / "datasets", "重废1_补图.zip")
    first_client = HeldUploadClient()
    second_client = _FakeClient()
    second_client.imported_images = first_client.imported_images

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_upload = executor.submit(upload_zips_for_dates, str(first_root), client=first_client)
        try:
            assert put_started.wait(timeout=5)
            with pytest.raises(YongfengUploadError):
                upload_zips_for_dates(str(second_root), client=second_client)
            assert not second_client.calls
        finally:
            release_put.set()
        assert first_upload.result(timeout=5)["uploaded"] == 1

    assert upload_zips_for_dates(str(second_root), client=second_client)["skipped"] == 1
    assert len(first_client.submitted_jobs) == 1
    assert not second_client.submitted_jobs


def test_success_counts_without_visible_images_keep_pending(tmp_path):
    class DelayedVisibilityClient(_FakeClient):
        def get_job_status(self, job_id):
            return {"status": 3, "info": {"total": 1, "success": 1}}

    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "重废1_补图.zip", "one.jpg")
    client = DelayedVisibilityClient()

    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["uploaded"] == 0
    assert (folder / "upload_pending.txt").is_file()

    client.imported_images[42] = {"one.jpg"}
    report = upload_zips_for_dates(str(tmp_path), client=client)

    assert report["skipped"] == 1
    assert not (folder / "upload_pending.txt").exists()
    assert client.calls.count("put") == 1


def test_unconfirmed_creation_looks_up_original_key_on_next_run(tmp_path):
    from agent.yongfeng.scrap_uploader import ImportCreationUnconfirmed

    class MissingAckClient(_FakeClient):
        recovered = False

        def create_import_job(self, dataset_id, key):
            super().create_import_job(dataset_id, key)
            raise ImportCreationUnconfirmed("HTTP 502，任务创建结果待确认")

        def find_import_job_by_key(self, dataset_id, key):
            assert (dataset_id, key) == (42, "tmp/uploads/datasets/abc123")
            return 1001 if self.recovered else None

    folder = tmp_path / "2026-09-01" / "datasets"
    _mkzip(folder, "废钢分类数据集.zip")
    client = MissingAckClient(statuses=[3])
    assert upload_zips_for_dates(str(tmp_path), client=client)["failed"] == 1
    assert upload_zips_for_dates(str(tmp_path), client=client)["failed"] == 1
    client.recovered = True
    assert upload_zips_for_dates(str(tmp_path), client=client)["skipped"] == 1
    assert client.calls.count("put") == 1
    assert len([call for call in client.calls if call.startswith("job:")]) == 1


def test_route_upload_directive():
    assert is_yongfeng_dataset_upload("上传 2026-09-01 的【永锋】数据集")
    assert is_yongfeng_dataset_upload("把永锋数据集 zip 上传到金睛平台")
    assert is_yongfeng_dataset_upload("导入 2026-09-01 的永锋压缩包")
    # 下载语义不命中（归下载分支）
    assert not is_yongfeng_dataset_upload("下载 2026-09-01 的【永锋】检判原图")
    assert not is_yongfeng_dataset_upload("下载并上传 2026-09-01 的永锋数据集")
    assert not is_yongfeng_dataset_upload("上传盛隆多标签数据集")
    assert not is_yongfeng_dataset_upload("下载永锋MINIO图像")
    # 无永锋不命中
    assert not is_yongfeng_dataset_upload("上传 2026-09-01 的数据集")
    # 上传但没提数据集/zip/金睛，不命中（防误路由）
    assert not is_yongfeng_dataset_upload("帮我上传这个文件")
    print("route upload directive OK")


def main():
    test_parse_zip_prefix()
    test_plan_day_zips_routes_by_material_prefix()
    test_plan_day_zips_accepts_unfamiliar_suffix()
    test_plan_day_zips_ignores_non_zip_and_unknown_names()
    test_plan_day_zips_skips_empty_and_corrupt()
    test_upload_one_zip_success_flow()
    test_upload_one_zip_fails_when_dataset_missing()
    test_upload_one_zip_impurity_uses_fixed_id()
    test_upload_zips_for_dates_aggregates()
    test_same_dataset_entry_defers_when_stuck()
    test_retry_round_leaves_own_job_running()
    test_retry_round_rechecks_own_job_and_recovers()
    test_first_round_network_error_retries_once()
    test_cross_run_busy_entry_waits_not_interrupts()
    test_pending_record_recovers_across_runs()
    test_run_with_deadline_supervision()
    test_success_record_and_platform_name_check_on_rerun()
    test_settings_upload_credentials()
    test_route_upload_directive()
    print("\nAll yongfeng upload tests PASSED")


if __name__ == "__main__":
    main()
