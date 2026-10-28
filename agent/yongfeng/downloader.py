"""永锋废钢检判原图下载（只走 vision.lg.china-yongfeng.com/srape-steel）。

流程：登录 → 按日拉列表 → 拉详情 → 取「智能判级照片」originImageUrl
→ 立刻写到用户指定的本地目录。已存在且非空的文件跳过，中断后可续传。

目录：
    <output>/<YYYY-MM-DD>/<YYYY-MM-DD_车牌_重废Ⅰ>/日期_英文料型_点位_第几辆_第几张.jpg

每个日期下完车次后，可选 scp（失败不中断），再打本地 datasets 压缩包。
车次文件夹用详情页废钢主料型，不写占比。没有主料型时文件夹记为「无人工」。

CLI：python -m agent.yongfeng.downloader --start YYYY-MM-DD --end YYYY-MM-DD --output DIR
不要占用烧结矿入口 python -m agent.yongfeng。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from agent.yongfeng.scrap_client import YongfengScrapClient
from agent.yongfeng.scrap_models import YongfengRecord
from agent.yongfeng.scrap_naming import (
    MaterialShare,
    build_image_filename,
    build_truck_folder_name,
    build_truck_folder_stem,
    extract_origin_image_urls,
    main_share_from_detail,
    resolve_station_code,
)
from agent.yongfeng.scrap_packager import pack_day_datasets
from agent.yongfeng.scrap_remote_sync import SyncResult, format_sync_report, sync_date_folder

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

_FLOW_MARK = ".briefme_flow"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_IMAGE_INDEX_RE = re.compile(r"_(\d+)_(\d+)_(\d+)$")


@dataclass
class TruckDownloadResult:
    flow_code: str
    car_number: str
    station: str
    folder: str
    saved_files: list[Path] = field(default_factory=list)
    skipped_existing: int = 0
    failed_files: list[tuple[str, str]] = field(default_factory=list)
    shares: list[MaterialShare] = field(default_factory=list)
    pack_shares: list[MaterialShare] = field(default_factory=list)


@dataclass
class DayDownloadResult:
    date: str
    total_trucks: int
    processed: int = 0
    saved_files: int = 0
    skipped_existing: int = 0
    failed_files: int = 0
    skipped_no_manual: int = 0
    skipped_no_images: int = 0
    failed_detail: list[tuple[str, str]] = field(default_factory=list)
    zip_files: list[str] = field(default_factory=list)
    scp_ok: Optional[bool] = None
    scp_skipped: bool = False
    scp_error: str = ""
    scp_remote_path: str = ""


def last_complete_7_days(today: Optional[date] = None) -> tuple[str, str]:
    """近 7 天：昨天往前共 7 个自然日（含昨天、不含今天）。"""
    day = today or date.today()
    end = day - timedelta(days=1)
    start = day - timedelta(days=7)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def resolve_output_dir(output_dir: str | Path) -> Path:
    text = str(output_dir or "").strip()
    if not text:
        raise ValueError("必须指定本地保存路径")
    root = Path(text).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


class _ProgressWriter:
    """同时写输出目录日志、项目 download_log.txt，并回调 UI。"""

    def __init__(self, output_root: Path, callback: Optional[ProgressCallback]) -> None:
        self.output_root = output_root
        self.callback = callback
        self.log_path = output_root / "download_progress.log"
        self.state_path = output_root / "download_progress.json"
        self.project_log = Path.cwd() / "download_log.txt"
        self.state: dict = {
            "status": "running",
            "output_dir": str(output_root),
            "current_date": "",
            "current_truck": "",
            "saved": 0,
            "skipped": 0,
            "failed": 0,
        }
        self._emit("开始下载，文件会立刻写到本地：" + str(output_root))

    def _emit(self, message: str, done: int = 0, total: int = 0) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
        logger.info(message)
        for path in (self.log_path, self.project_log):
            try:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                logger.warning("写进度日志失败: %s", path)
        self._flush_state()
        if self.callback:
            self.callback(done, total, message)

    def _flush_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("写进度状态失败: %s", self.state_path)

    def event(self, message: str, *, done: int = 0, total: int = 0, **updates) -> None:
        self.state.update(updates)
        self._emit(message, done=done, total=total)

    def finish(self, ok: bool) -> None:
        self.state["status"] = "done" if ok else "error"
        self._emit("全部任务结束" if ok else "任务结束（有失败）")


def _looks_like_image(data: bytes) -> bool:
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"BM"):
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _legacy_station_token(station_number) -> str:
    if isinstance(station_number, (list, tuple)):
        if not station_number:
            return "0"
        return str(station_number[-1])
    return str(station_number or "0")


def _download_image(client: YongfengScrapClient, url: str, dest: Path) -> int:
    """下载单张原图。仅 401/403 或网络中断才重登；404 等业务错误直接失败。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.session.get(
                url, headers=client._headers(), timeout=60, verify=False
            )
            if resp.status_code in (401, 403) and attempt == 0:
                logger.warning("永锋原图鉴权失败，重登后重试")
                client.login()
                continue
            if resp.status_code == 404:
                raise RuntimeError(f"原图不存在（HTTP 404）: {url}")
            resp.raise_for_status()
            payload = resp.content
            if not _looks_like_image(payload):
                raise RuntimeError(
                    f"响应不是图片（{resp.headers.get('Content-Type') or 'unknown'}, "
                    f"{len(payload)} bytes）"
                )
            dest.write_bytes(payload)
            return len(payload)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # 4xx（除鉴权）是对象本身的问题，重登无意义
            if status is not None and 400 <= int(status) < 500 and int(status) not in (401, 403):
                raise
            if attempt == 0:
                logger.warning("永锋原图下载中断，重登后重试: %s", exc)
                try:
                    client.login()
                except Exception:
                    pass
                continue
            break
    if last_err:
        raise last_err
    raise RuntimeError("下载失败")


def _read_flow_mark(truck_dir: Path) -> str:
    try:
        return (truck_dir / _FLOW_MARK).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_flow_mark(truck_dir: Path, flow_code: str) -> None:
    if not flow_code:
        return
    (truck_dir / _FLOW_MARK).write_text(flow_code + "\n", encoding="utf-8")


def _find_dir_by_flow(day_dir: Path, flow_code: str) -> Optional[Path]:
    if not flow_code or not day_dir.is_dir():
        return None
    for path in day_dir.iterdir():
        if path.is_dir() and path.name != "datasets" and _read_flow_mark(path) == flow_code:
            return path
    return None


def _existing_image(
    truck_dir: Path,
    dest: Path,
    station: str,
    image_index: int,
) -> Optional[Path]:
    """续传：规范文件名命中，或同工位同「第几张」的旧文件（列表顺序变了也能跳过）。"""
    candidates = [dest]
    if truck_dir.is_dir():
        for path in truck_dir.iterdir():
            if path == dest or not path.is_file():
                continue
            if path.suffix.lower() not in _IMAGE_EXTS:
                continue
            matched = _IMAGE_INDEX_RE.search(path.stem)
            if not matched:
                continue
            if matched.group(1) == str(station) and int(matched.group(3)) == image_index:
                candidates.append(path)
    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            if _looks_like_image(path.read_bytes()[:16]):
                return path
            path.unlink()
        except OSError:
            continue
    return None


def _try_rename_dir(src: Path, dest: Path) -> Path:
    if src.resolve() == dest.resolve():
        return dest
    if dest.exists():
        return src
    try:
        src.rename(dest)
        return dest
    except OSError:
        return src


def _unique_truck_dir(
    day_dir: Path,
    folder_name: str,
    daily_index: int,
    *,
    legacy_name: str = "",
    extra_legacy: Sequence[str] = (),
) -> Path:
    """手册规范名 YYYY-MM-DD_车牌_料型(...) 。仅规范名被另一辆车占用时加 _N。"""
    candidate = day_dir / folder_name
    indexed = day_dir / f"{folder_name}_{daily_index}"

    if indexed.exists() and not candidate.exists():
        return _try_rename_dir(indexed, candidate)

    if candidate.exists():
        if daily_index <= 1:
            return candidate
        return indexed

    old_dirs: list[Path] = []
    if legacy_name:
        old_dirs.append(day_dir / legacy_name)
        old_dirs.append(day_dir / f"{legacy_name}_{daily_index}")
    for extra in extra_legacy:
        if extra:
            old_dirs.append(day_dir / extra)
    for old_path in old_dirs:
        if old_path.is_dir() and old_path.resolve() != candidate.resolve():
            return _try_rename_dir(old_path, candidate)
    return candidate


def _folder_shares(detail: dict, raw: dict | None = None) -> list[MaterialShare]:
    """文件夹和文件名只用详情页废钢主料型，不写占比。"""
    return main_share_from_detail(detail, raw)


def _pack_shares(_detail: dict, folder_shares: Sequence[MaterialShare]) -> list[MaterialShare]:
    """打包和文件夹用同一条人工主料型。没有主料型就不进料型包。"""
    return list(folder_shares)


def download_truck_images(
    client: YongfengScrapClient,
    record: YongfengRecord,
    detail: dict,
    date_str: str,
    output_root: Path,
    daily_index: int,
) -> TruckDownloadResult:
    shares = _folder_shares(detail, record.raw)
    pack_shares = _pack_shares(detail, shares)
    station = resolve_station_code(record.station_number, detail)
    urls = extract_origin_image_urls(detail)
    folder_name = build_truck_folder_name(record.car_number, shares, date_str)
    legacy_name = build_truck_folder_stem(record.car_number, shares)
    extra_legacy = [
        f"{record.car_number}_{_legacy_station_token(record.station_number)}_{record.flow_code}",
    ]
    day_dir = output_root / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    canonical = day_dir / folder_name
    truck_dir = _find_dir_by_flow(day_dir, record.flow_code)
    if truck_dir is None:
        truck_dir = _unique_truck_dir(
            day_dir,
            folder_name,
            daily_index,
            legacy_name=legacy_name,
            extra_legacy=extra_legacy,
        )
    else:
        truck_dir = _try_rename_dir(truck_dir, canonical)
    truck_dir.mkdir(parents=True, exist_ok=True)
    _write_flow_mark(truck_dir, record.flow_code)

    result = TruckDownloadResult(
        flow_code=record.flow_code,
        car_number=record.car_number,
        station=station,
        folder=truck_dir.name,
        shares=list(shares),
        pack_shares=pack_shares,
    )
    if not urls:
        return result

    for idx, url in enumerate(urls, start=1):
        filename = build_image_filename(
            date_str, station, daily_index, shares, idx
        )
        dest = truck_dir / filename
        existing = _existing_image(truck_dir, dest, station, idx)
        if existing is not None:
            result.skipped_existing += 1
            result.saved_files.append(existing)
            continue
        try:
            _download_image(client, url, dest)
            result.saved_files.append(dest)
        except Exception as exc:  # noqa: BLE001
            # 404 / 坏链很常见，只记一行警告，不要 exception 堆栈刷屏
            logger.warning("永锋下载失败 %s -> %s", url, exc)
            result.failed_files.append((url, str(exc)))
    return result


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:到|至|-)\s*(\d{4}-\d{2}-\d{2})")


def expand_date_range(start_date: str, end_date: str) -> list[str]:
    start, end = start_date, end_date or start_date
    if start > end:
        start, end = end, start
    cur = datetime.strptime(start, "%Y-%m-%d")
    last = datetime.strptime(end, "%Y-%m-%d")
    out: list[str] = []
    while cur <= last:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _relative_dates_from_text(text: str) -> list[str]:
    """手册：近 7 天不含今天；昨天=昨日。仅在文中没有 YYYY-MM-DD 时使用。"""
    if "近7天" in text or "近 7 天" in text or "近一周" in text:
        start, end = last_complete_7_days(date.today())
        return expand_date_range(start, end)
    if "昨天" in text or "昨日" in text:
        return [(date.today() - timedelta(days=1)).strftime("%Y-%m-%d")]
    return []


def parse_requested_dates(
    text: str = "",
    *,
    start_date: str = "",
    end_date: str = "",
    dates: Optional[Sequence[str]] = None,
) -> list[str]:
    """解析要下载的日期列表。

    优先级：显式 dates → 文本里的「A 到 B」区间 + 顿号/逗号枚举 → start/end 闭区间。
    「2026-08-01、2026-08-03」不会自动补上 08-02。
    """
    found: set[str] = set()
    if dates:
        for item in dates:
            token = str(item or "").strip()
            if _DATE_RE.fullmatch(token):
                found.add(token)
    if text:
        for start, end in _RANGE_RE.findall(text):
            found.update(expand_date_range(start, end))
        for token in _DATE_RE.findall(text):
            found.add(token)
        if not found:
            found.update(_relative_dates_from_text(text))
    if not found and start_date:
        found.update(expand_date_range(start_date, end_date or start_date))
    return sorted(found)


def _sorted_records(records: list[YongfengRecord]) -> list[YongfengRecord]:
    return sorted(records, key=lambda rec: (rec.create_time or "", rec.flow_code or ""))


def iter_download_images(
    start_date: str = "",
    end_date: str = "",
    output_dir: str | Path = "",
    *,
    dates: Optional[Sequence[str]] = None,
    progress_callback: Optional[ProgressCallback] = None,
    include_missing_manual: bool = True,
) -> Iterator[dict]:
    """边下边产出进度事件；最后一条 type=done，含汇总结果。"""
    day_list = parse_requested_dates(
        start_date=start_date, end_date=end_date, dates=dates
    )
    if not day_list:
        raise ValueError("没有解析到有效日期，请使用 YYYY-MM-DD")
    output_root = resolve_output_dir(output_dir)
    writer = _ProgressWriter(output_root, progress_callback)
    label = "、".join(day_list)
    writer.event(f"日期 {label}，保存到 {output_root}")
    yield {"type": "start", "message": f"开始下载 {label}", "output_dir": str(output_root)}

    day_results: list[DayDownloadResult] = []
    zip_paths: list[str] = []
    # 远程 SSH 一旦确认不可达，本轮后续日期不再白等 ConnectTimeout
    skip_remote_sync = False
    skip_remote_reason = ""

    with YongfengScrapClient() as client:
        for cur in day_list:
            records = _sorted_records(client.query_list_by_date(cur))
            day = DayDownloadResult(date=cur, total_trucks=len(records))
            pack_trucks: list[dict] = []
            writer.event(
                f"{cur} 共 {len(records)} 车，开始逐车落盘",
                current_date=cur,
                current_truck="",
            )
            yield {"type": "day_start", "message": f"{cur} 共 {len(records)} 车", "date": cur}

            for daily_index, rec in enumerate(records, start=1):
                try:
                    detail = client.get_detail_by_flow(
                        rec.flow_code, rec.station_number
                    )
                except Exception as exc:  # noqa: BLE001
                    day.failed_detail.append((rec.flow_code, str(exc)))
                    day.failed_files += 1
                    msg = f"{cur} {rec.car_number} 详情失败: {exc}"
                    writer.event(msg, done=daily_index, total=len(records), failed=day.failed_files)
                    yield {"type": "progress", "message": msg}
                    continue

                shares = _folder_shares(detail, rec.raw)
                urls = extract_origin_image_urls(detail)
                if not shares and not include_missing_manual:
                    day.skipped_no_manual += 1
                    continue
                if not urls:
                    day.skipped_no_images += 1
                    msg = f"{cur} {rec.car_number} 无智能判级原图，跳过"
                    writer.event(msg, done=daily_index, total=len(records))
                    yield {"type": "progress", "message": msg}
                    continue

                result = download_truck_images(
                    client, rec, detail, cur, output_root, daily_index
                )
                day.processed += 1
                day.saved_files += len(result.saved_files) - result.skipped_existing
                day.skipped_existing += result.skipped_existing
                day.failed_files += len(result.failed_files)
                pack_trucks.append({
                    "files": result.saved_files,
                    "shares": result.pack_shares or result.shares,
                })
                msg = (
                    f"{cur} 第{daily_index}/{len(records)}车 {rec.car_number} "
                    f"→ {result.folder} "
                    f"新下{len(result.saved_files) - result.skipped_existing} "
                    f"跳过{result.skipped_existing} 失败{len(result.failed_files)}"
                )
                writer.event(
                    msg,
                    done=daily_index,
                    total=len(records),
                    current_date=cur,
                    current_truck=rec.car_number,
                    saved=writer.state.get("saved", 0) + len(result.saved_files) - result.skipped_existing,
                    skipped=writer.state.get("skipped", 0) + result.skipped_existing,
                    failed=writer.state.get("failed", 0) + len(result.failed_files),
                )
                yield {"type": "progress", "message": msg}

            if skip_remote_sync:
                sync = SyncResult(
                    ok=True,
                    skipped=True,
                    error=skip_remote_reason or "远程不可达，已跳过",
                    remote_path="",
                )
            else:
                try:
                    sync = sync_date_folder(output_root / cur)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("永锋 scp 同步异常，已跳过: %s", exc)
                    sync = SyncResult(ok=False, skipped=False, error=str(exc))
                err_l = (sync.error or "").lower()
                if (
                    not sync.ok
                    and not sync.skipped
                    and any(
                        token in err_l
                        for token in (
                            "timed out",
                            "timeout",
                            "connection refused",
                            "no route to host",
                            "network is unreachable",
                            "could not resolve",
                        )
                    )
                ):
                    skip_remote_sync = True
                    skip_remote_reason = f"远程不可达（{sync.error}），后续日期跳过 scp"
            day.scp_ok = sync.ok
            day.scp_skipped = sync.skipped
            day.scp_error = sync.error
            day.scp_remote_path = sync.remote_path
            if sync.skipped:
                if "未配置" in (sync.error or ""):
                    scp_msg = f"{cur} 未配置远程同步，跳过 scp"
                elif "远程不可达" in (sync.error or ""):
                    scp_msg = f"{cur} {sync.error}"
                else:
                    scp_msg = f"{cur} 没有可同步的车次文件夹，跳过 scp"
            elif sync.ok:
                scp_msg = f"{cur} 已原样 scp 到 {sync.remote_path}"
            else:
                scp_msg = f"{cur} scp 失败（已跳过，下载继续）: {sync.error}"
                if skip_remote_sync and skip_remote_reason:
                    scp_msg = f"{scp_msg}；{skip_remote_reason}"
            writer.event(scp_msg, current_date=cur)
            yield {
                "type": "day_scp",
                "message": scp_msg,
                "date": cur,
                "ok": sync.ok,
                "skipped": sync.skipped,
            }

            zips = pack_day_datasets(output_root / cur, pack_trucks) if pack_trucks else []
            day.zip_files = [str(p) for p in zips]
            zip_paths.extend(day.zip_files)
            if zips:
                pack_msg = f"{cur} 已打包 {len(zips)} 个数据集： " + "、".join(
                    Path(p).name for p in zips
                )
            else:
                pack_msg = f"{cur} 无车次可打包，跳过 datasets"
            writer.event(pack_msg, current_date=cur)
            yield {"type": "day_packed", "message": pack_msg, "zips": day.zip_files}

            day_results.append(day)

    summary = {
        "output_dir": str(output_root),
        "days": [d.__dict__ for d in day_results],
        "success": sum(d.saved_files for d in day_results),
        "failed": sum(d.failed_files for d in day_results),
        "skipped_existing": sum(d.skipped_existing for d in day_results),
        "zip_files": zip_paths,
        "dates": day_list,
        "scp_report": format_sync_report([d.__dict__ for d in day_results]),
    }
    writer.finish(summary["failed"] == 0)
    yield {
        "type": "done",
        "message": (
            "下载完成。已自动打好「废钢分类数据集」及其副本「废钢杂质数据集」，"
            "并按主料型打好实例分割和边缘分割包。"
            "不再另打「废钢多标签分类数据集」。"
        ),
        "result": summary,
    }


def download_images_by_date_range(
    start_date: str = "",
    end_date: str = "",
    output_dir: str | Path | None = None,
    *,
    dates: Optional[Sequence[str]] = None,
    progress_callback: Optional[ProgressCallback] = None,
    include_missing_manual: bool = True,
) -> dict:
    """兼容旧入口：跑完整条迭代，返回最终汇总。"""
    if output_dir is None:
        raise ValueError("必须指定本地保存路径 output_dir")
    final: dict = {}
    for event in iter_download_images(
        start_date,
        end_date,
        output_dir,
        dates=dates,
        progress_callback=progress_callback,
        include_missing_manual=include_missing_manual,
    ):
        if event.get("type") == "done":
            final = event.get("result") or {}
    return final


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="永锋检判原图下载")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="结束日期 YYYY-MM-DD，默认与 --start 相同")
    parser.add_argument("--output", required=True, help="本机保存目录")
    args = parser.parse_args(argv)
    result = download_images_by_date_range(
        args.start,
        args.end or args.start,
        output_dir=args.output,
    )
    print(
        json.dumps(
            {
                "output_dir": result.get("output_dir"),
                "success": result.get("success"),
                "failed": result.get("failed"),
                "skipped_existing": result.get("skipped_existing"),
                "zip_files": result.get("zip_files"),
                "scp_report": result.get("scp_report"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not result.get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
