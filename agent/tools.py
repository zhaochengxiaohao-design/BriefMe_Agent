"""GLM function calling 工具定义"""
from __future__ import annotations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_daily_stats",
            "description": "查询某一天的钢卷打包带视觉检测统计数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "查询日期，格式 YYYY-MM-DD，例如 2026-04-15",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date_range_stats",
            "description": (
                "查询一段日期范围内每天的钢卷统计数据，"
                "适用于用户询问多天或一段时间的情况"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_abnormal_images",
            "description": (
                "下载指定日期中异常且已打数与应打数差值大于1的钢卷图片"
                "（原图+渲染图），用于人工审核。图片保存到本地 downloads 目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "下载哪天的异常图片，格式 YYYY-MM-DD",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "yongfeng_export_and_upload_report",
            "description": (
                "【永锋项目】先在本地生成指定日期范围的 xlsx 报表，再自动上传为腾讯文档在线表格，"
                "并默认移动到腾讯云空间文件夹 JOVGVfapiCoO。"
                "适用于用户希望直接把本地新报表同步到腾讯文档时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    # =========================================================
    # 废钢检判系统（scrap_ 前缀）- 与打包带项目完全隔离
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "scrap_get_daily_summary",
            "description": (
                "【废钢检判项目】查询某一天的废钢智能检判统计数据。"
                "返回固定 4 条文本格式：赛迪共检判X车、主料型准确率、"
                "料型占比误差率、扣重重量差值、扣重占比值。"
                "仅在用户提到【废钢/检判/赛迪/料型/扣重/扣杂/车牌/工位】等废钢项目关键词时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "查询日期，格式 YYYY-MM-DD，例如 2026-04-15",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_get_range_summary",
            "description": (
                "【废钢检判项目】查询多天废钢检判统计，按日输出 4 条文本汇总。"
                "适用于用户询问多天或一段时间的废钢检判情况。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_export_report",
            "description": (
                "【废钢检判项目】导出指定日期/日期范围的废钢检判统计 xlsx 报表，"
                "并下载主料型判错车次的渲染图供人工分析。"
                "报表按固定格式生成（13 列、三级表头、日期合并、错判行浅蓝填充）。"
                "图片按 downloads/scrap/<日期>/ 分目录保存。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD。若只查一天，start_date=end_date",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD",
                    },
                    "download_error_images": {
                        "type": "boolean",
                        "description": "是否下载错判渲染图，默认 true",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_export_ppt",
            "description": (
                "【废钢检判项目 · 仅镔鑫】根据指定日期范围的镔鑫废钢检判每日统计，"
                "生成 1 页带可编辑图表 + 文字结论的 PowerPoint 汇报页。"
                "图表默认聚焦【主料识别率】随日期变化的趋势，并附任务判断、"
                "图表结构、关键观察、使用建议四个文字面板。"
                "适用场景：用户在镔鑫已经看过统计文字/导出过 xlsx 报表后，"
                "进一步说"
                "【生成对应的 ppt / 生成 PPT / 做一页汇报图 / 出一页趋势图】"
                "等指令时使用。"
                "注意：此工具仅用于镔鑫，不要用于盛隆或打包带；至少需要 2 天有效数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_weekly_type_stats",
            "description": (
                "【废钢检判项目 · 仅镔鑫】统计镔鑫废钢指定日期范围内各主料型"
                "（精炉料1/2/3、重废、中废、杂模等）的周度检判数据，仅统计赛迪系统数据，"
                "生成 Excel 报表。同时返回赛迪对比汇总文本。"
                "适用场景：用户说"
                "【各料型统计 / 料型周报 / 分料型 / 周度料型 / 料型对比 / 料型统计+汇报】等指令时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    # =========================================================
    # 盛隆废钢检判系统（shenglong_ 前缀）- 与镔鑫/打包带完全隔离
    # 部署于盛隆钢铁现场，人工检判为 3 人平均
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "shenglong_get_daily_summary",
            "description": (
                "【盛隆废钢检判项目】查询某一天的盛隆废钢智能检判统计数据。"
                "返回文本汇总：主料型识别率 R、扣杂符合率 两项核心指标。"
                "仅在用户明确提到【盛隆】关键词时使用；若只说【废钢】未指定"
                "镔鑫/盛隆时，必须反问用户，严禁误走此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "查询日期，格式 YYYY-MM-DD，例如 2026-04-22",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shenglong_get_range_summary",
            "description": (
                "【盛隆废钢检判项目】查询多天盛隆废钢检判统计，按日文本汇总。"
                "适用于用户询问多天或一段时间的盛隆废钢检判情况。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，格式 YYYY-MM-DD",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shenglong_export_report",
            "description": (
                "【盛隆废钢检判项目】导出指定日期/日期范围的盛隆废钢检判统计 xlsx 报表。"
                "报表含三个 sheet：Sheet1「统计周期概括」(识别准确率 / 扣重符合率 / "
                "价格差异分布，无环比)，Sheet2「累计统计」(截图式总览 + Tol 合计)，"
                "Sheet3「检判统计详情」(37 列单车明细)。"
                "如果用户给出『上周期』『上次』『环比』『对比上周期』等线索，"
                "把上周期日期范围分别填到 prev_start_date / prev_end_date，"
                "Sheet1 会自动写入上周期数值并算出环比公式。"
                "若用户只让看本周期，则 prev_* 留空。"
                "试运行阶段不下载图像。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "本周期起始日期 YYYY-MM-DD。若只查一天，start_date=end_date",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "本周期结束日期 YYYY-MM-DD",
                    },
                    "prev_start_date": {
                        "type": "string",
                        "description": (
                            "上周期起始日期 YYYY-MM-DD（可选）；"
                            "提供后会写入 Sheet1 F/G 列做环比对比，不提供则 F/G 留空"
                        ),
                    },
                    "prev_end_date": {
                        "type": "string",
                        "description": "上周期结束日期 YYYY-MM-DD（可选；与 prev_start_date 配套）",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shenglong_export_master_report",
            "description": (
                "【盛隆废钢检判项目 · 多周期主表】生成包含多个统计周期的"
                "盛隆主表 xlsx：Sheet1「统计周期概括」每个周期一个 14 行块依次"
                "往下排（A 列 1/2/3...，环比自动取上一个周期的实际值）；"
                "Sheet2「累计统计」汇总各期有效车次、正确车次、识别率和扣重符合率；"
                "Sheet3「检判统计详情」每个周期独立一段（深蓝段标题 + 三级"
                "表头 + 单车明细 + 期间汇总）。"
                "适用场景：用户说"
                "【盛隆主表 / 盛隆总表 / 历史主表 / 全部周期一起 / "
                "把所有周期累积起来】等指令。"
                "周期顺序按 cycles 数组顺序写入（建议传入时按时间升序）；"
                "首期没有上周期数据，从第二期起自动建立环比链。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cycles": {
                        "type": "array",
                        "description": (
                            "统计周期列表，按时间升序排列；至少 1 项。"
                            "每个 cycle 表示最终 Excel 中的一个统计周期。"
                            "普通周期写成 {ranges:[{start_date,end_date}]}。"
                            "只有当用户明确说『把 X 和 Y 当作同一个统计周期 / 合并统计』时，"
                            "才把 X、Y 两个日期段放进同一个 ranges 数组。"
                            "如果用户只是用顿号/逗号列出多个日期段，则每个日期段必须独立成一个 cycle，"
                            "禁止自动合并相邻日期段。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "ranges": {
                                    "type": "array",
                                    "description": (
                                        "组成同一个统计周期的一个或多个日期段。"
                                        "没有明确合并语义时，ranges 只能有 1 个日期段。"
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start_date": {
                                                "type": "string",
                                                "description": "日期段起始日期 YYYY-MM-DD",
                                            },
                                            "end_date": {
                                                "type": "string",
                                                "description": "日期段结束日期 YYYY-MM-DD",
                                            },
                                        },
                                        "required": ["start_date", "end_date"],
                                    },
                                },
                            },
                            "required": ["ranges"],
                        },
                    },
                },
                "required": ["cycles"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shenglong_export_heavy_master_report",
            "description": (
                "【盛隆废钢检判项目 · 重废1/2/3归一化口径多周期主表】"
                "生成包含多个统计周期的盛隆主表 xlsx。与普通 "
                "shenglong_export_master_report 的周期合并、环比、累计、扣重符合率、"
                "价格分布逻辑完全一致；唯一差异是 Sheet1 的识别准确率口径："
                "只看重废1/重废2/重废3，先把人工和 AI 各自的这三类占比归一化到 100%，"
                "再判断归一化后的主重废类是否相同且占比差异是否 ≤10%。"
                "准确率统计时明确排除人工检判结果中没有任意重废1/2/3料型的车次；"
                "AI 无重废1/2/3时也无法对比，不进入该准确率分母。"
                "用户明确说【重废1/2/3、重废归一化、只算重废、新准确率口径、"
                "把其他料型折算到重废】时使用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cycles": {
                        "type": "array",
                        "description": (
                            "统计周期列表，按时间升序排列；至少 1 项。"
                            "每个 cycle 表示最终 Excel 中的一个统计周期。"
                            "普通周期写成 {ranges:[{start_date,end_date}]}。"
                            "只有当用户明确说『把 X 和 Y 当作同一个统计周期 / 合并统计』时，"
                            "才把 X、Y 两个日期段放进同一个 ranges 数组。"
                            "如果用户只是用顿号/逗号列出多个日期段，则每个日期段必须独立成一个 cycle，"
                            "禁止自动合并相邻日期段。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "ranges": {
                                    "type": "array",
                                    "description": (
                                        "组成同一个统计周期的一个或多个日期段。"
                                        "没有明确合并语义时，ranges 只能有 1 个日期段。"
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start_date": {
                                                "type": "string",
                                                "description": "日期段起始日期 YYYY-MM-DD",
                                            },
                                            "end_date": {
                                                "type": "string",
                                                "description": "日期段结束日期 YYYY-MM-DD",
                                            },
                                        },
                                        "required": ["start_date", "end_date"],
                                    },
                                },
                            },
                            "required": ["ranges"],
                        },
                    },
                    "mat_code_1": {"type": "string", "description": "料号 1，默认 12031001"},
                    "mat_code_2": {"type": "string", "description": "料号 2，默认 12031002"},
                    "start_time": {"type": "string", "description": "起始时间，格式 YYYY-MM-DD HH:MM:SS"},
                    "end_time": {"type": "string", "description": "结束时间，格式 YYYY-MM-DD HH:MM:SS"},
                    "output": {"type": "string", "description": "输出 xlsx 路径（可选）"},
                    "verbose": {"type": "boolean", "description": "是否输出更详细日志，默认 false"},
                },
                "required": ["analysis_base_url", "visual_1_base_url", "visual_2_base_url", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "yongfeng_export_accuracy_report",
            "description": (
                "【永锋烧结矿统计】生成指定日期范围的烧结矿颗粒度人工筛分 vs 视觉准确率报表。"
                "该报表逻辑与原始脚本一致：按人工筛分样本时间对齐视觉数据，"
                "对每条样本取 (T-4h, T] 窗口内视觉记录均值，再计算各粒径区间误差和每行 MAE，"
                "最后导出 Excel 报表。"
                "适用于用户说【生成 2026-04-01 到 2026-04-07 的烧结矿颗粒度准确率报表】、"
                "【导出某一段时间的人工筛分 vs 视觉准确率】、"
                "【做永锋烧结矿准确率统计】等指令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "起始时间，格式 YYYY-MM-DD HH:MM:SS",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间，格式 YYYY-MM-DD HH:MM:SS",
                    },
                    "mat_code_1": {
                        "type": "string",
                        "description": "料号 1，默认 12031001",
                    },
                    "mat_code_2": {
                        "type": "string",
                        "description": "料号 2，默认 12031002",
                    },
                    "output": {
                        "type": "string",
                        "description": "输出 xlsx 路径（可选）",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "是否输出更详细日志，默认 false",
                    },
                },
                "required": ["start_time", "end_time"],
            },
        },
    },
        # 盛隆图像下载工具（只走 3000 业务系统，不走 MinIO）
    {
        "type": "function",
        "function": {
            "name": "download_shenglong_images",
            "description": (
                "【盛隆检判原图下载】从盛隆废钢智能质检系统（172.16.16.101:3000）"
                "下载指定日期、多个不连续日期、或连续日期区间的「智能判级照片」原图。"
                "用户用顿号/逗号列出的多个日期必须放进 dates，不要当成连续区间补中间天。"
                "用户说 A 到 B 时用 start_date/end_date。"
                "必须使用用户给出的本地保存路径；没有路径时 output_dir 传空字符串。"
                "不要走 MinIO。下载前请确保盛隆 VPN 已连接。"
                "每个日期的车次文件夹下完后会原样 scp 到推理测试机；scp 失败只记总结，不中断下载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD；单日或连续区间时填写"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD；单日时与 start_date 相同"
                    },
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "不连续的多个日期，如 [\"2026-08-01\",\"2026-08-03\"]。有此字段时按列表下载，不补中间天"
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "本机保存目录的绝对路径。必须从用户消息或页面「保存路径」提取，例如 /Users/xxx/Desktop/盛隆图像"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pack_shenglong_multilabel",
            "description": (
                "【盛隆废钢多标签分类数据集打包】仅在用户人工筛完不合格图、并明确确认打包后调用。"
                "扫描保存目录里各日期/各车次还剩下的原图，生成「废钢多标签分类数据集.zip」。"
                "不要在下载刚结束时自动调用。没有保存路径时 output_dir 传空字符串。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "description": "本机保存目录，须与下载时同一路径",
                    },
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。只打包这些日期；不填则打包目录下全部日期文件夹",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_yongfeng_images",
            "description": (
                "【永锋检判原图下载】从永锋废钢智能质检系统"
                "（http://vision.lg.china-yongfeng.com/srape-steel）"
                "下载指定日期、多个不连续日期、或连续日期区间的「智能判级照片」原图。"
                "用户用顿号/逗号列出的多个日期必须放进 dates，不要当成连续区间补中间天。"
                "用户说 A 到 B 时用 start_date/end_date。"
                "必须使用用户给出的本地保存路径；没有路径时 output_dir 传空字符串。"
                "车次文件夹和原图文件名用详情页废钢主料型（重废Ⅰ/Ⅱ/Ⅲ、统料、统料Ⅰ/Ⅱ），"
                "不写占比。不要走盛隆 3000、不要走烧结矿报表、不要引用盛隆字典。"
                "下载前请确保永锋 VPN（与打包带同一专网）已连接。"
                "未配置 scp 则跳过；scp 失败只记总结，不中断下载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD；单日或连续区间时填写"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD；单日时与 start_date 相同"
                    },
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "不连续的多个日期，如 [\"2026-08-31\",\"2026-09-01\"]。有此字段时按列表下载，不补中间天"
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "本机保存目录的绝对路径。必须从用户消息或页面「保存路径」提取"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "yongfeng_align_scrap_manual",
            "description": (
                "【永锋废钢视觉/人工对齐】把用户选取的本地人工检判 xlsx，"
                "和永锋废钢质检实绩（srape-steel 检判列表）按同一天、同一车牌对齐。"
                "视觉侧只取已经质检完成的车次，写入扣重扣杂总计，以及扣杂、厚度扣重的重量、占比、说明。"
                "人工表里对不上的车次记为「未检判」。"
                "用户说【永锋】+【人工检判/人工判定/质检实绩/扣重扣杂】且要对齐、对照或筛选时使用。"
                "不要用于烧结矿，不要用于检判原图下载，不要用于镔鑫或盛隆。"
                "manual_file 填用户消息里的 xlsx 绝对路径；界面已选文件但消息里没写路径时传空字符串。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "manual_file": {
                        "type": "string",
                        "description": "本地人工检判 xlsx 绝对路径。界面已选择且消息没写路径时传空字符串",
                    },
                    "output": {
                        "type": "string",
                        "description": "可选。输出 xlsx 路径或目录；留空则写到 downloads/yongfeng",
                    },
                },
                "required": ["manual_file"],
            },
        },
    },
    # =========================================================
    # 镔鑫球机图像下载+重命名（bxsteel_ 前缀）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "bxsteel_download_images",
            "description": (
                "【镔鑫球机图像下载+重命名】从镔鑫废钢智能检判系统下载指定日期或日期区间的"
                "球机原图，并按人工判级结果重命名落盘。默认下载昨天的数据。"
                "需要用户提供镔鑫系统的登录凭证（工号+密码）。"
                "所有参数（日期、工号、密码、下载目录）都必须从用户的自然语言消息中提取。"
                "工号和密码是必填的，若用户未提供则必须反问。"
                "适用场景：用户说"
                "【下载镔鑫球机图像 / 下载昨天的球机图像 / 镔鑫球机图像下载 / "
                "球机原图下载 / 下载镔鑫车辆图片】等指令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD。若只下载一天，start_date=end_date。默认为昨天。"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD"
                    },
                    "username": {
                        "type": "string",
                        "description": "镔鑫系统登录工号（必填），从用户消息中提取，如 022499"
                    },
                    "password": {
                        "type": "string",
                        "description": "镔鑫系统登录密码（必填），从用户消息中提取"
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "下载目录路径（可选），用户不指定则使用默认目录"
                    },
                },
                "required": ["start_date", "end_date", "username", "password"],
            },
        },
    },
    # =========================================================
    # 用友检判结果图片下载（yongyou_ 前缀）
    # =========================================================
    {
        "type": "function",
        "function": {
            "name": "yongyou_download_images",
            "description": (
                "【用友检判结果图片下载】从用友智能判级系统下载指定日期或日期区间的"
                "检判结果原始图片（包括原图、判级识别图、危险物图、夹杂物图、饼图）。"
                "默认下载昨天的数据。需要用户提供用友系统的登录凭证（账号+密码）。"
                "适用场景：用户说"
                "【下载用友检判结果图 / 用友图片下载 / 用友检判图 / "
                "下载用友质检判级图】等指令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "起始日期 YYYY-MM-DD。若只下载一天，start_date=end_date。默认为昨天。"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期 YYYY-MM-DD"
                    },
                    "username": {
                        "type": "string",
                        "description": "用友系统登录账号（必填），从用户消息中提取"
                    },
                    "password": {
                        "type": "string",
                        "description": "用友系统登录密码（必填），从用户消息中提取"
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "下载目录路径（可选），用户不指定则使用默认目录"
                    },
                },
                "required": ["start_date", "end_date", "username", "password"],
            },
        },
    },
]
