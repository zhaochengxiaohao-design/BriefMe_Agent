"""把永锋按日下载的车次文件夹 scp 到配置的远程机。

主机取自详情页照片 origin（10.233.224.206），远端目录用永锋 yf_feigang，
不要写入盛隆 sl_feigang。失败只返回错误，不抛给下载主流程。
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from config.settings import YongfengScrapConfig, settings

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    ok: bool
    skipped: bool
    error: str = ""
    remote_path: str = ""


def _cfg() -> YongfengScrapConfig:
    return settings.yongfeng_scrap


def _is_configured(cfg: YongfengScrapConfig) -> bool:
    return bool((cfg.remote_host or "").strip() and (cfg.remote_image_root or "").strip())


def _ssh_base(cfg: YongfengScrapConfig) -> list[str]:
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


def _scp_base(cfg: YongfengScrapConfig) -> list[str]:
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


def _truck_dirs(day_dir: Path) -> list[Path]:
    return sorted(
        p for p in day_dir.iterdir()
        if p.is_dir() and p.name != "datasets"
    )


def format_sync_report(days: Sequence[dict]) -> str:
    """把各日 scp 结果收成最终总结里的一段话。"""
    ok_dates: list[str] = []
    failed: list[str] = []
    unconfigured = False
    for row in days:
        date = str(row.get("date") or "")
        err = str(row.get("scp_error") or "").strip()
        if row.get("scp_skipped"):
            if "未配置" in err:
                unconfigured = True
            continue
        if row.get("scp_ok"):
            ok_dates.append(date)
        else:
            failed.append(f"{date}：{err or '未知错误'}" if date else (err or "未知错误"))

    lines = ["远程同步（scp）："]
    if unconfigured and not ok_dates and not failed:
        lines.append("未配置，已跳过")
        return "\n".join(lines)
    if ok_dates:
        lines.append("成功：" + "、".join(ok_dates))
    if failed:
        lines.append("失败（已跳过，下载没有中断）：")
        lines.extend(f"- {item}" for item in failed)
    if not ok_dates and not failed:
        lines.append("没有需要同步的车次文件夹")
    return "\n".join(lines)


def sync_date_folder(local_day_dir: Path) -> SyncResult:
    """把某个日期下的车次文件夹原样拷到远程（不含 datasets 压缩包）。"""
    cfg = _cfg()
    day_dir = Path(local_day_dir)
    remote_root = (cfg.remote_image_root or "").rstrip("/")
    remote_day = f"{remote_root}/{day_dir.name}" if remote_root else ""

    if not _is_configured(cfg):
        return SyncResult(
            ok=True,
            skipped=True,
            error="未配置远程主机",
            remote_path=remote_day,
        )

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

    timeout = max(1, int(cfg.remote_scp_timeout_sec))
    deadline = time.monotonic() + timeout
    try:
        mkdir = subprocess.run(
            _ssh_base(cfg) + [f"mkdir -p {shlex.quote(remote_day)}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mkdir.returncode != 0:
            err = (mkdir.stderr or mkdir.stdout or "ssh mkdir 失败").strip()
            logger.warning("永锋 scp 准备目录失败 %s: %s", day_dir.name, err)
            return SyncResult(ok=False, skipped=False, error=err, remote_path=remote_day)

        errors: list[str] = []
        dest = f"{cfg.remote_user}@{cfg.remote_host}:{remote_day}/"
        for truck in truck_dirs:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(f"{truck.name}: scp 超时（>{timeout}s）")
                break
            copied = subprocess.run(
                _scp_base(cfg) + [str(truck), dest],
                capture_output=True,
                text=True,
                timeout=remaining,
            )
            if copied.returncode != 0:
                err = (copied.stderr or copied.stdout or "scp 失败").strip()
                errors.append(f"{truck.name}: {err}")
                logger.warning("永锋 scp 失败 %s/%s: %s", day_dir.name, truck.name, err)
        if errors:
            return SyncResult(
                ok=False,
                skipped=False,
                error="; ".join(errors),
                remote_path=remote_day,
            )
    except subprocess.TimeoutExpired:
        err = f"scp 超时（>{timeout}s）"
        logger.warning("永锋 scp 超时 %s", day_dir.name)
        return SyncResult(ok=False, skipped=False, error=err, remote_path=remote_day)
    except Exception as exc:  # noqa: BLE001
        logger.warning("永锋 scp 异常 %s: %s", day_dir.name, exc)
        return SyncResult(ok=False, skipped=False, error=str(exc), remote_path=remote_day)

    logger.info("永锋 scp 成功 %s → %s", day_dir, remote_day)
    return SyncResult(ok=True, skipped=False, remote_path=remote_day)
