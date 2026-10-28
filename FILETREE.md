# 🗂️ BriefMe · 项目文件树

> 多场景工业智能决策助手（Gradio Web UI + LLM 路由 + 多钢铁厂业务链路）
> 本文件为项目结构的"地图"，帮助快速定位代码；由人工维护，目录有大变动时请同步更新。
> 生成日期：2026-10-27

```text
BriefMe_Agent/
│
├── .github/
│   └── workflows/
│       └── ci.yml                     # GitHub Actions CI（pytest + flake8）
│
├── agent/                             # ★ 业务智能体核心包
│   ├── __init__.py
│   ├── core.py                        # SteelCoilAgent 主控：自然语言指令解析与业务路由
│   ├── llm_client.py                  # OpenAI 兼容 Chat Completions 客户端（DeepSeek，Function Calling）
│   ├── data_fetcher.py                # 永锋视觉系统 API 客户端（打包带 packing-tape）
│   ├── vpn_manager.py                 # VPN 连通性检测与半自动连接辅助
│   ├── image_download_route.py        # 检判原图 / 多标签数据集指令的路由判定
│   ├── tools.py                       # Agent 工具函数集
│   │
│   ├── bxsteel/                       # 镔鑫钢铁 · 废钢检判链路
│   │   ├── __init__.py
│   │   ├── config.py                  #   地址 / 账号 / token 缓存配置
│   │   ├── api_client.py              #   /fcs 系列接口 HTTP 客户端（登录/列表/详情）
│   │   ├── list_fetcher.py            #   检判任务列表分页拉取
│   │   ├── detail_fetcher.py          #   任务详情（flowCode → 明细）
│   │   ├── downloader.py              #   检判原图下载
│   │   ├── parser.py                  #   明细数据解析
│   │   ├── naming.py                  #   文件 / 文件夹命名规则
│   │   └── pipeline.py                #   登录→列表→详情→下载 顶层流水线
│   │
│   ├── scrap/                         # 废钢检判通用流水线 + PPT 汇报生成
│   │   ├── __init__.py
│   │   ├── client.py                  #   废钢检判系统 HTTP 客户端（satoken Cookie 鉴权）
│   │   ├── parser.py                  #   明细数据解析
│   │   ├── dict.py                    #   业务字典（料型 / 场地等映射）
│   │   ├── models.py                  #   数据模型
│   │   ├── calculator.py              #   统计计算
│   │   ├── excel_writer.py            #   Excel 报表输出
│   │   ├── ppt_builder.py             #   PPT 构建
│   │   ├── ppt_writer.py              #   PPT 写入
│   │   └── ppt/                       #   PPT 图表 skill（SKILL.md + 参考规则 + 脚本）
│   │       ├── SKILL.md
│   │       ├── __init__.py
│   │       ├── agents/openai.yaml
│   │       ├── references/            #     图表选型与视觉规则
│   │       │   ├── chart-selection.md
│   │       │   └── visual-rules.md
│   │       └── scripts/               #     输入分析 / 图表构建 / 文本表规范化
│   │           ├── analyze_input.py
│   │           ├── build_ppt_chart.py
│   │           └── normalize_text_table.py
│   │
│   ├── shenglong/                     # 盛隆钢铁 · 废钢检判链路
│   │   ├── __init__.py
│   │   ├── __main__.py                #   python -m agent.shenglong 入口
│   │   ├── config.py                  #   （见 api_client 等，略）
│   │   ├── client.py
│   │   ├── dict.py
│   │   ├── models.py
│   │   ├── calculator.py
│   │   ├── downloader.py              #   原图下载
│   │   ├── minio_downloader.py        #   MinIO 原图下载
│   │   ├── naming.py
│   │   ├── excel_writer.py
│   │   ├── packager.py                #   数据集打包
│   │   └── remote_sync.py             #   scp 远端同步
│   │
│   ├── yongfeng/                      # 永锋钢铁 · 打包带 / 烧结矿颗粒度链路
│   │   ├── __init__.py
│   │   ├── main.py                    #   场景入口
│   │   ├── client.py
│   │   ├── dict.py
│   │   ├── models.py
│   │   ├── calculator.py
│   │   ├── downloader.py
│   │   ├── excel_writer.py
│   │   ├── scrap_align.py             #   废钢对齐（人工 vs 视觉）
│   │   ├── scrap_client.py            #   废钢检判系统客户端
│   │   ├── scrap_dict.py
│   │   ├── scrap_models.py
│   │   ├── scrap_naming.py
│   │   ├── scrap_packager.py
│   │   ├── scrap_remote_sync.py
│   │   ├── scrap_uploader.py          #   datasets/ zip 上传金睛平台（登录→搜索→MinIO直传→导入任务）
│   │   └── output/                    #   准确率报表 JSON 中间结果（运行时生成）
│   │       ├── accuracy_report_computed.json
│   │       └── accuracy_report_raw.json
│   │
│   └── yongyou/                       # 用友系统 · 图片下载链路（登录→列表→点击→下载）
│       ├── __init__.py
│       ├── auth.py
│       ├── config.py
│       ├── page_actions.py
│       ├── pipeline.py
│       ├── downloader.py
│       └── excel_builder.py
│
├── config/                            # 全局配置
│   ├── __init__.py
│   └── settings.py
│
├── tests/                             # pytest 测试
│   ├── test_bxsteel_unit.py
│   ├── test_gradio_bind.py
│   ├── test_handbook_adversarial.py
│   ├── test_image_download_route.py
│   ├── test_llm_client.py
│   ├── test_scrap_ppt_unit.py
│   ├── test_scrap_unit.py
│   ├── test_shenglong_download.py
│   ├── test_shenglong_remote_sync.py
│   ├── test_shenglong_unit.py
│   ├── test_yongfeng_download.py
│   ├── test_yongfeng_scrap_align.py
│   ├── test_yongfeng_unit.py
│   ├── test_yongfeng_upload.py
│   └── test_yongyou_unit.py
│
├── tools/                             # 独立运维 / 导出脚本
│   ├── scrap_export.py                #   废钢数据导出
│   ├── shenglong_export.py            #   盛隆数据导出
│   ├── shenglong_master_export.py     #   盛隆主表导出
│   ├── yongfeng_export.py             #   永锋数据导出
│   ├── tencent_docs_upload_xlsx_to_tencent_docs.py  # xlsx 上传腾讯文档在线表格
│   └── ppt/                           #   PPT 美化脚本
│       ├── beautify_briefme.py
│       └── beautify_sales_overview.py
│
├── docs/                              # 运维文档
│   └── shenglong-scp-hostkey.html     #   盛隆 scp hostkey 说明
│
├── from_colleague_reference/          # 同事提供的参考 skill（graphing-ppt-charts）
│   └── graphing-ppt-charts/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       ├── references/
│       │   ├── chart-selection.md
│       │   └── visual-rules.md
│       └── scripts/
│           ├── analyze_input.py
│           ├── build_ppt_chart.py
│           └── normalize_text_table.py
│
├── reference/                         # 业务参考样表
│   └── 盛隆赛迪废钢判级_主表_2026-04-14_2026-05-20 (2).xlsx
│
├── .gitignore                         # Git 忽略规则（缓存/视频/密钥/下载目录等）
├── app.py                             # ★ Gradio Web UI 入口（五档界面：主题/品牌栏+VPN灯/分栏/预设prompt/产物下载面板）
├── BriefMe使用手册.md                 # 用户操作手册
├── Readme.md                          # 项目说明（场景规则、VPN 前置、指令示例）
├── requirements.txt                   # Python 依赖（gradio / zhipuai / httpx / openpyxl / python-pptx / boto3 等）
├── deduction_exclusion_log.json       # 扣分/剔除规则命中日志（运行时追加）
├── 废钢每日检判统计步骤-垣泰终版.docx  # 现场操作文档（二进制）
└── 永锋钢卷打包带_操作文档.doc         # 现场操作文档（二进制）
```

## 快速定位

| 想做什么 | 去哪里 |
| --- | --- |
| 启动 Web 界面 | `app.py` |
| 改指令路由 / 新增场景 | `agent/core.py`、`agent/image_download_route.py` |
| 改某现场的取数或下载 | `agent/{yongfeng,bxsteel,shenglong,yongyou}/` 对应子包 |
| 改报表计算 / Excel 输出 | 各场景 `calculator.py` / `excel_writer.py` |
| 改 PPT 汇报 | `agent/scrap/ppt_builder.py`、`agent/scrap/ppt/` |
| 上传腾讯文档 | `tools/tencent_docs_upload_xlsx_to_tencent_docs.py` |
| 加依赖 / 配置 | `requirements.txt`、`config/settings.py` |
| 补测试 | `tests/` |

## Git 使用约定

- `main`：始终保存可回退的稳定基线；不要在 `main` 上直接改代码。
- `feature/*`：新功能 / 文档改动先切分支，验证后合回 `main`。
- 常用命令：
  - 切新分支：`git checkout -b feature/xxx`
  - 查看改动：`git status` / `git diff`
  - 提交：`git add <文件> && git commit -m "说明"`
  - 回退单个文件到上个版本：`git restore <文件>`
  - 整体回退到某次提交：`git reset --hard <commitID>`
