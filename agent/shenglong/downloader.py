"""盛隆废钢检判原图下载（只走 172.16.16.101:3000 业务系统）。

流程：登录 3000 → 按日拉列表 → 拉详情 → 取「智能判级照片」originImageUrl
→ 立刻写到用户指定的本地目录。已存在且非空的文件跳过，中断后可续传。

目录：
    <output>/<YYYY-MM-DD>/<YYYY-MM-DD_车牌_中废(40)、重废1(30)...>/日期_料型_点位_第几辆_第几张.jpg

每个日期下完车次后，先把该日车次文件夹 scp 到推理测试机，再打本地 datasets 压缩包。
scp 失败只记日志，不中断后续日期。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from agent.shenglong.client import ShenglongClient
from agent.shenglong.models import ShenglongRecord
from agent.shenglong.naming import (
    MaterialShare,
    build_image_filename,
    build_truck_folder_name,
    build_truck_folder_stem,
    extract_origin_image_urls,
    parse_manual_shares,
    resolve_station_code,
)
from agent.shenglong.calculator import last_complete_7_days
from agent.shenglong.packager import pack_day_datasets
from agent.shenglong.remote_sync import SyncResult, format_sync_report, sync_date_folder

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

_FLOW_MARK = ".briefme_flow"


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


def _download_image(client: ShenglongClient, url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = client._client.get(url, headers=client._auth_headers(), timeout=60.0)  # noqa: SLF001
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return len(resp.content)


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
) -> Path:
    """手册规范名 YYYY-MM-DD_车牌_料型(...) 。
    默认不加当日序号。规范名已被另一辆车占用（有 flow 标记）时用 _N。
    无标记的旧目录续传时仍回规范名，避免按列表序号拆出第二份。
    """
    candidate = day_dir / folder_name
    indexed = day_dir / f"{folder_name}_{daily_index}"
    if indexed.exists() and not candidate.exists():
        return _try_rename_dir(indexed, candidate)
    if candidate.exists():
        if daily_index <= 1:
            return candidate
        if _read_flow_mark(candidate) or indexed.exists():
            return indexed
        return candidate
    if legacy_name:
        for old_name in (legacy_name, f"{legacy_name}_{daily_index}"):
            old_path = day_dir / old_name
            if old_path.is_dir() and not candidate.exists():
                return _try_rename_dir(old_path, candidate)
    return candidate


def download_truck_images(
    client: ShenglongClient,
    record: ShenglongRecord,
    detail: dict,
    date_str: str,
    output_root: Path,
    daily_index: int,
) -> TruckDownloadResult:
    shares = parse_manual_shares(
        ((detail.get("manualCheckResultVO") or {}).get("avgResult"))
    )
    station = resolve_station_code(record.station_number, detail)
    urls = extract_origin_image_urls(detail)
    folder_name = build_truck_folder_name(record.car_number, shares, date_str)
    legacy_name = build_truck_folder_stem(record.car_number, shares)
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
        shares=shares,
    )
    if not urls:
        return result

    for idx, url in enumerate(urls, start=1):
        filename = build_image_filename(
            date_str, station, daily_index, shares, idx
        )
        dest = truck_dir / filename
        if dest.exists() and dest.stat().st_size > 0:
            result.skipped_existing += 1
            result.saved_files.append(dest)
            continue
        try:
            _download_image(client, url, dest)
            result.saved_files.append(dest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("下载失败 %s -> %s", url, exc)
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


def _sorted_records(records: list[ShenglongRecord]) -> list[ShenglongRecord]:
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

    with ShenglongClient() as client:
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
                    detail = client.get_detail_by_flow(rec.flow_code)
                except Exception as exc:  # noqa: BLE001
                    day.failed_detail.append((rec.flow_code, str(exc)))
                    day.failed_files += 1
                    msg = f"{cur} {rec.car_number} 详情失败: {exc}"
                    writer.event(msg, done=daily_index, total=len(records), failed=day.failed_files)
                    yield {"type": "progress", "message": msg}
                    continue

                shares = parse_manual_shares(
                    ((detail.get("manualCheckResultVO") or {}).get("avgResult"))
                )
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
                pack_trucks.append({"files": result.saved_files, "shares": result.shares})
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

            try:
                sync = sync_date_folder(output_root / cur)
            except Exception as exc:  # noqa: BLE001
                logger.exception("scp 同步异常，已跳过: %s", exc)
                sync = SyncResult(ok=False, skipped=False, error=str(exc))
            day.scp_ok = sync.ok
            day.scp_skipped = sync.skipped
            day.scp_error = sync.error
            day.scp_remote_path = sync.remote_path
            if sync.skipped:
                scp_msg = f"{cur} 没有可同步的车次文件夹，跳过 scp"
            elif sync.ok:
                scp_msg = f"{cur} 已原样 scp 到 {sync.remote_path}"
            else:
                scp_msg = f"{cur} scp 失败（已跳过，下载继续）: {sync.error}"
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
            "下载完成。实例/边缘分割包已按主料自动打好。"
            "「废钢多标签分类数据集」请先按日期、按车次打开文件夹删掉不合格图，"
            "再在对话里确认打包。"
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
    final = {}
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
