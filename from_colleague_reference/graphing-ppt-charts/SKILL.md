---

## name: graphing-ppt-charts
description: Analyze Excel, CSV, JSON, Markdown tables, and pasted structured text, infer the business charting task, recommend the best editable PowerPoint chart approach, and generate a one-slide native PPT chart after user confirmation. Use when Codex needs to turn tabular data into professional industrial-report charts, especially for trend, comparison, target attainment, cost/cycle reduction, or range/error presentations in editable `.pptx` form.

# Graphing PPT Charts

# 图表表达助手

将用户提供的数据表转化为“适合正式汇报的一页 PPT 图表表达方案”。先分析数据结构和表达目标，输出推荐图表方案并请求确认；只有在用户确认后，才生成 PowerPoint 原生可编辑图表。

## 默认定位

- 面向工业技术汇报、项目申报、产品宣传材料中的单页图表表达。
- 默认遵循 OopsOps 的 PPT 规则和金睛技术型页面风格。
- 默认主色 `#015BAC`，强调色 `#C60019`。
- 默认输出到 `/Users/gxxxxxxxf/Downloads/`。

生成结果必须是 PowerPoint 内置可编辑图表对象，不是图片。

## 输入类型

优先支持以下输入：

- Excel：`.xlsx`、`.xls`
- CSV：`.csv`
- JSON：对象数组、键值映射后可转表的常见结构
- Markdown 表格
- 用户直接粘贴的结构化文本
- 常见字段如日期、准确率、成本、周期、误差、版本、项目、工厂、算法等

如果输入是本地文件，优先把文件路径传给 `scripts/analyze_input.py`。如果输入是纯文本，直接作为 `--input` 内容传入即可。

## 工作流

### Step 1. 先分析，不直接生成

先运行：

```bash
/Users/gxxxxxxxf/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  skills/graphing-ppt-charts/scripts/analyze_input.py \
  --input "<path-or-inline>" \
  --format auto \
  --output tmp/graphing-analysis.json
```

分析脚本负责：

- 读取数据
- 识别字段与字段类型
- 推断横轴、纵轴、系列和关键标签
- 判断是否为趋势、对比、下降优化、目标达成、误差范围等表达任务
- 输出 1-3 个推荐图表方案
- 给出自然语言任务归纳
- 判断是否信息不足，需要先向用户澄清

如果返回 `clarification_needed: true`，先向用户提出缺失问题，不要继续生成 PPT。

### Step 2. 向用户确认方案

确认时固定覆盖以下信息：

- 识别出的任务类型
- 推荐图表类型
- 主要表达结论
- 横轴、纵轴、系列
- 关键标注点
- 样式策略

推荐话术模板：

> 我识别到这是一个……任务，建议使用……图表，突出……，并采用……样式。是否按这个方案生成 PPT？

未收到用户明确确认前，不生成 `.pptx`。

### Step 3. 用户确认后再生成

把已确认的方案写回分析 JSON 的 `confirmed_plan_id`，然后运行：

```bash
python3 skills/graphing-ppt-charts/scripts/build_ppt_chart.py \
  --plan tmp/graphing-analysis.json \
  --output "/Users/gxxxxxxxf/Downloads/<filename>.pptx"
```

生成脚本负责：

- 创建 16:9 单页 PPT
- 插入 PowerPoint 原生可编辑图表
- 默认让图表只占页面约 1/4 到 1/3 的核心区域
- 在剩余区域写入任务判断、图表结构、关键观察、使用建议，供汇报者参考
- 对“准确率/识别率/稳定率”收敛类趋势，优先切换到“快速收敛趋势图”分支

## 图表选择规则

读取前优先了解：

- `references/chart-selection.md`
- `references/visual-rules.md`

默认推荐逻辑：

- 趋势提升类：单折线图、折线图带 marker、折线图加目标线
- 多方案对比类：双折线图、多折线图、分组柱状图
- 成本/周期下降类：折线图或柱状图，突出下降幅度
- 误差范围类：上下界 + 均值多序列折线图
- 信息非常有限但需要表达方向时：简化趋势卡片式折线图

如果字段语义明显不足，例如：

- 没有数值列
- 只有一行数据
- 无法判断哪个字段是横轴
- 多个数值字段含义冲突且用户描述不足

则进入澄清分支，不要静默猜测。

## 输出约束

- V1 只做单页图表 PPT。
- 不做一页多图。
- 不做多页批量生成。
- 不做插入现有 PPT 的编辑流程。
- 不依赖图片渲染图表主体。
- 默认输出不是“只有图”的画面，而是“图表 + 分析结论”的参考页。

## 建议用法

以下请求应触发本技能：

- 根据这个 Excel 做一页准确率趋势图
- 把这组 CSV 数据转成 PPT 可编辑图表
- 分析这段 Markdown 表格并推荐适合汇报的图表
- 把这组成本、周期、准确率数据做成工业汇报页
- 根据 JSON 数据自动推荐图表并生成 PowerPoint 原生图表

## 运行说明

- 数据解析使用工作区 Python：
  - `/Users/gxxxxxxxf/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`
- PPT 生成使用系统 Python：
  - `python3`

这样可以同时利用：

- 工作区 Python 的 `pandas`、`openpyxl`
- 系统 Python 的 `python-pptx`

