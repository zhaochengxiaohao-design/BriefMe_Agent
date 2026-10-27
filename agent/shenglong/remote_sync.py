"""把盛隆按日下载的车次文件夹 scp 到推理测试机。失败只返回错误，不抛给下载主流程。"""
from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from config.settings import ShenglongConfig, settings

logger = logging.getLogger(__name__)

# 查询远程体积的间隔。失败不看这个间隔，而看体积是否还在涨。
SCP_POLL_INTERVAL_SEC = 60.0


@dataclass
class SyncResult:
    ok: bool
    skipped: bool
    error: str = ""
    remote_path: str = ""


def _ssh_base(cfg: ShenglongConfig) -> list[str]:
    return [
        "ssh",
        "-p",
        str(cfg.remote_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        f"{cfg.remote_user}@{cfg.remote_host}",
    ]


def _scp_base(cfg: ShenglongConfig) -> list[str]:
    return [
        "scp",
        "-r",
        "-P",
        str(cfg.remote_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
    ]


def _parse_du_bytes(stdout: str) -> Optional[int]:
    """从 `du -sb` 输出取出字节数。空输出或非数字返回 None。"""
    parts = (stdout or "").strip().split()
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _remote_size_command(cfg: ShenglongConfig, remote_path: str) -> list[str]:
    quoted = shlex.quote(remote_path)
    script = (
        f"if [ -e {quoted} ]; then du -sb {quoted} | awk '{{print $1}}'; "
        f"else echo 0; fi"
    )
    return _ssh_base(cfg) + [script]


def _query_remote_bytes(cfg: ShenglongConfig, remote_path: str) -> Optional[int]:
    """远程目录当前字节数。查不到时返回 None，不把它当成体积 0。"""
    try:
        probed = subprocess.run(
            _remote_size_command(cfg, remote_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.info("查询远程体积失败 %s: %s", remote_path, exc)
        return None
    if probed.returncode != 0:
        return None
    return _parse_du_bytes(probed.stdout)


def _start_stderr_reader(
    proc: subprocess.Popen[str],
) -> tuple[Optional[threading.Thread], list[str]]:
    chunks: list[str] = []
    if proc.stderr is None:
        return None, chunks

    def _drain() -> None:
        try:
            chunks.append(proc.stderr.read() or "")
        except Exception as exc:  # noqa: BLE001
            chunks.append(str(exc))

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    return reader, chunks


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("scp 进程结束超时")


def _watch_scp(
    proc: subprocess.Popen[str],
    cfg: ShenglongConfig,
    remote_path: str,
    *,
    hard_deadline: float,
    stall_sec: float,
) -> Optional[str]:
    """进程退出且返回码为 0 时返回 None。否则返回失败原因。

    体积还在增加就继续等，直到硬上限。连续 stall_sec 秒不增加才算卡死。
    """
    reader, stderr_chunks = _start_stderr_reader(proc)
    last_size: Optional[int] = None
    last_growth = time.monotonic()
    reason: Optional[str] = None
    while proc.poll() is None:
        now = time.monotonic()
        if now >= hard_deadline:
            _stop_process(proc)
            reason = f"传输仍在进行，但已超过硬上限 {int(cfg.remote_scp_timeout_sec)}s"
            break
        size = _query_remote_bytes(cfg, remote_path)
        if size is not None and (last_size is None or size > last_size):
            last_size = size
            last_growth = now
        elif now - last_growth >= stall_sec:
            _stop_process(proc)
            reason = f"远程体积连续 {int(stall_sec)}s 没有增加，已中止"
            break
        remaining = min(SCP_POLL_INTERVAL_SEC, max(0.0, hard_deadline - time.monotonic()))
        if remaining <= 0:
            continue
        time.sleep(remaining)

    if reader is not None:
        reader.join(timeout=5)
    if reason:
        return reason
    if proc.returncode not in (0, None):
        return "".join(stderr_chunks).strip() or "scp 失败"
    return None


def _truck_dirs(day_dir: Path) -> list[Path]:
    return sorted(
        p for p in day_dir.iterdir()
        if p.is_dir() and p.name != "datasets"
    )


def format_sync_report(days: Sequence[dict]) -> str:
    """把各日 scp 结果收成最终总结里的一段话。"""
    ok_dates: list[str] = []
    failed: list[str] = []
    for row in days:
        date = str(row.get("date") or "")
        if row.get("scp_skipped"):
            continue
        if row.get("scp_ok"):
            ok_dates.append(date)
        else:
            err = str(row.get("scp_error") or "未知错误").strip()
            failed.append(f"{date}：{err}" if date else err)

    lines = ["推理测试机同步（scp）："]
    if ok_dates:
        lines.append("成功：" + "、".join(ok_dates))
    if failed:
        lines.append("失败（已跳过，下载没有中断）：")
        lines.extend(f"- {item}" for item in failed)
    if not ok_dates and not failed:
        lines.append("没有需要同步的车次文件夹")
    return "\n".join(lines)


def sync_date_folder(local_day_dir: Path) -> SyncResult:
    """把某个日期下的车次文件夹原样拷到测试机（不含 datasets 压缩包）。

    远程结果：
        <remote_image_root>/<YYYY-MM-DD>/<YYYY-MM-DD_车牌_料型...>/原图
    """
    cfg = settings.shenglong
    day_dir = Path(local_day_dir)
    remote_root = cfg.remote_image_root.rstrip("/")
    remote_day = f"{remote_root}/{day_dir.name}"

    if not day_dir.is_dir():
        return SyncResult(
            ok=False,
            skipped=True,
            error=f"本地日期目录不存在: {day_dir}",
            remote_path=remote_day,
        )

    truck_dirs = _truck_dirs(day_dir)
    if not truck_dirs:
        return SyncResult(
            ok=True,
            skipped=True,
            error="没有可同步的车次文件夹",
            remote_path=remote_day,
        )

    hard_sec = max(1, int(cfg.remote_scp_timeout_sec))
    stall_sec = max(1, int(cfg.remote_scp_stall_sec))
    hard_deadline = time.monotonic() + hard_sec
    try:
        mkdir = subprocess.run(
            _ssh_base(cfg) + [f"mkdir -p {shlex.quote(remote_day)}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mkdir.returncode != 0:
            err = (mkdir.stderr or mkdir.stdout or "ssh mkdir 失败").strip()
            logger.warning("scp 准备目录失败 %s: %s", day_dir.name, err)
            return SyncResult(ok=False, skipped=False, error=err, remote_path=remote_day)

        errors: list[str] = []
        dest = f"{cfg.remote_user}@{cfg.remote_host}:{remote_day}/"
        for truck in truck_dirs:
            if time.monotonic() >= hard_deadline:
                errors.append(f"{truck.name}: 传输仍在进行，但已超过硬上限 {hard_sec}s")
                break
            remote_truck = f"{remote_day}/{truck.name}"
            proc = subprocess.Popen(
                _scp_base(cfg) + [str(truck), dest],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            reason = _watch_scp(
                proc,
                cfg,
                remote_truck,
                hard_deadline=hard_deadline,
                stall_sec=stall_sec,
            )
            if reason:
                errors.append(f"{truck.name}: {reason}")
                logger.warning("scp 失败 %s/%s: %s", day_dir.name, truck.name, reason)
        if errors:
            return SyncResult(
                ok=False,
                skipped=False,
                error="; ".join(errors),
                remote_path=remote_day,
            )
    except subprocess.TimeoutExpired:
        err = f"scp 准备阶段超时（>{hard_sec}s）"
        logger.warning("scp 准备阶段超时 %s", day_dir.name)
        return SyncResult(ok=False, skipped=False, error=err, remote_path=remote_day)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scp 异常 %s: %s", day_dir.name, exc)
        return SyncResult(ok=False, skipped=False, error=str(exc), remote_path=remote_day)

    logger.info("scp 成功 %s → %s", day_dir, remote_day)
    return SyncResult(ok=True, skipped=False, remote_path=remote_day)
