"""永锋数据集上传（金睛平台）命名映射 / 上传计划 / 链路编排 单测（不连网）。

运行：python tests/test_yongfeng_upload.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.image_download_route import is_yongfeng_dataset_upload
from agent.yongfeng.scrap_uploader import (
    CLASSIFICATION_ZIP_NAME,
    IMPURITY_ZIP_NAME,
    INSTANCE_SUFFIX,
    YongfengUploadError,
    AthenaClient,
    UploadItem,
    upload_one_zip,
    upload_zips_for_dates,
    parse_zip_prefix,
    plan_day_zips,
)
from config.settings import settings


def _mkzip(directory: Path, name: str) -> Path:
    import zipfile

    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    # 合法空 zip（仅 EOCD），测试只看文件名 / 编排，不依赖内容
    with zipfile.ZipFile(p, "w"):
        pass
    return p


def test_parse_zip_prefix():
    assert parse_zip_prefix("废钢分类数据集.zip") == "废钢分类"
    # 杂质包由 plan_day_zips 单独映射，不走「永锋-」前缀
    assert parse_zip_prefix("废钢杂质数据集.zip") is None
    assert parse_zip_prefix("重废1_实例分割数据集.zip") == "重废1"
    assert parse_zip_prefix("重废1_边缘分割数据集.zip") == "重废1"
    assert parse_zip_prefix("统料_实例分割数据集.zip") == "统料"
    assert parse_zip_prefix("统料1厚剪_边缘分割数据集.zip") == "统料1厚剪"
    # 本地打包用全角罗马数字，平台数据集名用半角数字
    assert parse_zip_prefix("统料Ⅰ_实例分割数据集.zip") == "统料1"
    assert parse_zip_prefix("重废Ⅱ_边缘分割数据集.zip") == "重废2"
    assert parse_zip_prefix("统料Ⅱ_实例分割数据集.zip") == "统料2"
    assert parse_zip_prefix("重废Ⅲ_实例分割数据集.zip") == "重废3"
    assert parse_zip_prefix("平均料型_实例分割数据集.zip") == "平均料型"
    assert parse_zip_prefix("别的文件.zip") is None
    assert parse_zip_prefix("车次图片.zip") is None
    print("parse_zip_prefix OK")


def test_plan_day_zips_dedup_prefers_instance():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "2026-09-01" / "datasets"
        _mkzip(datasets, "重废1_边缘分割数据集.zip")
        _mkzip(datasets, "重废1_实例分割数据集.zip")
        _mkzip(datasets, "废钢分类数据集.zip")
        _mkzip(datasets, "废钢杂质数据集.zip")
        _mkzip(datasets, "平均料型_实例分割数据集.zip")
        plans = plan_day_zips(datasets)
        by_name = {p.zip_path.name: p for p in plans}
        assert len(plans) == 5
        # 实例分割优先上传
        chosen = by_name["重废1_实例分割数据集.zip"]
        assert not chosen.skip
        assert chosen.dataset_name == "永锋-重废1"
        # 边缘分割被去重跳过
        edge = by_name["重废1_边缘分割数据集.zip"]
        assert edge.skip and edge.dataset_name == "永锋-重废1"
        assert "只传一个" in edge.reason
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
    print("plan_day_zips dedup OK")


def test_plan_day_zips_edge_fallback_when_no_instance():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "重废2_边缘分割数据集.zip")
        plans = plan_day_zips(datasets)
        assert len(plans) == 1 and not plans[0].skip
        assert plans[0].dataset_name == "永锋-重废2"
    print("plan_day_zips edge fallback OK")


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


class _FakeClient(AthenaClient):
    """按脚本回放的假客户端（不发任何网络请求）。"""

    def __init__(self, dataset_id: Optional[int] = 42, statuses: List[int] = None):
        from config.settings import settings as _settings

        self.cfg = _settings.yongfeng_upload
        self.dataset_id = dataset_id
        self.statuses = list(statuses or [2, 3])
        self.calls: List[str] = []
        self.job_payload: Dict[str, Any] = {}
        self.put_args: tuple = ()

    def search_dataset_id(self, dataset_name: str) -> Optional[int]:
        self.calls.append(f"search:{dataset_name}")
        return self.dataset_id

    def find_running_import_job(self, dataset_id: int) -> Optional[int]:
        self.calls.append(f"busy:{dataset_id}")
        return None

    def stop_job(self, dataset_id: int, job_id: int) -> None:
        self.calls.append(f"stop:{dataset_id}:{job_id}")
        # stop 之后再查应视为已结束，避免 _wait_job_not_running 空等
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
        self.calls.append("put")
        self.put_args = (signature["bucket"], signature["key"], Path(zip_path).name)

    def create_import_job(self, dataset_id: int, key: str) -> int:
        self.calls.append(f"job:{dataset_id}:{key}")
        self.job_payload = {
            "dataset": dataset_id,
            "config": {
                "source": {"files": [{"key": key}]},
                "options": {"mode": 1},
            },
        }
        return 1001

    def get_job_status(self, job_id: int) -> Dict[str, Any]:
        status = self.statuses.pop(0) if self.statuses else 3
        return {"status": status, "duration": 1, "info": {}}


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
        # 链路顺序：搜索 → 查在跑任务（跨指令防护）→ 签名 → 直传 → 建任务 → 轮询
        assert client.calls[0] == "search:永锋-重废1"
        assert client.calls[1] == "busy:42"
        assert client.calls[2] == "signature:42"
        assert client.calls[3] == "put"
        assert client.calls[4] == "job:42:tmp/uploads/datasets/abc123"
        # 无标注模式：options 里不允许出现 annotation_format
        assert client.job_payload["config"]["options"] == {"mode": 1}
        assert "annotation_format" not in client.job_payload["config"]["options"]
        assert item.zip == str(z)
    print("upload_one_zip success flow OK")


def test_upload_one_zip_skips_when_dataset_missing():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "重废9_实例分割数据集.zip")
        plan = plan_day_zips(datasets)[0]
        assert not plan.skip
        client = _FakeClient(dataset_id=None)
        item = upload_one_zip(plan, client)
        assert item.status == "skipped"
        assert "平台上没有数据集" in item.message
        # 未发生任何直传/建任务
        assert client.calls == ["search:永锋-重废9"]
    print("upload_one_zip missing dataset skip OK")


def test_upload_zips_for_dates_aggregates():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d1 = root / "2026-09-01" / "datasets"
        _mkzip(d1, "废钢分类数据集.zip")
        _mkzip(d1, "重废1_实例分割数据集.zip")
        _mkzip(d1, "重废1_边缘分割数据集.zip")
        (root / "2026-09-02").mkdir()  # 无 datasets/
        result = upload_zips_for_dates(str(root), dates=["2026-09-01", "2026-09-02"], client=_FakeClient())
        assert result["uploaded"] == 2
        assert result["skipped"] == 2  # 边缘包去重 + 09-02 无 datasets 目录
        assert result["failed"] == 0
        text = result["summary_text"]
        assert "永锋-废钢分类" in text
        assert "永锋-重废1" in text
        assert "2026-09-02" in text
        assert text.count("导入成功") == 2
        assert "全部日期处理完成：成功 2 个，跳过 2 个，失败 0 个。" in text
        assert "✅ 永锋数据集上传完成" not in text
    print("upload_zips_for_dates aggregation OK")


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
        assert client.calls[0] == "busy:28392"
        assert client.calls[1] == "signature:28392"
        assert client.calls[2] == "put"
        assert client.calls[3] == "job:28392:tmp/uploads/datasets/abc123"
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
            return {"status": 3}

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


def test_retry_round_stops_own_job_then_reuploads():
    """自己的任务仍在跑：只停永锋管辖入口上的本任务，再重传。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _StuckStillRunningClient(_FakeClient):
        def __init__(self):
            # 首轮 wait 超时后：重试轮 get_job_status 仍为 2 → stop → 重传成功
            super().__init__(dataset_id=42, statuses=[2])
            self._stuck_used = False
            self._job_n = 1000

        def wait_job(self, job_id: int) -> Dict[str, Any]:
            if not self._stuck_used:
                self._stuck_used = True
                raise YongfengUploadError(f"导入任务 {job_id} 等待超时")
            return {"status": 3}

        def create_import_job(self, dataset_id: int, key: str) -> int:
            self._job_n += 1
            self.calls.append(f"job:{dataset_id}:{key}")
            return self._job_n

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "废钢分类数据集.zip")
        client = _StuckStillRunningClient()
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01", "2026-09-02"], client=client
        )
        assert result["uploaded"] == 2
        assert result["failed"] == 0
        text = result["summary_text"]
        assert "【统一重试】" not in text  # 总结已合并为最终态
        assert "重试导入成功" in text or "导入成功" in text
        stops = [c for c in client.calls if c.startswith("stop:")]
        assert stops == ["stop:42:1001"]  # 只停自己的任务
    print("retry stops own job then reuploads OK")


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
            return {"status": 3}

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "废钢分类数据集.zip")
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
            self.calls.append("put")

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
                return {"status": 3, "duration": 1, "info": {}}

        client = _RecoverClient(dataset_id=42)
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


def test_uploaded_record_prevents_duplicate_import():
    """平台不去重：成功导入记进 datasets/uploaded.txt，重跑自动跳过。"""
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
        # 重跑同一日期：有记录 → 跳过，不再直传/建任务
        r2 = upload_zips_for_dates(str(root), dates=["2026-09-01"], client=client)
        assert r2["uploaded"] == 0
        assert r2["skipped"] == 1
        assert "已成功导入过" in r2["summary_text"]
        assert client.calls.count("put") == 1  # 只直传过一次
        assert len([c for c in client.calls if c.startswith("job:")]) == 1
    print("uploaded record dedup OK")


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
    test_plan_day_zips_dedup_prefers_instance()
    test_plan_day_zips_edge_fallback_when_no_instance()
    test_plan_day_zips_ignores_non_zip_and_unknown_names()
    test_plan_day_zips_skips_empty_and_corrupt()
    test_upload_one_zip_success_flow()
    test_upload_one_zip_skips_when_dataset_missing()
    test_upload_one_zip_impurity_uses_fixed_id()
    test_upload_zips_for_dates_aggregates()
    test_same_dataset_entry_defers_when_stuck()
    test_retry_round_stops_own_job_then_reuploads()
    test_retry_round_rechecks_own_job_and_recovers()
    test_first_round_network_error_retries_once()
    test_cross_run_busy_entry_waits_not_interrupts()
    test_pending_record_recovers_across_runs()
    test_run_with_deadline_supervision()
    test_uploaded_record_prevents_duplicate_import()
    test_settings_upload_credentials()
    test_route_upload_directive()
    print("\nAll yongfeng upload tests PASSED")


if __name__ == "__main__":
    main()
