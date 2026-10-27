#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from normalize_text_table import parse_text_table

TIME_KEYWORDS = ("日期", "时间", "月份", "季度", "年度", "date", "time", "month", "quarter", "year")
PERCENT_KEYWORDS = ("率", "占比", "百分比", "%", "accuracy", "precision", "recall")
CURRENCY_KEYWORDS = ("成本", "金额", "费用", "元", "万元", "price", "cost", "revenue")
DURATION_KEYWORDS = ("周期", "时长", "耗时", "天", "小时", "分钟", "duration", "cycle", "latency")
VERSION_KEYWORDS = ("版本", "版次", "version", "ver", "release")
TARGET_KEYWORDS = ("目标", "基准", "target", "goal", "benchmark")
UPPER_KEYWORDS = ("上限", "上界", "最高", "max", "upper", "high")
LOWER_KEYWORDS = ("下限", "下界", "最低", "min", "lower", "low")
AVERAGE_KEYWORDS = ("平均", "均值", "avg", "mean")
REDUCTION_KEYWORDS = ("成本", "周期", "误差", "耗时", "cost", "cycle", "latency", "error")
IMPROVEMENT_KEYWORDS = ("准确率", "识别率", "稳定率", "命中率", "accuracy", "stability")


def load_dataframe(input_value: str, input_format: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if os.path.exists(input_value):
        path = Path(input_value)
        fmt = input_format if input_format != "auto" else path.suffix.lower().lstrip(".")
        metadata = {"input_kind": "file", "source_path": str(path), "source_format": fmt}
        if fmt in ("xlsx", "xlsm"):
            return pd.read_excel(path), metadata
        if fmt == "xls":
            converted = convert_xls(path)
            metadata["converted_from_xls"] = str(converted)
            return pd.read_excel(converted), metadata
        if fmt == "csv":
            return pd.read_csv(path), metadata
        if fmt == "json":
            return json_to_dataframe(path.read_text(encoding="utf-8")), metadata
        if fmt in ("md", "markdown", "txt", "text"):
            text = path.read_text(encoding="utf-8")
            return text_to_dataframe(text), metadata
        raise ValueError(f"Unsupported file format: {fmt}")

    suspected_path = Path(input_value)
    if suspected_path.suffix.lower() in {".xlsx", ".xls", ".xlsm", ".csv", ".json", ".md", ".markdown", ".txt"}:
        raise FileNotFoundError(f"Input file not found: {input_value}")

    metadata = {"input_kind": "inline", "source_format": input_format}
    if input_format in ("json", "auto") and input_value.strip().startswith(("{", "[")):
        return json_to_dataframe(input_value), metadata
    return text_to_dataframe(input_value), metadata


def convert_xls(path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="graphing-xls-"))
    cmd = [
        "/opt/homebrew/bin/soffice",
        "--headless",
        "--convert-to",
        "xlsx",
        "--outdir",
        str(temp_dir),
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    converted = temp_dir / f"{path.stem}.xlsx"
    if not converted.exists():
        raise FileNotFoundError(f"Converted xlsx not found for {path}")
    return converted


def json_to_dataframe(text_or_path: Any) -> pd.DataFrame:
    if isinstance(text_or_path, Path):
        payload = json.loads(text_or_path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(str(text_or_path))
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        if all(isinstance(value, list) for value in payload.values()):
            return pd.DataFrame(payload)
        if "data" in payload and isinstance(payload["data"], list):
            return pd.DataFrame(payload["data"])
        return pd.DataFrame([payload])
    raise ValueError("JSON payload is not tabular")


def text_to_dataframe(text: str) -> pd.DataFrame:
    header, rows, _parser_name = parse_text_table(text)
    return pd.DataFrame(rows, columns=header)


def normalize_column_name(name: Any) -> str:
    text = str(name).strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text)
    return text.strip("_").lower() or "column"


def contains_keyword(name: str, keywords: Tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def infer_column_type(name: str, series: pd.Series) -> str:
    non_null = [str(value).strip() for value in series.tolist() if pd.notna(value) and str(value).strip()]
    lowered = name.lower()
    if contains_keyword(lowered, TIME_KEYWORDS):
        return "time"
    if contains_keyword(lowered, TARGET_KEYWORDS):
        return "target"
    if contains_keyword(lowered, UPPER_KEYWORDS):
        return "upper_bound"
    if contains_keyword(lowered, LOWER_KEYWORDS):
        return "lower_bound"
    if contains_keyword(lowered, AVERAGE_KEYWORDS):
        return "average"
    if contains_keyword(lowered, VERSION_KEYWORDS):
        return "version"
    if contains_keyword(lowered, CURRENCY_KEYWORDS):
        return "currency"
    if contains_keyword(lowered, DURATION_KEYWORDS):
        return "duration"

    if not non_null:
        return "category"

    parsed_dates = 0
    numeric_like = 0
    percent_like = 0
    for value in non_null[:50]:
        if re.search(r"\d+(\.\d+)?\s*%", value):
            percent_like += 1
        try:
            pd.to_datetime(value)
            parsed_dates += 1
        except Exception:
            pass
        try:
            float(clean_numeric_text(value))
            numeric_like += 1
        except Exception:
            pass

    threshold = max(1, math.ceil(len(non_null[:50]) * 0.6))
    if parsed_dates >= threshold:
        return "time"
    if contains_keyword(lowered, PERCENT_KEYWORDS) or percent_like >= threshold:
        return "percent"
    if numeric_like >= threshold:
        return "numeric"
    return "category"


def clean_numeric_text(value: Any) -> str:
    text = str(value).strip().replace(",", "")
    text = text.replace("％", "%")
    text = text.replace("万元", "")
    text = text.replace("元", "")
    text = text.replace("天", "")
    text = text.replace("小时", "")
    text = text.replace("分钟", "")
    text = text.replace("%", "")
    return text


def to_number(value: Any, field_type: str) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = clean_numeric_text(value)
    if text == "":
        return None
    try:
        number = float(text)
        if field_type == "percent":
            return number
        return number
    except ValueError:
        return None


def infer_roles(df: pd.DataFrame, field_types: Dict[str, str]) -> Dict[str, Any]:
    columns = list(df.columns)
    numeric_columns = [col for col in columns if field_types[col] in ("numeric", "percent", "currency", "duration", "target", "upper_bound", "lower_bound", "average")]
    category_columns = [col for col in columns if field_types[col] in ("category", "version")]
    time_columns = [col for col in columns if field_types[col] == "time"]
    target_columns = [col for col in columns if field_types[col] == "target"]

    x_axis = time_columns[0] if time_columns else (category_columns[0] if category_columns else columns[0])
    series_field = None
    primary_value = None
    value_columns = [col for col in numeric_columns if col != x_axis]

    if len(category_columns) >= 2:
        for candidate in category_columns:
            if candidate == x_axis:
                continue
            distinct = df[candidate].nunique(dropna=True)
            if 1 < distinct <= max(8, len(df) // 2):
                series_field = candidate
                break

    if not value_columns and x_axis in numeric_columns and len(numeric_columns) > 1:
        value_columns = [col for col in numeric_columns if col != x_axis]
    if value_columns:
        primary_value = next((col for col in value_columns if field_types[col] not in ("target", "upper_bound", "lower_bound")), value_columns[0])

    return {
        "x_axis": x_axis,
        "series_field": series_field,
        "value_columns": value_columns,
        "primary_value": primary_value,
        "target_columns": target_columns,
        "label_field": x_axis,
    }


def detect_relationships(df: pd.DataFrame, field_types: Dict[str, str], roles: Dict[str, Any]) -> Dict[str, bool]:
    x_axis = roles["x_axis"]
    value_columns = roles["value_columns"]
    series_field = roles["series_field"]
    range_fields = [col for col, ftype in field_types.items() if ftype in ("upper_bound", "lower_bound")]
    average_fields = [col for col, ftype in field_types.items() if ftype == "average"]

    return {
        "time_series": field_types.get(x_axis) == "time",
        "multi_series": bool(series_field) or len([col for col in value_columns if field_types[col] not in ("target", "upper_bound", "lower_bound", "average")]) > 1,
        "grouped_comparison": field_types.get(x_axis) in ("category", "version") and (bool(series_field) or len(value_columns) > 1),
        "target_attainment": bool(roles["target_columns"]),
        "range_present": bool(range_fields) and bool(average_fields or roles["primary_value"]),
        "dual_metric": len([col for col in value_columns if field_types[col] in ("numeric", "percent", "currency", "duration")]) == 2,
    }


def compute_key_points(df: pd.DataFrame, field_types: Dict[str, str], roles: Dict[str, Any]) -> List[Dict[str, Any]]:
    primary_value = roles["primary_value"]
    x_axis = roles["x_axis"]
    if not primary_value:
        return []

    points = []
    values = []
    x_values = []
    for _, row in df.iterrows():
        value = to_number(row[primary_value], field_types[primary_value])
        if value is None:
            continue
        values.append(value)
        x_values.append(row[x_axis])
    if len(values) < 2:
        return []

    def make_point(label: str, index: int, value: float) -> Dict[str, Any]:
        return {"kind": label, "x": stringify_value(x_values[index]), "value": round(value, 4)}

    points.append(make_point("start", 0, values[0]))
    points.append(make_point("end", len(values) - 1, values[-1]))
    points.append(make_point("max", values.index(max(values)), max(values)))
    points.append(make_point("min", values.index(min(values)), min(values)))

    target_column = roles["target_columns"][0] if roles["target_columns"] else None
    if target_column:
        targets = [to_number(row[target_column], field_types[target_column]) for _, row in df.iterrows()]
        for idx, (value, target) in enumerate(zip(values, targets)):
            if target is not None and value >= target:
                points.append(make_point("first_target_reached", idx, value))
                break

    deltas = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
    if len(deltas) >= 2:
        max_turn = max(range(1, len(deltas)), key=lambda idx: abs(deltas[idx] - deltas[idx - 1]))
        points.append(make_point("turning_point", max_turn, values[max_turn]))
        delta_mean = sum(abs(delta) for delta in deltas) / len(deltas)
        recent = deltas[-3:] if len(deltas) >= 3 else deltas
        if recent and max(abs(delta) for delta in recent) <= max(0.5, delta_mean * 0.4):
            points.append({
                "kind": "stable_region",
                "x": f"{stringify_value(x_values[-len(recent) - 1])}~{stringify_value(x_values[-1])}",
                "value": round(values[-1], 4),
            })
        large_delta = max(abs(delta) for delta in deltas)
        if delta_mean > 0 and large_delta >= delta_mean * 2.5:
            anomaly_index = max(range(1, len(values)), key=lambda idx: abs(values[idx] - values[idx - 1]))
            points.append(make_point("anomaly", anomaly_index, values[anomaly_index]))

    unique = []
    seen = set()
    for point in points:
        key = (point["kind"], point["x"], point["value"])
        if key not in seen:
            seen.add(key)
            unique.append(point)
    priority = ["start", "end", "first_target_reached", "turning_point", "max", "min", "stable_region", "anomaly"]
    unique.sort(key=lambda item: priority.index(item["kind"]) if item["kind"] in priority else len(priority))
    return unique[:6]


def stringify_value(value: Any) -> str:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    return str(value)


def point_label(kind: str) -> str:
    mapping = {
        "start": "起点",
        "end": "终点",
        "max": "最高点",
        "min": "最低点",
        "first_target_reached": "首次达标点",
        "turning_point": "拐点",
        "stable_region": "稳定区间",
        "anomaly": "异常波动点",
    }
    return mapping.get(kind, kind)


def summarize_task(df: pd.DataFrame, field_types: Dict[str, str], roles: Dict[str, Any], relationships: Dict[str, bool]) -> Tuple[str, List[str]]:
    primary_value = roles["primary_value"]
    if not primary_value:
        return "这组数据缺少可用于图表表达的核心数值指标。", ["clarification"]

    metric_name = str(primary_value)
    x_axis = str(roles["x_axis"])
    tags = []
    is_reduction = contains_keyword(metric_name, REDUCTION_KEYWORDS)
    is_improvement = contains_keyword(metric_name, IMPROVEMENT_KEYWORDS) or field_types.get(primary_value) == "percent"

    if relationships["range_present"]:
        tags.extend(["range", "stability"])
        summary = f"这是一个围绕{metric_name}波动区间和中心趋势的范围展示任务。"
    elif relationships["grouped_comparison"]:
        tags.append("comparison")
        if relationships["dual_metric"]:
            tags.append("dual-metric")
            summary = f"这是一个围绕{x_axis}展示双指标对比关系的对比任务。"
        else:
            summary = f"这是一个围绕{x_axis}展示不同对象在{metric_name}上差异的对比任务。"
    elif relationships["time_series"]:
        tags.append("trend")
        if relationships["target_attainment"]:
            tags.append("target-attainment")
        if is_reduction:
            tags.append("reduction")
            summary = f"这是一个{metric_name}随{x_axis}变化持续优化下降的趋势展示任务。"
        elif is_improvement:
            tags.extend(["improvement", "stabilization"])
            summary = f"这是一个{metric_name}随{x_axis}变化持续提升并趋于稳定的趋势展示任务。"
        else:
            summary = f"这是一个围绕{metric_name}随{x_axis}变化趋势的展示任务。"
    else:
        tags.append("comparison")
        summary = f"这是一个围绕{metric_name}展示不同类别差异的对比任务。"
    return summary, tags


def build_conclusion(df: pd.DataFrame, field_types: Dict[str, str], roles: Dict[str, Any], relationships: Dict[str, bool]) -> str:
    primary_value = roles["primary_value"]
    if not primary_value:
        return "当前数据需要先补充明确的指标字段后再生成图表。"
    x_axis = roles["x_axis"]
    series = [to_number(value, field_types[primary_value]) for value in df[primary_value].tolist()]
    series = [value for value in series if value is not None]
    if len(series) < 2:
        return f"{primary_value} 数据点不足，暂不建议直接生成正式汇报图表。"

    start = series[0]
    end = series[-1]
    change = end - start
    direction = "提升" if change >= 0 else "下降"
    if relationships["grouped_comparison"]:
        return f"建议重点突出不同对象在{primary_value}上的差异，尤其是最大差异点和最终结果点。"
    if relationships["range_present"]:
        return f"建议重点突出{primary_value}的波动区间、平均水平以及是否进入稳定区间。"
    return f"建议重点突出{primary_value}在{x_axis}维度上的整体{direction}趋势，以及起点、终点和关键转折点。"


def choose_chart_plans(df: pd.DataFrame, field_types: Dict[str, str], roles: Dict[str, Any], relationships: Dict[str, bool], key_points: List[Dict[str, Any]], summary: str, tags: List[str]) -> List[Dict[str, Any]]:
    x_axis = roles["x_axis"]
    primary_value = roles["primary_value"]
    series_field = roles["series_field"]
    conclusion = build_conclusion(df, field_types, roles, relationships)
    styles = {
        "hide_legend": not relationships["multi_series"],
        "hide_axis_titles": True,
        "hide_gridlines": True,
        "emphasize_key_labels": True,
    }

    plans = []
    if relationships["range_present"]:
        plans.append(make_plan(
            plan_id="range-lines",
            chart_type="range_lines",
            chart_name="上下界 + 均值折线图",
            rationale="数据中存在上下界或平均值字段，使用多序列折线图可以在同一坐标系内表达波动区间和中心趋势。",
            expression="适合表达误差范围、波动区间和稳定性结论。",
            x_axis=x_axis,
            y_axis=primary_value,
            series=series_field or "多序列指标",
            key_points=key_points,
            style={**styles, "hide_legend": False},
            conclusion=conclusion,
            tags=tags,
        ))
    elif relationships["grouped_comparison"] and relationships["multi_series"]:
        chart_type = "clustered_column" if not relationships["time_series"] else "multi_line"
        chart_name = "分组柱状图" if chart_type == "clustered_column" else "多折线图"
        plans.append(make_plan(
            plan_id="comparison-primary",
            chart_type=chart_type,
            chart_name=chart_name,
            rationale="数据以多个对象或多个序列展开，使用并列比较图能直接体现差异和排序。",
            expression="适合表达不同模型、版本、工厂或项目之间的指标对比。",
            x_axis=x_axis,
            y_axis=primary_value,
            series=series_field or ",".join(roles["value_columns"]),
            key_points=key_points,
            style={**styles, "hide_legend": False},
            conclusion=conclusion,
            tags=tags,
        ))
        plans.append(make_plan(
            plan_id="comparison-alt",
            chart_type="multi_line",
            chart_name="多折线图",
            rationale="当需要同时观察多个对象的变化轨迹而不是单点差异时，多折线图更利于观察趋势分化。",
            expression="适合表达多个方案在不同阶段的走势差别。",
            x_axis=x_axis,
            y_axis=primary_value,
            series=series_field or ",".join(roles["value_columns"]),
            key_points=key_points,
            style={**styles, "hide_legend": False},
            conclusion=conclusion,
            tags=tags,
        ))
    else:
        chart_type = "line_target" if relationships["target_attainment"] else "line_markers"
        chart_name = "折线图 + 目标线" if chart_type == "line_target" else "折线图（带关键节点）"
        plans.append(make_plan(
            plan_id="trend-primary",
            chart_type=chart_type,
            chart_name=chart_name,
            rationale="主指标沿横轴连续变化，折线图最适合表达上升、下降、收敛和阶段性变化。",
            expression="适合表达趋势提升、收敛稳定、周期缩短或成本降低等结论。",
            x_axis=x_axis,
            y_axis=primary_value,
            series=series_field or primary_value,
            key_points=key_points,
            style={**styles, "hide_legend": False} if chart_type == "line_target" else styles,
            conclusion=conclusion,
            tags=tags,
        ))
        if "reduction" in tags or "comparison" in tags:
            plans.append(make_plan(
                plan_id="trend-column",
                chart_type="clustered_column",
                chart_name="柱状图",
                rationale="如果更强调各阶段的数值差距而非连续性，可使用柱状图放大差异。",
                expression="适合表达阶段性对比、成本下降幅度和结果差异。",
                x_axis=x_axis,
                y_axis=primary_value,
                series=series_field or primary_value,
                key_points=key_points,
                style=styles,
                conclusion=conclusion,
                tags=tags,
            ))
        else:
            plans.append(make_plan(
                plan_id="trend-area",
                chart_type="area",
                chart_name="面积图",
                rationale="如果希望更强地表达累计提升或整体走势覆盖感，面积图是次优方案。",
                expression="适合表达趋势方向明确、序列较少的整体走势。",
                x_axis=x_axis,
                y_axis=primary_value,
                series=series_field or primary_value,
                key_points=key_points,
                style=styles,
                conclusion=conclusion,
                tags=tags,
            ))

    return plans[:3]


def make_plan(plan_id: str, chart_type: str, chart_name: str, rationale: str, expression: str, x_axis: str, y_axis: str, series: str, key_points: List[Dict[str, Any]], style: Dict[str, Any], conclusion: str, tags: List[str]) -> Dict[str, Any]:
    return {
        "plan_id": plan_id,
        "chart_type": chart_type,
        "chart_name": chart_name,
        "rationale": rationale,
        "expression_goal": expression,
        "axes": {"x_axis": x_axis, "y_axis": y_axis, "series": series},
        "key_points": key_points[:4],
        "style": style,
        "conclusion": conclusion,
        "task_tags": tags,
    }


def serialize_dataframe(df: pd.DataFrame) -> List[Dict[str, Any]]:
    records = []
    for _, row in df.fillna("").iterrows():
        records.append({str(key): stringify_value(value) for key, value in row.to_dict().items()})
    return records


def build_filename(summary: str, chart_type: str) -> str:
    if "趋势" in summary:
        prefix = "trend"
    elif "对比" in summary:
        prefix = "comparison"
    elif "范围" in summary or "区间" in summary:
        prefix = "range"
    elif "下降" in summary or "优化" in summary:
        prefix = "reduction"
    else:
        prefix = "chart"
    today = datetime.now().strftime("%Y%m%d")
    return f"{prefix}_{chart_type}_{today}.pptx"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--format", default="auto")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    df, input_meta = load_dataframe(args.input, args.format)
    df = df.dropna(how="all")
    df.columns = [str(column).strip() for column in df.columns]

    field_types = {column: infer_column_type(column, df[column]) for column in df.columns}
    roles = infer_roles(df, field_types)
    relationships = detect_relationships(df, field_types, roles)
    key_points = compute_key_points(df, field_types, roles)
    summary, tags = summarize_task(df, field_types, roles, relationships)
    chart_plans = choose_chart_plans(df, field_types, roles, relationships, key_points, summary, tags)
    clarification_needed = len(df) < 2 or not roles["primary_value"] or len(roles["value_columns"]) == 0
    clarification_questions = []
    if clarification_needed:
        if len(df) < 2:
            clarification_questions.append("数据行数不足，至少需要两行可比较数据。")
        if not roles["primary_value"]:
            clarification_questions.append("未识别出明确的核心数值指标，请说明哪一列是需要表达的数值。")
        if roles["x_axis"] == roles["primary_value"]:
            clarification_questions.append("当前无法区分横轴和数值列，请说明横轴字段。")

    payload = {
        "skill": "graphing-ppt-charts",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_meta": input_meta,
        "row_count": int(len(df)),
        "columns": [
            {
                "original_name": column,
                "normalized_name": normalize_column_name(column),
                "inferred_type": field_types[column],
            }
            for column in df.columns
        ],
        "field_roles": roles,
        "relationships": relationships,
        "key_points": key_points,
        "task_summary": summary,
        "task_tags": tags,
        "clarification_needed": clarification_needed,
        "clarification_questions": clarification_questions,
        "chart_plans": chart_plans,
        "recommended_plan_id": chart_plans[0]["plan_id"] if chart_plans else None,
        "confirmed_plan_id": None,
        "data_records": serialize_dataframe(df),
        "default_output_filename": build_filename(summary, chart_plans[0]["chart_type"] if chart_plans else "chart"),
        "confirmation_prompt": (
            f"我识别到{summary.rstrip('。')}，建议使用{chart_plans[0]['chart_name']}，"
            f"突出{'、'.join(point_label(point['kind']) for point in key_points[:3]) or '关键节点'}。"
            "图表将隐藏坐标轴标题，弱化网格线，并放大关键数据标签。是否按这个方案生成 PPT？"
            if chart_plans
            else "当前数据需要先澄清字段语义后再推荐图表方案。"
        ),
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
