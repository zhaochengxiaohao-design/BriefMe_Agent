"""Accuracy calculator matching the original script logic."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import logging
import pandas as pd

from .dict import BINS, WINDOW_HOURS

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=WINDOW_HOURS)


def infer_coverage(visual_df: pd.DataFrame) -> Tuple[datetime, datetime]:
    if visual_df.empty:
        raise ValueError("视觉数据为空, 无法推断覆盖范围")
    start = visual_df["time"].min().to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = visual_df["time"].max().to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_day + timedelta(days=1) - timedelta(seconds=1)
    return start, end


def compute_accuracy(raw: Dict[str, Any]) -> Dict[str, Any]:
    manual_1 = raw.get("manual_1", [])
    manual_2 = raw.get("manual_2", [])
    visual_1 = raw.get("visual_1", [])
    visual_2 = raw.get("visual_2", [])
    meta = raw.get("meta", {})
    rows_1, stats_1 = _align(manual_1, visual_1, label="1#")
    rows_2, stats_2 = _align(manual_2, visual_2, label="2#")
    return {"meta": meta, "1#": {"rows": rows_1, "stats": stats_1}, "2#": {"rows": rows_2, "stats": stats_2}}


def _align(manual_rows: List[Dict[str, Any]], visual_rows: List[Dict[str, Any]], label: str = "") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    manual_df = pd.DataFrame(manual_rows)
    visual_df = pd.DataFrame(visual_rows)
    stats = {
        "total_manual": len(manual_df),
        "out_of_range": 0,
        "no_visual": 0,
        "kept": 0,
        "coverage_start": None,
        "coverage_end": None,
    }
    if manual_df.empty or visual_df.empty:
        return [], stats

    manual_df["time"] = manual_df["time"].map(_parse_dt)
    visual_df["time"] = visual_df["time"].map(_parse_dt)
    manual_df = manual_df.sort_values("time").reset_index(drop=True)
    visual_df = visual_df.sort_values("time").reset_index(drop=True)
    coverage_start, coverage_end = infer_coverage(visual_df)
    stats["coverage_start"] = coverage_start.strftime("%Y/%m/%d %H:%M")
    stats["coverage_end"] = coverage_end.strftime("%Y/%m/%d %H:%M")

    vtime = visual_df["time"].values
    result: List[Dict[str, Any]] = []
    for _, row in manual_df.iterrows():
        t = row["time"]
        if pd.isna(t):
            continue
        ws = t - WINDOW
        window_end = t
        if ws < coverage_start or window_end > coverage_end:
            stats["out_of_range"] += 1
            continue
        mask = (vtime > pd.Timestamp(ws)) & (vtime <= pd.Timestamp(window_end))
        sub = visual_df.loc[mask]
        # 调试日志已移除，避免影响正式输出
        out = {"time": t.strftime("%Y/%m/%d %H:%M"), "n_visual": int(len(sub))}
        for b in BINS:
            out[f"manual_{b}"] = float(row[b])
            if sub.empty:
                out[f"visual_{b}"] = None
                out[f"err_{b}"] = None
            else:
                out[f"visual_{b}"] = float(sub[b].mean())
                out[f"err_{b}"] = abs(out[f"manual_{b}"] - out[f"visual_{b}"])
        if sub.empty:
            stats["no_visual"] += 1
            out["mae"] = None
        else:
            out["mae"] = sum(out[f"err_{b}"] for b in BINS) / len(BINS)
            stats["kept"] += 1
        result.append(out)
    return result, stats


def _parse_dt(v: Any) -> datetime:
    return pd.to_datetime(v).to_pydatetime()
