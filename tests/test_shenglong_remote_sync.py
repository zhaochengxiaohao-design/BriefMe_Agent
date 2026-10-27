"""盛隆日期文件夹 scp 同步单测（不连真实服务器）。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.shenglong.remote_sync import format_sync_report, sync_date_folder
from config.settings import settings


def _ok_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = ""
    proc.stderr = ""
    return proc


def test_sync_skips_empty_day(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-01"
    day.mkdir()
    result = sync_date_folder(day)
    assert result.ok is True
    assert result.skipped is True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Proc:
    """poll 立刻结束，或按时钟结束。stderr 为 None 时不启读取线程。"""

    def __init__(self, code: int = 0, err: str = "", done_at: float | None = 0.0, clock: _Clock | None = None) -> None:
        self.returncode: int | None = None if done_at is None else code
        self._code = code
        self.stderr = _Text(err) if err else None
        self.done_at = done_at
        self.clock = clock
        self.killed = False

    def poll(self) -> int | None:
        if self.killed:
            self.returncode = -9
            return -9
        if self.done_at is None:
            return None
        if self.clock is not None and self.clock.now < self.done_at:
            return None
        self.returncode = self._code
        return self._code

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _Text:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


def test_sync_excludes_datasets_and_copies_trucks(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-01"
    truck = day / "桂A00001_重废1(80)、中废(20)"
    truck.mkdir(parents=True)
    (truck / "a.jpg").write_bytes(b"x")
    (day / "datasets").mkdir()
    (day / "datasets" / "平均料型_实例分割数据集.zip").write_bytes(b"z")

    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return _ok_proc()

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return _Proc()

    with (
        patch("agent.shenglong.remote_sync.subprocess.run", side_effect=fake_run),
        patch("agent.shenglong.remote_sync.subprocess.Popen", side_effect=fake_popen),
    ):
        result = sync_date_folder(day)

    assert result.ok is True
    assert result.skipped is False
    assert result.remote_path.endswith("/2026-08-01")
    scp_calls = [c for c in calls if c and c[0] == "scp"]
    assert len(scp_calls) == 1
    assert str(truck) in scp_calls[0]
    joined = " ".join(scp_calls[0])
    assert str(day / "datasets") not in scp_calls[0]
    assert "cisdi@10.180.34.16:" in joined
    assert "/test_images_full_car/2026-08-01/" in joined


def test_sync_failure_does_not_raise(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-02"
    truck = day / "桂B00002_中废(100)"
    truck.mkdir(parents=True)
    (truck / "b.jpg").write_bytes(b"y")

    def fake_run(cmd, **_kwargs):
        return _ok_proc()

    def fake_popen(cmd, **_kwargs):
        return _Proc(code=1, err="Permission denied", done_at=0.0)

    with (
        patch("agent.shenglong.remote_sync.subprocess.run", side_effect=fake_run),
        patch("agent.shenglong.remote_sync.subprocess.Popen", side_effect=fake_popen),
    ):
        result = sync_date_folder(day)

    assert result.ok is False
    assert result.skipped is False
    assert "Permission denied" in result.error


def test_sync_timeout_is_failure(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-03"
    truck = day / "桂C00003_重废1(100)"
    truck.mkdir(parents=True)
    (truck / "c.jpg").write_bytes(b"z")

    with patch(
        "agent.shenglong.remote_sync.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="scp", timeout=1),
    ):
        result = sync_date_folder(day)

    assert result.ok is False
    assert "超时" in result.error


def _patch_clock(clock: _Clock):
    return (
        patch("agent.shenglong.remote_sync.time.monotonic", clock.monotonic),
        patch("agent.shenglong.remote_sync.time.sleep", clock.sleep),
    )


def test_slow_transfer_is_not_failure_while_bytes_grow(tmp_path: Path) -> None:
    """超过旧的 1800 秒，只要远程体积还在涨，就不判失败。"""
    day = tmp_path / "2026-08-04"
    truck = day / "桂D00004_重废1(100)"
    truck.mkdir(parents=True)
    (truck / "d.jpg").write_bytes(b"z")
    clock = _Clock()

    def fake_run(cmd, **_kwargs):
        proc = _ok_proc()
        if cmd and any("du -sb" in str(part) for part in cmd):
            proc.stdout = str(int(clock.now) + 1)
        return proc

    def fake_popen(cmd, **_kwargs):
        return _Proc(done_at=4000.0, clock=clock)

    mono, sleep = _patch_clock(clock)
    with (
        patch("agent.shenglong.remote_sync.subprocess.run", side_effect=fake_run),
        patch("agent.shenglong.remote_sync.subprocess.Popen", side_effect=fake_popen),
        mono,
        sleep,
    ):
        result = sync_date_folder(day)

    assert result.ok is True
    assert clock.now >= 4000


def test_stalled_remote_bytes_are_failure(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-05"
    truck = day / "桂E00005_重废1(100)"
    truck.mkdir(parents=True)
    (truck / "e.jpg").write_bytes(b"z")
    clock = _Clock()
    proc = _Proc(done_at=None, clock=clock)

    def fake_run(cmd, **_kwargs):
        result = _ok_proc()
        if cmd and any("du -sb" in str(part) for part in cmd):
            result.stdout = "100"
        return result

    mono, sleep = _patch_clock(clock)
    with (
        patch.object(settings.shenglong, "remote_scp_stall_sec", 120),
        patch("agent.shenglong.remote_sync.subprocess.run", side_effect=fake_run),
        patch("agent.shenglong.remote_sync.subprocess.Popen", return_value=proc),
        mono,
        sleep,
    ):
        result = sync_date_folder(day)

    assert result.ok is False
    assert proc.killed is True
    assert "没有增加" in result.error


def test_hard_cap_stops_even_if_bytes_still_grow(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-06"
    truck = day / "桂F00006_重废1(100)"
    truck.mkdir(parents=True)
    (truck / "f.jpg").write_bytes(b"z")
    clock = _Clock()
    proc = _Proc(done_at=None, clock=clock)

    def fake_run(cmd, **_kwargs):
        result = _ok_proc()
        if cmd and any("du -sb" in str(part) for part in cmd):
            result.stdout = str(int(clock.now) + 1)
        return result

    mono, sleep = _patch_clock(clock)
    with (
        patch.object(settings.shenglong, "remote_scp_timeout_sec", 120),
        patch.object(settings.shenglong, "remote_scp_stall_sec", 99999),
        patch("agent.shenglong.remote_sync.subprocess.run", side_effect=fake_run),
        patch("agent.shenglong.remote_sync.subprocess.Popen", return_value=proc),
        mono,
        sleep,
    ):
        result = sync_date_folder(day)

    assert result.ok is False
    assert proc.killed is True
    assert "硬上限" in result.error


def test_format_sync_report_lists_failures() -> None:
    text = format_sync_report(
        [
            {"date": "2026-08-01", "scp_ok": True, "scp_skipped": False, "scp_error": ""},
            {
                "date": "2026-08-02",
                "scp_ok": False,
                "scp_skipped": False,
                "scp_error": "Connection refused",
            },
            {"date": "2026-08-03", "scp_ok": True, "scp_skipped": True, "scp_error": ""},
        ]
    )
    assert "成功：2026-08-01" in text
    assert "2026-08-02：Connection refused" in text
    assert "2026-08-03" not in text.split("成功：")[1].split("\n")[0]
    assert "没有中断" in text
