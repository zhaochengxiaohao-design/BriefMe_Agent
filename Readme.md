# 🏭 BriefMe · 多场景工业智能决策助手



## 📖 项目简介

BriefMe 是一个为工业现场量身定制的数据统计交互智能体。
它本身**不持久化存储任何业务数据**，而是通过解析用户的自然语言指令，智能路由并调用对应的现场业务系统 API 实时取数。数据经过本地核心算法层清洗与计算后，自动生成并交付标准化的 Excel 报表、PPT 汇报以及异常监控图片。

## 🛠️ 技术栈与依赖

* **核心语言**: Python 3.12+
* **前端交互**: `gradio` (构建 Web UI 界面)
* **大语言模型**: DeepSeek（`DEEPSEEK_API_KEY`，OpenAI 兼容接口；负责自然语言理解与 Function Calling）
* **网络请求**: `httpx` (对接各类现场专网 API)
* **自动化办公**: `openpyxl` (生成数据核对表), `python-pptx` (生成自动化汇报 PPT), `tencent docs upload` (生成报表后自动上传腾讯文档在线表格)
* **质量保证**: `pytest`, `flake8` (自动化测试与静态检查)

---

## 🏭 接入场景与核心业务规则

本项目严禁跨场景复用业务逻辑，各现场规则完全独立：

### 1. 永锋钢铁

永锋专网同时承载三块能力，**不要互相抢路由**：打包带钢卷、烧结矿颗粒度、废钢检判原图。顶部灯探测打包带地址，检判原图走同一 VPN。

#### 1.1 烧结矿颗粒度 📦
* **功能**：生成人工筛分 vs 视觉准确率报表，按日对齐并计算各粒径误差 / MAE，导出 Excel 结果，并可自动上传到腾讯文档在线表格。
* **数据规则**：人工数据按 `inspectResult=Y` 保留；视觉 1# / 2# 对应指定站点与页面路径，取 `(T-4h, T]` 的视觉窗口后计算均值。
* **输出目录**：JSON 中间结果写入 `agent/yongfeng/output/`，Excel 报表默认输出到 `downloads/yongfeng/`。
* **网络前置**：需连接永锋专网并保证视觉 / 人工系统可访问。

#### 1.2 检判原图下载（只走 srape-steel，不走盛隆 3000）

从 `http://vision.lg.china-yongfeng.com/srape-steel` 拉「智能判级照片」原图，按日、按车立刻写到本机。指令必须带【永锋】。**左侧「图像保存路径」必填**，例如 `/Users/你的用户名/Desktop/永锋图像`。不要把烧结矿报表、打包带异常图、盛隆 3000 混进这条链路。照片 URL 里的 MinIO `origin` 只是原图 HTTP 源，下载 API 仍是 srape-steel。

* **目录**（车次文件夹带当天日期；默认同车牌不加 `_N`，仅第二辆同车牌同料型才加）：

```text
<保存路径>/
  download_progress.log
  YYYY-MM-DD/
    YYYY-MM-DD_车牌_重废1(85)、重废2(15)/
      20260901_zhongfei1_85_zhongfei2_15_1_1_1.jpg
    datasets/
      重废1_实例分割数据集.zip
      重废1_边缘分割数据集.zip
```

* **规则**：
  * 单日 / `A 到 B` / 顿号枚举；顿号**不补中间天**。文中无 `YYYY-MM-DD` 时，「昨天/昨日」「近 7 天/近一周」按手册展开（近 7 天不含今天）。
  * 已有非空 JPEG 跳过，可续传。0 车当天不建空 `datasets/`。
  * 每天下完后 scp 到  
    `cisdi@10.233.224.206:/mnt/data01/embedded/projects/wangyutai/yf_feigang/test_images_full_car/<日期>/`  
    只拷车次文件夹，不拷 `datasets/`，**禁止写入盛隆 `sl_feigang`**。scp 失败只记总结，**不中断下载**。
  * 实例/边缘分割包自动打。**废钢多标签分类数据集不会自动打**。
* **页面用法**（「永锋钢铁 → 检判原图下载」）：
  * `下载 2026-09-01 的【永锋】检判原图`
  * `下载 2026-08-31 到 2026-09-01 的【永锋】检判原图`
  * `下载 2026-08-31、2026-09-01 的【永锋】检判原图`
  * `确认打包保存目录下已筛完的【永锋】废钢多标签分类数据集`
* **CLI**（不要占用烧结矿入口 `python -m agent.yongfeng`）：

```bash
python -m agent.yongfeng.downloader --start 2026-09-01 --end 2026-09-01 --output /Users/你的用户名/Desktop/永锋图像
```

### 2. 镔鑫钢铁 · 废钢检判 ♻️
* **功能**：生成单日/区间文本汇总、报表，下载错判图，**自动生成包含趋势图与 KPI 的汇报 PPT**。
* **业务规则**：主料型为“杂摸 / 中废”的不计入主料准确率（但保留在明细中）；AI 视觉报“重废”与人工报“重废1/2/3”视为一致。

### 3. 盛隆钢铁 · 废钢检判 ⚙️（重点）

盛隆有两套独立能力：**检判统计出表**，以及 **3000 网站智能判级原图下载**。都只走 `http://172.16.16.101:3000/`，必须先连盛隆专网。页面顶部「盛隆 VPN」变绿后再操作。指令里必须带【盛隆】，否则会和镔鑫废钢混淆。

#### 3.1 检判统计

* **功能**：单日文本汇总、单周期 Excel、多周期普通主表、重废 1/2/3 归一化主表。
* **统计口径**：
  * 主料正确 = 料型名字一致 **且** 占比差异 **&lt; 11%**（10.xx% 算对，刚好 11% 不算）。并列主料时，任一料型命中且差异 &lt; 11% 即算对。
  * 扣杂符合 = `0.5 ≤ AI/人工 ≤ 1.5` **或** `|AI−人工| &lt; 0.151 吨`（未到 151kg 算对）。
  * 「近 7 天 / 近一周」= **昨天往前共 7 个自然日**（含昨天、不含今天）。
  * **黑名单**：施宏波、冉星明、周倩、王宇泰、王重阳不参与人工合并；剔除后无人，则该车人工视为缺失，不进识别率/扣杂分母。
  * **单位防御**：人工扣重 &gt; 10 吨视为按 kg 录入忘换算，自动 `/1000` 转吨。
  * **重废归一化主表**：准确率只看重废 1/2/3；人工完全没有这三类的车次不进准确率分母。Sheet1 每周期 17 行，不再写环比列。
* **页面用法**（左侧选「盛隆钢铁 → 废钢检判」，点指令填入后回车）：
  * `发 2026-04-28 的【盛隆】废钢检判情况`
  * `导出 2026-04-23 到 2026-04-29 的【盛隆】废钢检判报表`
  * `生成【盛隆】主表，把这两个周期累积到一个 xlsx：2026-04-14 至 2026-04-22、2026-04-23 至 2026-04-29`
  * `生成【盛隆】重废1/2/3归一化准确率主表，把这几个周期累积到一个 xlsx：...`
* **产出**：`downloads/shenglong/<日期或区间>/`、`downloads/shenglong/master/`

#### 3.2 检判原图下载（只走 3000，不走 MinIO）

从 3000「智能判级照片」按日、按车立刻写到本机。**左侧「图像保存路径」必填**，例如 `/Users/你的用户名/Desktop/盛隆图像`。

* **目录**（每个车次文件夹都带当天日期）：

```text
<保存路径>/
  download_progress.log
  YYYY-MM-DD/
    YYYY-MM-DD_车牌_中废(40)、重废1(30).../
      2026_08_27_53_1_medium_40_..._1.jpg
    datasets/
      重废1_实例分割数据集.zip
      重废1_边缘分割数据集.zip
      平均料型_实例分割数据集.zip   # 主次料差 ≤15 个百分点时打这个
```

* **规则**：
  * 单日 / `A 到 B` 连续区间 / 顿号枚举多个日期都支持；顿号枚举**不补中间天**。
  * 已有非空文件跳过，可续传。旧文件夹没有日期前缀时，重跑会改成新名字，不重下。
  * 每天下完车次后，**原样 scp** 到推理测试机  
    `cisdi@10.180.34.16:/mnt/data01/embedded/projects/wangyutai/sl_feigang/test_images_full_car/<日期>/`  
    只拷车次文件夹，不拷 `datasets/`。scp 失败只记总结，**不中断下载**。
  * 实例/边缘分割包下载完自动打。**废钢多标签分类数据集不会自动打**：先按日期、按车次删不合格图，再点「确认打包多标签」。
* **页面用法**（「盛隆钢铁 → 检判原图下载」）：
  * `下载 2026-08-26 的【盛隆】检判原图`
  * `下载 2026-08-20 到 2026-08-26 的【盛隆】检判原图`
  * `下载 2026-08-01、2026-08-03、2026-08-05 的【盛隆】检判原图`
  * `确认打包保存目录下已筛完的【盛隆】废钢多标签分类数据集`

---

## 🚀 快速启动 (Quick Start)

### 1. 克隆项目与配置环境
强烈建议使用纯净的虚拟环境隔离依赖：
```bash
git clone https://github.com/LAWSSSS/BriefMe_Agent.git
cd BriefMe_Agent

# 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate  # Windows 用户请使用 venv\Scripts\activate

# 安装核心依赖
pip install -r requirements.txt
pip install python-pptx
```

### 2. 配置密钥与网络联通性自检
**⚠️ 严禁将真实 Key 或密码硬编码写入代码库！**
启动前，请根据你当前使用的终端类型，临时设置环境变量：

本地也可把密钥写进项目根目录 `.env`（已加入 `.gitignore`，不要提交）：

```bash
DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"
```

或在终端临时设置：

```bash
# 【如果你使用 Windows CMD 命令提示符】
set DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"

# 【如果你使用 Windows PowerShell】
$env:DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"

# 【如果你使用 Mac / Linux / Git Bash】
export DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"
```
启动前，必须连接对应现场的专网 VPN。请通过浏览器访问以下地址验证连通性：

永锋打包带 / 永锋检判原图: http://vision.lg.china-yongfeng.com/packing-tape/  
（检判原图业务页：http://vision.lg.china-yongfeng.com/srape-steel ，同一张永锋专网）

镔鑫废钢: http://172.31.1.102:8081/fcs-web/

盛隆废钢: http://172.16.16.101:3000/

### 3. 启动交互界面
```bash
python app.py
```

终端输出 Running on local URL: http://0.0.0.0:7860 后，在浏览器中打开该地址即可开始对话。左侧提供快捷指令按钮，点击填入后按回车发送。

## 💻 命令行批量导出模式 (CLI)

不开页面也可以运行 CLI，适合后台批量导出或排查问题：

```bash
# 导出永锋烧结矿准确率报表
python tools/yongfeng_export.py --start 2026-05-15 00:00:00 --end 2026-05-21 23:59:59

# 导出镔鑫区间报表 (不带错判图)
python tools/scrap_export.py --start 2026-04-22 --end 2026-04-28 --no-images

# 导出盛隆单周期报表
python tools/shenglong_export.py --start 2026-04-23 --end 2026-04-29

# 导出盛隆多周期重废归一化主表 (+ 号表示前后两段日期合并为一个统计周期)
python tools/shenglong_master_export.py --heavy-normalized \
  2026-04-14:2026-04-22 \
  2026-04-23:2026-04-29 \
  2026-04-30:2026-05-06+ \
  2026-05-07:2026-05-13

# 下载永锋检判原图（须永锋 VPN；不要写成 python -m agent.yongfeng）
python -m agent.yongfeng.downloader --start 2026-09-01 --end 2026-09-01 \
  --output /Users/你的用户名/Desktop/永锋图像
```

## 📁 核心代码结构

```text
BriefMe_Agent/
├── app.py                         # Web UI 入口
├── config/settings.py             # 各场景 URL、阈值、盛隆/永锋 scp 配置
├── agent/                         # Agent 核心逻辑层
│   ├── core.py                    # LLM 路由、工具调用分发
│   ├── tools.py                   # 给大模型看的 Function Calling Schema
│   ├── image_download_route.py    # 盛隆/永锋检判原图 Gradio 拦截（互斥）
│   ├── llm_client.py              # DeepSeek / OpenAI 兼容客户端
│   ├── data_fetcher.py            # 永锋打包带取数与异常处理
│   ├── scrap/                     # 镔鑫废钢子包
│   ├── shenglong/                 # 盛隆废钢子包（统计 + 3000 原图）
│   └── yongfeng/                  # 永锋烧结矿 + 检判原图（scrap_* 独立，禁止 import 盛隆 dict）
│       ├── downloader.py          # srape-steel 原图按日按车下载
│       ├── scrap_client.py        # 登录 Cookie satoken；列表 current/size
│       ├── scrap_naming.py / scrap_packager.py / scrap_remote_sync.py
├── tools/                         # CLI 批量导出
├── tests/
└── downloads/
```

---

## 本次 PR：永锋检判原图下载

对齐盛隆手册的车次目录命名与打包节奏；下载只走永锋 srape-steel，**不改盛隆统计公式**。

| 范围 | 说明 |
|---|---|
| 永锋下载 | `agent/yongfeng/scrap_*.py`、`downloader.py`：登录 `satoken`、按日按车落盘、续传、实例/边缘自动打包、多标签需确认 |
| 路由 | `agent/image_download_route.py`、`app.py`、`core.py`、`tools.py`：永锋/盛隆互斥；「永锋 + MINIO/3000」仍走永锋，裸 `MINIO图像下载` 仍是盛隆 |
| 盛隆原图 | 同车牌同料型第二车写入 `规范名_N`，避免混车；空日不建 `datasets/` |
| 远程 | 永锋 scp → `yf_feigang`；盛隆仍是 `sl_feigang`。失败不中断本地下载 |
| 测试 | `tests/test_yongfeng_download.py`、`test_image_download_route.py`、`test_handbook_adversarial.py` |
| 不要提交 | `download_log.txt`、`deduction_exclusion_log.json`、桌面原图、`venv` |

提交前至少跑：

```bash
python -m pytest tests/test_handbook_adversarial.py tests/test_yongfeng_download.py \
  tests/test_image_download_route.py tests/test_shenglong_download.py -q
python -c "import app; app.build_ui(); print('UI OK')"
```

## 🧑‍💻 开发者交接与协同规范 (Git Workflow)

为了保证工业级代码的绝对稳定，后续接手维护的工程师/实习生，请严格遵守以下开发规范：

### 1. 核心铁律
* **业务隔离**：不同现场的数据结构不同，切勿生搬硬套。永锋检判原图不要 import 盛隆 `dict` / `ShenglongClient`，也不要写入 `sl_feigang`。
* **脱敏原则**：**严禁提交 `venv` 文件夹**，严禁提交真实 Token 或将 `downloads/` 里的客户真实报表 Push 到云端。

### 2. 测试驱动开发 (TDD)
任何涉及 **准确率计算、扣重口径、多周期合并逻辑、人员黑名单调整** 的代码变动，**必须**同步更新 `tests/` 目录下的测试用例。
提交代码前，必须在本地跑通验证命令：

```bash
# 运行全部业务逻辑断言 (必须全绿 PASSED)
python -m pytest tests/ -x --tb=short -q

# UI 构建冒烟测试
python -c "import app; app.build_ui(); print('UI OK')"
```

### 3. 标准化提交与自动合并流水线
本项目已配置 GitHub Actions 强管控，严禁在 `master` 分支直接修改代码。请遵循以下标准流转：

```bash
# 1. 获取最新主干代码
git checkout master
git pull origin master

# 2. 切出新分支进行开发 (不要直接在 master 上改代码)
git checkout -b fix-xxx-bug

# 3. 本地自测通过后，提交你的修改
git add .
git commit -m "feat/fix: 简要说明你的修改内容"

# 4. 推送到云端的对应分支
git push -u origin fix-xxx-bug
```

*推送到云端后，请在 GitHub 网页端发起 **Pull Request (PR)**。此时云端机器会自动进行测试，当流水线全部通过（变绿）后，系统将自动把你的代码安全合入主干。*

---
