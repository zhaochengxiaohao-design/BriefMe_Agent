"""
盛隆工厂 MinIO 图像批量下载模块
从 MinIO 服务器下载指定日期范围的监控图像
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple, Callable, Optional

from minio import Minio
from tqdm import tqdm

# ============================================================
# 常量配置
# ============================================================
MINIO_HOST = "172.16.16.101"
MINIO_API_PORT = 19000
BUCKET = "scrape-steel"
PREFIX_BASE = "algo-active"
ACCESS_KEY = "cisdi_mv_minio"
SECRET_KEY = "cisdi_mv@minio123"

# 日志文件路径
LOG_FILE = Path.cwd() / "download_log.txt"

# 配置日志
log = logging.getLogger(__name__)


def write_log(message: str):
    """写入日志到文件"""
    try:
        # 使用绝对路径
        log_file = Path.cwd() / "download_log.txt"
        print(f"[DEBUG] 尝试写入: {log_file}")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
            f.flush()
        print(f"[DEBUG] 写入成功: {message[:50]}")
    except Exception as e:
        print(f"[DEBUG] 写入失败: {e}")
        import traceback
        traceback.print_exc()


def clear_log():
    """清空日志文件"""
    try:
        print(f"[DEBUG] clear_log 被调用")
        LOG_FILE.write_text("")
        print(f"[DEBUG] clear_log 完成")
    except Exception as e:
        print(f"[DEBUG] clear_log 失败: {e}")
        


# ============================================================
# 核心下载函数（原版，带终端进度条）
# ============================================================

def download_images_by_date_range(
    start_date: str,
    end_date: str,
    output_dir: str = None
) -> Dict:
    """下载指定日期范围内的所有图像"""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    if start > end:
        start, end = end, start

    if output_dir is None:
        output_dir = Path.cwd() / "shenglong_images"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"图像将保存到: {output_dir}")

    try:
        client = Minio(
            f"{MINIO_HOST}:{MINIO_API_PORT}",
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        if not client.bucket_exists(BUCKET):
            raise Exception(f"Bucket '{BUCKET}' 不存在，请检查 VPN 连接")
        log.info(f"已连接 MinIO: {MINIO_HOST}:{MINIO_API_PORT}, Bucket: {BUCKET}")
    except Exception as e:
        log.error(f"连接 MinIO 失败: {e}")
        return {
            "success": 0,
            "failed": 0,
            "output_dir": str(output_dir),
            "dates": [],
            "error": f"连接 MinIO 失败: {e}"
        }

    total_success = 0
    total_failed = 0
    dates_processed = []

    total_files = 0
    date_file_counts = {}
    
    log.info("正在扫描文件列表...")
    current = start
    while current <= end:
        prefix = f"{PREFIX_BASE}/{current.isoformat()}/"
        try:
            objects = list(client.list_objects(BUCKET, prefix=prefix, recursive=True))
            files = [o for o in objects if o.size and o.size > 0]
            date_file_counts[current.isoformat()] = len(files)
            total_files += len(files)
        except Exception as e:
            log.warning(f"扫描日期 {current} 失败: {e}")
            date_file_counts[current.isoformat()] = 0
        current += timedelta(days=1)

    log.info(f"共找到 {total_files} 个文件待下载")

    with tqdm(total=total_files, desc="总进度", unit="张") as pbar:
        current = start
        while current <= end:
            log.info(f"开始处理日期: {current}")
            success, failed = _download_single_date(
                client, current, output_dir, pbar, date_file_counts.get(current.isoformat(), 0)
            )
            total_success += success
            total_failed += failed
            dates_processed.append(current.isoformat())
            log.info(f"日期 {current}: 成功 {success}, 失败 {failed}")
            current += timedelta(days=1)

    log.info(f"全部完成: 总成功 {total_success}, 总失败 {total_failed}")

    return {
        "success": total_success,
        "failed": total_failed,
        "output_dir": str(output_dir),
        "dates": dates_processed,
    }

def _download_single_date(
    client: Minio,
    target_date,
    output_dir: Path,
    pbar: tqdm = None,
    expected_count: int = 0
) -> Tuple[int, int]:
    """下载单个日期的所有图像"""
    prefix = f"{PREFIX_BASE}/{target_date.isoformat()}/"
    date_dir = output_dir / target_date.isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)

    try:
        objects = list(client.list_objects(BUCKET, prefix=prefix, recursive=True))
        files = [o for o in objects if o.size and o.size > 0]
    except Exception as e:
        log.error(f"列出对象失败: {e}")
        return 0, 0

    if not files:
        log.warning(f"日期 {target_date} 没有找到文件")
        return 0, 0

    success = 0
    failed = 0
    use_sub_pbar = pbar is None and expected_count > 0
    
    if use_sub_pbar:
        sub_pbar = tqdm(total=expected_count, desc=f"下载 {target_date}", unit="张", leave=False)
    
    for idx, obj in enumerate(files, 1):
        filename = Path(obj.object_name).name
        local_path = date_dir / filename

        if local_path.exists() and local_path.stat().st_size == obj.size:
            log.debug(f"[{target_date}] 已存在，跳过: {filename}")
            success += 1
            if pbar:
                pbar.update(1)
            elif use_sub_pbar:
                sub_pbar.update(1)
            continue

        try:
            client.fget_object(BUCKET, obj.object_name, str(local_path))
            success += 1
            log.debug(f"[{target_date}] {idx}/{len(files)} 下载完成: {filename}")
        except Exception as e:
            failed += 1
            log.error(f"[{target_date}] 下载失败 {filename}: {e}")
        
        if pbar:
            pbar.update(1)
        elif use_sub_pbar:
            sub_pbar.update(1)

    if use_sub_pbar:
        sub_pbar.close()

    return success, failed

def write_log(message: str):
    """写入日志到文件"""
    log_file = Path.cwd() / "download_log.txt"
    try:
        # 确保文件存在
        if not log_file.parent.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"[DEBUG] 写入日志到: {log_file}")
        print(f"[DEBUG] 日志内容: {message[:80]}...")
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
            f.flush()
        
        # 验证写入
        if log_file.exists():
            size = log_file.stat().st_size
            print(f"[DEBUG] 写入成功，文件大小: {size} 字节")
        else:
            print(f"[DEBUG] 写入失败，文件不存在")
    except Exception as e:
        print(f"[DEBUG] 写入异常: {e}")
        import traceback
        traceback.print_exc()

# ============================================================
# 带进度回调的下载函数（用于前端显示）
# ============================================================

def download_images_with_progress(
    start_date: str,
    end_date: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    output_dir: str = None
) -> Dict:
    """
    带进度回调的下载函数，每 1% 更新一次进度
    用于在聊天框中显示实时下载进度
    """
    # 清空并初始化日志文件
    clear_log()
    write_log(f"========== 开始下载 {start_date} 到 {end_date} ==========")
    
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    if start > end:
        start, end = end, start

    if output_dir is None:
        output_dir = Path.cwd() / "shenglong_images"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"图像将保存到: {output_dir}")

    try:
        client = Minio(
            f"{MINIO_HOST}:{MINIO_API_PORT}",
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        if not client.bucket_exists(BUCKET):
            raise Exception(f"Bucket '{BUCKET}' 不存在，请检查 VPN 连接")
        log.info(f"已连接 MinIO: {MINIO_HOST}:{MINIO_API_PORT}, Bucket: {BUCKET}")
    except Exception as e:
        log.error(f"连接 MinIO 失败: {e}")
        write_log(f"[错误] 连接 MinIO 失败: {e}")
        return {
            "success": 0,
            "failed": 0,
            "output_dir": str(output_dir),
            "dates": [],
            "error": f"连接 MinIO 失败: {e}"
        }

    if progress_callback:
        progress_callback(0, 0, "正在扫描文件列表...")
    write_log("正在扫描文件列表...")

    total_files = 0
    dates = []

    current = start
    while current <= end:
        date_str = current.isoformat()
        prefix = f"{PREFIX_BASE}/{date_str}/"
        try:
            objects = list(client.list_objects(BUCKET, prefix=prefix, recursive=True))
            files = [o for o in objects if o.size and o.size > 0]
            total_files += len(files)
            dates.append(date_str)
        except Exception:
            pass
        current += timedelta(days=1)

    if total_files == 0:
        msg = "没有找到任何文件，请检查日期范围或 VPN 连接"
        if progress_callback:
            progress_callback(0, 0, f"[错误] {msg}")
        write_log(f"[错误] {msg}")
        return {
            "success": 0,
            "failed": 0,
            "output_dir": str(output_dir),
            "dates": dates,
            "error": msg
        }

    msg = f"找到 {total_files} 个文件，开始下载..."
    if progress_callback:
        progress_callback(0, total_files, msg)
    write_log(msg)

    total_success = 0
    total_failed = 0
    completed = 0
    last_percent = -1

    current = start
    while current <= end:
        date_str = current.isoformat()
        date_dir = output_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        prefix = f"{PREFIX_BASE}/{date_str}/"
        try:
            objects = list(client.list_objects(BUCKET, prefix=prefix, recursive=True))
            files = [o for o in objects if o.size and o.size > 0]
        except Exception as e:
            log.error(f"列出对象失败 {date_str}: {e}")
            current += timedelta(days=1)
            continue

        # 添加日志：开始处理当前日期
        write_log(f"开始处理日期: {date_str}，共 {len(files)} 个文件")

        for idx, obj in enumerate(files, 1):
            filename = Path(obj.object_name).name
            local_path = date_dir / filename
            total = len(files)

            if local_path.exists() and local_path.stat().st_size == obj.size:
                total_success += 1
                completed += 1
                # 写入日志：已存在跳过
                write_log(f"[{date_str}] {idx}/{total} 已存在，跳过: {filename}")
            else:
                try:
                    client.fget_object(BUCKET, obj.object_name, str(local_path))
                    total_success += 1
                    completed += 1
                    # 写入日志：下载完成
                    write_log(f"[{date_str}] {idx}/{total} 下载完成: {filename}")
                except Exception as e:
                    total_failed += 1
                    completed += 1
                    log.error(f"下载失败 {filename}: {e}")
                    # 写入日志：下载失败
                    write_log(f"[{date_str}] {idx}/{total} 下载失败: {filename} - {e}")

            # 更新进度回调（每1%更新一次）
            if total_files > 0:
                percent = int(completed / total_files * 100)
                if percent != last_percent or completed == total_files:
                    last_percent = percent
                    progress_msg = f"下载进度: {percent}% ({completed}/{total_files})"
                    if progress_callback:
                        progress_callback(completed, total_files, progress_msg)

        current += timedelta(days=1)

    log.info(f"全部完成: 总成功 {total_success}, 总失败 {total_failed}")
    write_log(f"========== 下载完成！成功 {total_success} 个，失败 {total_failed} 个 ==========")

    return {
        "success": total_success,
        "failed": total_failed,
        "output_dir": str(output_dir),
        "dates": dates,
    }
import zipfile
import tempfile
import shutil

def download_and_pack(
    start_date: str,
    end_date: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[Optional[str], int, int]:
    """
    下载图片并打包成 ZIP 文件
    返回: (zip文件路径, 成功数, 失败数)
    """
    # 创建临时目录下载图片
    temp_dir = Path(tempfile.mkdtemp())
    download_dir = temp_dir / "shenglong_images"
    
    # 下载图片到临时目录
    result = download_images_with_progress(start_date, end_date, output_dir=str(download_dir))
    
    if result.get("error") or result['success'] == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, 0, 0
    
    # 保存到 Gradio 的静态目录（项目下的 tmp 文件夹）
    tmp_dir = Path.cwd() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    
    zip_name = f"shenglong_images_{start_date}_{end_date}.zip"
    zip_path = tmp_dir / zip_name
    
    # 如果文件已存在，先删除
    if zip_path.exists():
        zip_path.unlink()
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for date_str in result['dates']:
            date_dir = download_dir / date_str
            if date_dir.exists():
                for img_file in date_dir.glob("*.*"):
                    arcname = f"{date_str}/{img_file.name}"
                    zf.write(img_file, arcname)
    
    # 删除临时目录
    shutil.rmtree(temp_dir, ignore_errors=True)
    
    return str(zip_path), result['success'], result['failed']