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


def test_same_dataset_entry_blocked_after_stuck_import():
    """平台约束：同一数据集入口一次只能导入一个包。第一天导入未完成时，
    第二天同入口的 zip 必须跳过，其他入口不受影响。"""
    from agent.yongfeng.scrap_uploader import upload_day_zips, plan_day_zips
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
        blocked: set = set()
        r1 = upload_day_zips(root / "2026-09-01", client=client, blocked_datasets=blocked)
        assert r1["items"][0]["status"] == "failed"
        assert r1["items"][0]["job_id"] == 1001  # 失败条目保留 job_id
        blocked.add(r1["items"][0]["dataset_name"])
        r2 = upload_day_zips(root / "2026-09-02", client=client, blocked_datasets=blocked)
        by_name = {P(it["zip"]).name: it for it in r2["items"]}
        # 同入口（永锋-重废1）被锁：跳过且不再直传
        assert by_name["重废1_实例分割数据集.zip"]["status"] == "skipped"
        assert "未完成" in by_name["重废1_实例分割数据集.zip"]["message"]
        assert "search:永锋-重废1" not in client.calls[1:]  # 没有再搜索/直传
        # 不同入口（永锋-废钢分类）不受影响，正常上传成功
        assert by_name["废钢分类数据集.zip"]["status"] == "uploaded"
    print("same-dataset entry lock OK")


def test_upload_zips_for_dates_locks_stuck_entry_across_days():
    """多日期编排：第一天重废1 导入超时 → 第二天重废1 自动跳过，
    第二天废钢分类仍正常上传。"""
    from agent.yongfeng.scrap_uploader import upload_zips_for_dates
    from pathlib import Path as P
    import tempfile

    class _StuckOnceClient(_FakeClient):
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
        _mkzip(root / "2026-09-01" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "重废1_实例分割数据集.zip")
        _mkzip(root / "2026-09-02" / "datasets", "废钢分类数据集.zip")
        result = upload_zips_for_dates(
            str(root), dates=["2026-09-01", "2026-09-02"], client=_StuckOnceClient()
        )
        assert result["failed"] == 1   # 09-01 重废1 超时
        assert result["uploaded"] == 1  # 09-02 废钢分类
        assert result["skipped"] == 1   # 09-02 重废1 被入口锁跳过
        text = result["summary_text"]
        assert "等待超时" in text
        assert "已有未完成的导入任务" in text
    print("cross-day entry lock OK")


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
    test_same_dataset_entry_blocked_after_stuck_import()
    test_upload_zips_for_dates_locks_stuck_entry_across_days()
    test_settings_upload_credentials()
    test_route_upload_directive()
    print("\nAll yongfeng upload tests PASSED")


if __name__ == "__main__":
    main()
