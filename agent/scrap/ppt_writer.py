"""镔鑫废钢 → PPT 单页趋势图 适配层。

主入口 ``write_stats_pptx``：
  · 默认走 **自研 builder**（agent.scrap.ppt_builder.build_binxin_ppt）：
    - 单线主料识别率趋势 + 红色虚线目标线
    - 元信息卡（数据来源/周期/有效车次/错判车次）
    - KPI 卡（大数字 + 达标徽章）
    - 错判 Top 5 真实车牌表
    - 动态改进建议
    - 完全可控的版式
  · 失败时自动 fallback 到 **同事 skill**（agent.scrap.ppt/scripts/）：
    - subprocess 调 analyze_input + build_ppt_chart
    - 作为安全网，保证任何情况下都能输出一份文件
  · 也可以传 use_legacy=True 强制用同事 skill（A/B 对比时用）

设计选择：
  · 自研 builder = 镔鑫专属版面，字段、配色、目标线、错判表全部按业务最优解
  · 同事 skill = 通用模板，作为兜底
  · 这种"主路径 + fallback"组合既得了控制力，也保留了健壮性
"""
from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
import os
import site
import sys
import subprocess

from pathlib import Path
from typing import Iterable, List, Optional

from agent.scrap.models import DailyScrapStats

logger = logging.getLogger(__name__)

PPT_SKILL_ROOT: Path = Path(__file__).resolve().parent / "ppt"
ANALYZE_SCRIPT: Path = PPT_SKILL_ROOT / "scripts" / "analyze_input.py"
BUILD_SCRIPT: Path = PPT_SKILL_ROOT / "scripts" / "build_ppt_chart.py"


class PPTGenerationError(RuntimeError):
    """PPT 生成失败"""


# ---------------------------------------------------------------------------
# 公开主入口
# ---------------------------------------------------------------------------
def write_stats_pptx(
    stats_list: List[DailyScrapStats],
    save_path: Path,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    target_pct: float = 95.0,
    source_label: str = "镔鑫废钢检判系统（赛迪 AI vs 人工质检）",
    use_legacy: bool = False,
) -> Path:
    """根据日统计列表生成单页 PPT。

    主路径走自研 builder（agent.scrap.ppt_builder）；失败时 fallback 到同事 skill。

    Args:
        stats_list: 每日统计列表
        save_path: 输出 .pptx 路径
        start_date / end_date: 周期边界（可选；缺省时从 stats 取首尾日期）
        target_pct: 主料识别率目标值（默认 95%）
        source_label: 元信息卡显示的"数据来源"
        use_legacy: True = 强制走同事 skill（用于对比/兜底）

    Returns:
        save_path 的绝对路径

    Raises:
        PPTGenerationError: 数据不足 / 子进程失败
    """
    if not stats_list:
        raise PPTGenerationError("stats_list 为空，无法生成 PPT。")

    save_path = Path(save_path).resolve()

    # 自动推断周期边界
    valid_days = [s for s in stats_list if s.eligible_trucks > 0]
    if not valid_days:
        raise PPTGenerationError(
            "所有日期 eligible_trucks=0，没有可绘制的有效数据。"
        )
    sd = start_date or valid_days[0].date
    ed = end_date or valid_days[-1].date

    if len(valid_days) < 2:
        raise PPTGenerationError(
            f"有效日期数据不足 2 行（实得 {len(valid_days)} 行），趋势图无法绘制。"
        )

    # ---- 主路径：自研 builder ----
    if not use_legacy:
        try:
            from agent.scrap.ppt_builder import build_binxin_ppt

            return build_binxin_ppt(
                stats_list,
                save_path,
                start_date=sd,
                end_date=ed,
                source_label=source_label,
                target_pct=target_pct,
            )
        except Exception as e:
            logger.warning(
                "自研 builder 失败，尝试走 legacy 同事 skill: %s", e, exc_info=True
            )
            # 不直接抛异常，fall through 到 legacy

    # ---- Legacy 路径：同事 skill（subprocess）----
    return _legacy_write_stats_pptx(stats_list, save_path)


# ---------------------------------------------------------------------------
# Legacy 实现（同事 graphing-ppt-charts skill）
# ---------------------------------------------------------------------------
def _legacy_write_stats_pptx(
    stats_list: Iterable[DailyScrapStats],
    save_path: Path,
) -> Path:
    """走同事 skill 的旧实现，作为 fallback 保留。"""
    save_path = Path(save_path).resolve()
    work_dir = save_path.parent / ".ppt_workspace"
    csv_path = work_dir / "daily_stats.csv"
    plan_path = work_dir / "plan.json"

    n_rows = _build_csv(stats_list, csv_path)
    if n_rows < 2:
        raise PPTGenerationError(
            f"有效日期数据不足 2 行（实得 {n_rows} 行），趋势图无法绘制。"
        )
    logger.info("[legacy] 镔鑫日统计 csv: %s（%d 行）", csv_path, n_rows)

    plan = _run_analyze(csv_path, plan_path)
    plan_id = _confirm_plan(plan, plan_path)
    logger.info("[legacy] 使用推荐方案 plan_id=%s", plan_id)

    _run_build(plan_path, save_path)
    logger.info("[legacy] PPT 已生成: %s", save_path)
    return save_path


def _build_csv(stats_list: Iterable[DailyScrapStats], csv_path: Path) -> int:
    rows = 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "日期",
                "主料识别率(%)",
                "平均误差率(%)",
                "平均扣重差值(Kg)",
                "扣重占比",
            ]
        )
        for stats in stats_list:
            if stats.eligible_trucks <= 0:
                continue
            writer.writerow(
                [
                    stats.date,
                    f"{(stats.accuracy_rate or 0):.2f}",
                    (
                        f"{stats.avg_error_rate:.2f}"
                        if stats.avg_error_rate is not None
                        else ""
                    ),
                    (
                        f"{stats.avg_weight_diff:.2f}"
                        if stats.avg_weight_diff is not None
                        else ""
                    ),
                    (
                        f"{stats.avg_weight_ratio:.3f}"
                        if stats.avg_weight_ratio is not None
                        else ""
                    ),
                ]
            )
            rows += 1
    return rows


def _run_analyze(csv_path: Path, plan_path: Path) -> dict:
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ANALYZE_SCRIPT),
        "--input",
        str(csv_path),
        "--format",
        "csv",
        "--output",
        str(plan_path),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(ANALYZE_SCRIPT.parent)
    )
    if proc.returncode != 0:
        raise PPTGenerationError(
            f"analyze_input.py 失败 (exit={proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    with open(plan_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _confirm_plan(plan: dict, plan_path: Path) -> str:
    plan_id: Optional[str] = plan.get("recommended_plan_id")
    if not plan_id:
        raise PPTGenerationError(
            "analyze_input 未推荐图表方案；可能数据点不足或字段语义不清。"
        )
    plan["confirmed_plan_id"] = plan_id
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    return plan_id


def _run_build(plan_path: Path, pptx_path: Path) -> None:
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 获取主环境已安装依赖的路径
    current_site_packages = site.getsitepackages()
    env = os.environ.copy()
    
    # 强行塞入 PYTHONPATH 环境变量
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = os.pathsep.join(current_site_packages) + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = os.pathsep.join(current_site_packages)

    cmd = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--plan", str(plan_path),
        "--output", str(pptx_path),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(BUILD_SCRIPT.parent), env=env
    )
    if proc.returncode != 0:
        raise PPTGenerationError(f"build_ppt_chart.py 失败: \nstdout: {proc.stdout}\nstderr: {proc.stderr}")
