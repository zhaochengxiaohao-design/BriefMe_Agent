"""把已下载的检判原图打成「只有 images/」的数据集压缩包。"""
from __future__ import annotations

import logging
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional, Sequence

from agent.shenglong.naming import MaterialShare, classify_pack_group

logger = logging.getLogger(__name__)

INSTANCE_SUFFIX = "实例分割数据集"
EDGE_SUFFIX = "边缘分割数据集"
MULTILABEL_NAME = "废钢多标签分类数据集"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_DAY_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def write_images_zip(zip_path: Path, image_files: Sequence[Path]) -> Path:
    """生成只含 images/ 的 zip；同名文件只收一份。"""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    unique: dict[str, Path] = {}
    for src in image_files:
        src = Path(src)
        if not src.is_file() or src.stat().st_size <= 0:
            continue
        unique[src.name] = src

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, src in unique.items():
            zf.write(src, arcname=f"images/{name}")
    logger.info("已打包 %s （%d 张）", zip_path.name, len(unique))
    return zip_path


def collect_reviewed_images(day_dir: Path) -> list[Path]:
    """收集某日各车次文件夹里还剩的原图（跳过 datasets）。"""
    files: list[Path] = []
    root = Path(day_dir)
    if not root.is_dir():
        return files
    for truck_dir in sorted(root.iterdir()):
        if not truck_dir.is_dir() or truck_dir.name == "datasets":
            continue
        for img in sorted(truck_dir.iterdir()):
            if img.is_file() and img.suffix.lower() in _IMAGE_EXTS and img.stat().st_size > 0:
                files.append(img)
    return files


def _day_dirs(output_root: Path, dates: Optional[Sequence[str]]) -> list[Path]:
    if dates:
        return [output_root / d for d in dates if _DAY_DIR_RE.fullmatch(str(d))]
    return sorted(
        p for p in output_root.iterdir()
        if p.is_dir() and _DAY_DIR_RE.fullmatch(p.name)
    )


def pack_multilabel_from_disk(
    output_dir: str | Path,
    dates: Optional[Sequence[str]] = None,
) -> dict:
    """人工筛图之后，按磁盘上还在的原图打「废钢多标签分类数据集」。"""
    root = Path(output_dir).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.is_dir():
        raise FileNotFoundError(f"保存目录不存在: {root}")

    created: list[str] = []
    day_stats: list[dict] = []
    for day_dir in _day_dirs(root, dates):
        if not day_dir.is_dir():
            day_stats.append({"date": day_dir.name, "images": 0, "zip": "", "skipped": True})
            continue
        files = collect_reviewed_images(day_dir)
        datasets_dir = day_dir / "datasets"
        datasets_dir.mkdir(parents=True, exist_ok=True)
        zip_path = datasets_dir / f"{MULTILABEL_NAME}.zip"
        if not files:
            if zip_path.exists():
                zip_path.unlink()
            day_stats.append({"date": day_dir.name, "images": 0, "zip": "", "skipped": True})
            continue
        write_images_zip(zip_path, files)
        created.append(str(zip_path))
        day_stats.append({
            "date": day_dir.name,
            "images": len(files),
            "zip": str(zip_path),
            "skipped": False,
        })
    return {
        "output_dir": str(root.resolve()),
        "zip_files": created,
        "days": day_stats,
    }


def pack_day_datasets(
    day_dir: Path,
    trucks: Iterable[dict],
) -> list[Path]:
    """一天下完后：只打主料/平均料型的实例+边缘包。多标签包等人工筛图后再打。"""
    groups: dict[str, list[Path]] = defaultdict(list)
    for truck in trucks:
        files = [Path(p) for p in (truck.get("files") or [])]
        files = [p for p in files if p.is_file() and p.stat().st_size > 0]
        if not files:
            continue
        shares: Sequence[MaterialShare] = truck.get("shares") or []
        kind, stem = classify_pack_group(shares)
        if kind == "none" or not stem:
            continue
        groups[stem].extend(files)

    if not groups:
        return []

    datasets_dir = Path(day_dir) / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    for pattern in (f"*{INSTANCE_SUFFIX}.zip", f"*{EDGE_SUFFIX}.zip"):
        for stale in datasets_dir.glob(pattern):
            stale.unlink(missing_ok=True)
    created: list[Path] = []
    for stem, files in groups.items():
        for suffix in (INSTANCE_SUFFIX, EDGE_SUFFIX):
            created.append(
                write_images_zip(datasets_dir / f"{stem}_{suffix}.zip", files)
            )
    return created
