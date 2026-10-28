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
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"PK\x05\x06")  # 空zip签名占位，测试只看文件名
    return p


def test_parse_zip_prefix():
    assert parse_zip_prefix("废钢分类数据集.zip") == "废钢分类"
    assert parse_zip_prefix("重废1_实例分割数据集.zip") == "重废1"
    assert parse_zip_prefix("重废1_边缘分割数据集.zip") == "重废1"
    assert parse_zip_prefix("统料_实例分割数据集.zip") == "统料"
    assert parse_zip_prefix("统料1厚剪_边缘分割数据集.zip") == "统料1厚剪"
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
        _mkzip(datasets, "平均料型_实例分割数据集.zip")
        plans = plan_day_zips(datasets)
        by_name = {p.zip_path.name: p for p in plans}
        assert len(plans) == 4
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
        # 平均料型仍进计划，由搜索结果决定跳过
        avg = by_name["平均料型_实例分割数据集.zip"]
        assert not avg.skip and avg.dataset_name == "永锋-平均料型"
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
        # 链路顺序：搜索 → 签名 → 直传 → 建任务 → 轮询
        assert client.calls[0] == "search:永锋-重废1"
        assert client.calls[1] == "signature:42"
        assert client.calls[2] == "put"
        assert client.calls[3] == "job:42:tmp/uploads/datasets/abc123"
        # 无标注模式：options 里不允许出现 annotation_format
        assert client.job_payload["config"]["options"] == {"mode": 1}
        assert "annotation_format" not in client.job_payload["config"]["options"]
        assert item.zip == str(z)
    print("upload_one_zip success flow OK")


def test_upload_one_zip_skips_when_dataset_missing():
    with tempfile.TemporaryDirectory() as td:
        datasets = Path(td) / "d" / "datasets"
        _mkzip(datasets, "平均料型_实例分割数据集.zip")
        plan = plan_day_zips(datasets)[0]
        client = _FakeClient(dataset_id=None)
        item = upload_one_zip(plan, client)
        assert item.status == "skipped"
        assert "平台上没有数据集" in item.message
        # 未发生任何直传/建任务
        assert client.calls == ["search:永锋-平均料型"]
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
    print("upload_zips_for_dates aggregation OK")


def test_settings_upload_credentials():
    cfg = settings.yongfeng_upload
    assert cfg.base_url.startswith("http")
    assert cfg.username == "intern1"
    assert cfg.password
    assert cfg.namespace == 1
    assert cfg.dataset_name_prefix == "永锋-"
    print("settings upload credentials OK")


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


def test_retry_round_gives_up_when_platform_job_still_running():
    """统一重试时，若平台上占入口的任务仍在运行，不抢入口，记失败。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _StuckStillRunningClient(_FakeClient):
        def __init__(self):
            super().__init__(dataset_id=42, statuses=[2, 2])
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
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01", "2026-09-02"], client=_StuckStillRunningClient()
        )
        assert result["uploaded"] == 1   # 09-02 废钢分类
        assert result["failed"] == 2     # 两天重废1：任务仍在运行，不抢入口
        assert result["skipped"] == 0
        text = result["summary_text"]
        assert "【统一重试】" in text
        assert "仍在运行" in text
        assert "不抢入口" in text
        # 重试时没有再直传（put 只有首轮 09-01 一次）
        # client 未保留引用，这里靠 failed message 断言即可
    print("retry gives up when still running OK")


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
        assert "首轮失败（MinIO 直传中断（网络））" in text
        assert "重试导入成功" in text
    print("first-round network error retry OK")


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
    test_upload_one_zip_success_flow()
    test_upload_one_zip_skips_when_dataset_missing()
    test_upload_zips_for_dates_aggregates()
    test_same_dataset_entry_defers_when_stuck()
    test_retry_round_gives_up_when_platform_job_still_running()
    test_retry_round_rechecks_own_job_and_recovers()
    test_first_round_network_error_retries_once()
    test_settings_upload_credentials()
    test_route_upload_directive()
    print("\nAll yongfeng upload tests PASSED")


if __name__ == "__main__":
    main()
