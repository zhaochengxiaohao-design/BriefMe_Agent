# BriefMe 使用与二次开发手册

> 中冶赛迪（重庆）信息技术有限公司 · 多场景数据统计助手  
> 当前接入：永锋钢铁（打包带 / 烧结矿 / 检判原图） / 镔鑫钢铁废钢检判 / 盛隆钢铁废钢检判  
> 读者：使用者、实习生、后续维护开发者

---

## 0. 交接目标

这份文档不是单纯的“怎么点按钮”，而是给实习生接手优化用的交接手册。读完后应该能做到：

- 在本地启动 BriefMe。
- 知道每个场景怎么使用。
- 知道新增/修改功能应该改哪些文件。
- 知道哪些业务规则不能随便动。
- 知道每次改完要跑哪些验证命令。

一句话理解：BriefMe 是一层“自然语言 → 工具调用 → 业务系统 API → 本地计算 → 生成报表”的胶水层。它本身不存业务数据，每次都通过 VPN 到现场系统实时取数。

---

## 1. 快速启动

### 1.1 进入项目目录

```bash
cd BriefMe_Agent
```

建议用仓库 `venv` 的 `python`（与 GitHub Readme 一致）。下面命令里的 `python` 都指这个解释器。

### 1.2 安装依赖

```bash
python -m pip install -r requirements.txt
python -m pip install python-pptx
```

主要依赖：

- `gradio`：页面 UI。
- DeepSeek（`DEEPSEEK_API_KEY`）：自然语言理解和 function calling。
- `httpx`：访问现场业务系统 API。
- `openpyxl`：生成 Excel。
- `python-pptx`：生成镔鑫 PPT。

### 1.3 配置 DeepSeek API Key

不要把真实 Key 写进代码或文档。优先在项目根目录建 `.env`（已 gitignore）：

```bash
DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"
```

或启动前在终端设置：

```bash
export DEEPSEEK_API_KEY="<向负责人索取的 DeepSeek API Key>"
```

### 1.4 连接 VPN

三个场景走不同 VPN，不能混用。

| 场景 | VPN / 网络 | 自检地址 |
|---|---|---|
| 永锋打包带 / 烧结矿 / 检判原图 | 永锋 aTrust，手机 Google Authenticator 验证码 | 打包带：http://vision.lg.china-yongfeng.com/packing-tape/ ；检判原图：http://vision.lg.china-yongfeng.com/srape-steel |
| 镔鑫废钢 | 镔鑫专网 | http://172.31.1.102:8081/fcs-web/ |
| 盛隆废钢 | 盛隆专网 | http://172.16.16.101:3000/ |

原则：要跑哪个场景，就先用浏览器打开对应地址确认能访问。

### 1.5 启动页面

```bash
python app.py
```

看到类似输出就成功：

```text
Running on local URL: http://0.0.0.0:7860
```

浏览器打开：

```text
http://localhost:7860
```

关闭服务：

```bash
Ctrl+C
```

---

## 2. 页面怎么用

页面左侧是快捷按钮，点击后只是把文字填到输入框，**不会自动发送**。确认日期和场景后，按回车或点发送。

| 按钮 | 作用 |
|---|---|
| 昨日【镔鑫】废钢检判统计 | 生成镔鑫文本汇总 |
| 近 7 天【镔鑫】报表+错判图 | 生成镔鑫 Excel，并下载错判渲染图 |
| 近 7 天【镔鑫】生成 PPT 汇报页 | 生成镔鑫单页 PPT |
| 昨日【盛隆】废钢检判统计 | 生成盛隆文本汇总 |
| 近 7 天【盛隆】报表 | 生成盛隆单周期 Excel（昨天往前 7 天，不含今天） |
| 【盛隆】主表（多周期累积·可改日期） | 生成盛隆普通多周期主表 |
| 【盛隆】重废归一化主表（排除无重废车次） | 生成盛隆新准确率口径主表 |
| 下载昨日/近 7 天/指定日期【永锋】检判原图 | 从 srape-steel 按日按车下载智能判级原图（先填保存路径） |
| 确认打包【永锋】多标签数据集 | 人工删完不合格图后再打永锋「废钢多标签分类数据集」 |
| 下载昨日/近 7 天/指定日期【盛隆】检判原图 | 从 3000 按日按车下载智能判级原图（先填保存路径） |
| 确认打包【盛隆】多标签数据集 | 人工删完不合格图后再打「废钢多标签分类数据集」 |
| 昨日打包带情况 | 生成永锋打包带文本汇总 |
| 下载昨日打包带异常图 | 下载永锋打包带异常原图/渲染图 |

页面下方“最近生成的报表 / PPT / 错判图片”会自动扫描 `downloads/`，展示最新产物。

---

## 3. 三个场景的使用方式

### 3.1 永锋钢铁 · 打包带

前置：连接永锋 VPN，顶部“打包带 VPN”为绿色。

常用指令：

```text
发昨天的打包带情况
发 2026-04-29 的打包带情况
下载昨天打包带的异常图片
```

主要输出：

- 当日生产钢卷总数。
- 正常、异常、未识别数量。
- 已打数与应打数差值为 1 / 大于 1 的异常数量。
- 差值大于 1 的异常图片。

### 3.1.1 永锋钢铁 · 检判原图下载

前置：同一张永锋 VPN。顶部灯仍探测打包带地址。指令必须带【永锋】。只走 `srape-steel`，不要走盛隆 3000，不要走烧结矿报表。左侧「图像保存路径」必填（绝对路径）。

```text
下载 2026-09-01 的【永锋】检判原图
下载 2026-08-31 到 2026-09-01 的【永锋】检判原图
下载 2026-08-31、2026-09-01 的【永锋】检判原图
确认打包保存目录下已筛完的【永锋】废钢多标签分类数据集
```

- 只拉详情「智能判级照片」原图（跳过 `*_render_*`），立刻写磁盘；已有非空 JPEG 跳过。
- 顿号枚举不补中间天；「A 到 B」才连续。无具体日期时，「昨天/昨日」「近 7 天/近一周」按手册展开（近 7 天不含今天）。
- 车次文件夹：`YYYY-MM-DD_车牌_重废1(85)、重废2(15)`。默不加当日序号；仅第二辆同车牌同料型用 `_N`。
- scp 到 `cisdi@10.233.224.206:.../yf_feigang/test_images_full_car/<日期>/`，不拷 `datasets/`，禁止写入 `sl_feigang`。失败不中断下载。
- 实例/边缘包自动打；多标签必须人工筛图后确认。0 车不建空 `datasets/`。

### 3.2 镔鑫钢铁 · 废钢检判

前置：连接镔鑫 VPN，顶部“镔鑫 VPN”为绿色。

常用指令：

```text
发 2026-04-28 的【镔鑫】废钢检判情况
导出 2026-04-22 到 2026-04-28 的【镔鑫】废钢检判报表并下载错判图
按 2026-04-22 到 2026-04-28 的【镔鑫】检判结果生成对应的 PPT 汇报页
```

镔鑫关键规则：

- 人工主料型为“杂摸 / 中废”的车次不计入主料准确率，但保留在表格中。
- 视觉结果为“重废”，人工结果为“重废 / 重废1 / 重废2”，都算主料一致。
- PPT 由 `agent/scrap/ppt_builder.py` 生成，包含趋势图、KPI、错判 Top、建议和数据来源。

输出位置：

```text
downloads/scrap/<日期或区间>/
```

### 3.3 盛隆钢铁 · 废钢检判

前置：连接盛隆 VPN，顶部“盛隆 VPN”为绿色。指令必须带【盛隆】。

盛隆现在是两块：**统计出表**，以及 **3000 智能判级原图下载**。不要再走 MinIO。

#### 统计

常用指令：

```text
发 2026-04-28 的【盛隆】废钢检判情况
导出 2026-04-23 到 2026-04-29 的【盛隆】废钢检判报表
导出 2026-04-23 到 2026-04-29 的【盛隆】报表，上周期是 2026-04-14 到 2026-04-22
```

「近 7 天」= 昨天往前共 7 个自然日（含昨天、不含今天）。今天是 2026-08-27 时，区间是 2026-08-20 到 2026-08-26。

普通多周期主表：

```text
生成【盛隆】主表，把这几个周期累积到一个 xlsx：
2026-04-14 至 2026-04-22、2026-04-23 至 2026-04-29、
2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13；
其中 2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13 当作一个统计周期进行统计
```

重废归一化主表：

```text
生成【盛隆】重废1/2/3归一化准确率主表，把这几个周期累积到一个 xlsx：
准确率统计时排除人工检判结果中没有任意重废1/2/3料型的车次；
2026-04-14 至 2026-04-22、2026-04-23 至 2026-04-29、
2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13；
其中 2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13 当作一个统计周期进行统计
```

盛隆统计规则：

- 剔除检判员：施宏波、冉星明、周倩、王宇泰、王重阳。
- 剔除后如果没有有效人工检判员，这辆车人工结果视为缺失。
- 人工或 AI 任一方缺失时，该车显示在明细里，但不计入汇总指标。
- 扣重结果如果大于 10 吨，视为人工按 kg 录入但未换算，自动除以 1000。
- 主料正确：料型名字一致，且占比差异 **&lt; 11%**（10.xx% 算对，刚好 11% 不算）。
- 扣杂符合：`0.5 ≤ 比值 ≤ 1.5` 或 `|误差| &lt; 0.151 吨`。
- 重废归一化主表：只看重废1/2/3，先把这三类归一化到 100%，再比较主重废类和差异。人工没有任何重废1/2/3 的车不进准确率分母。主表 Sheet1 每周期 17 行，无环比列。

重废归一化例子：

```text
人工：重废1 45%，重废2 35%，厚剪 10%，剪料1 10%
目标料型总占比 = 45 + 35 = 80
重废1归一化 = 45 / 80 * 100 = 56.25%
重废2归一化 = 35 / 80 * 100 = 43.75%
```

统计产出：

```text
downloads/shenglong/<日期或区间>/
downloads/shenglong/master/
```

#### 检判原图下载

先在页面左侧填「图像保存路径」（绝对路径），再发下载指令。没有路径会追问。

```text
下载 2026-08-26 的【盛隆】检判原图
下载 2026-08-20 到 2026-08-26 的【盛隆】检判原图
下载 2026-08-01、2026-08-03、2026-08-05 的【盛隆】检判原图
确认打包保存目录下已筛完的【盛隆】废钢多标签分类数据集
```

- 只拉 3000 详情里的「智能判级照片」，立刻写磁盘；已有非空文件跳过。
- 顿号/逗号列出的多个日期不补中间天；「A 到 B」才连续补天。
- 车次文件夹：`YYYY-MM-DD_车牌_中废(40)、重废1(30)...`（带日期）。
- 每天下完后，车次文件夹会 scp 到  
  `cisdi@10.180.34.16:/mnt/data01/embedded/projects/wangyutai/sl_feigang/test_images_full_car/<日期>/`  
  失败只在最终总结里说明，下载继续。
- 实例/边缘分割包自动打。主次料差 ≤15 个百分点 → 打进「平均料型」包。
- 「废钢多标签分类数据集」要先按车次删不合格图，再确认打包。

---

## 4. CLI 命令

不开页面也可以跑 CLI，适合批量导出或排查问题。

镔鑫导出：

```bash
python tools/scrap_export.py --start 2026-04-22 --end 2026-04-28
python tools/scrap_export.py --start 2026-04-22 --end 2026-04-28 --no-images
```

盛隆单周期：

```bash
python tools/shenglong_export.py --start 2026-04-23 --end 2026-04-29
```

盛隆普通多周期主表：

```bash
python tools/shenglong_master_export.py \
  2026-04-14:2026-04-22 \
  2026-04-23:2026-04-29 \
  2026-04-30:2026-05-06+ \
  2026-05-07:2026-05-13
```

盛隆重废归一化主表：

```bash
python tools/shenglong_master_export.py \
  --heavy-normalized \
  2026-04-14:2026-04-22 \
  2026-04-23:2026-04-29 \
  2026-04-30:2026-05-06+ \
  2026-05-07:2026-05-13
```

说明：日期段后面的 `+` 表示“这一段和下一段合并成同一个统计周期”。

永锋检判原图（须永锋 VPN；不要占用烧结矿入口 `python -m agent.yongfeng`）：

```bash
python -m agent.yongfeng.downloader --start 2026-09-01 --end 2026-09-01 \
  --output /Users/你的用户名/Desktop/永锋图像
```

---

## 5. 代码结构

```text
BriefMe_Agent/
├── app.py                         # Gradio UI 入口
├── requirements.txt
├── BriefMe使用手册.md              # 当前交接文档
│
├── config/
│   └── settings.py                # 各场景 URL、账号、目标值（永锋/盛隆 scp 分开）
│
├── agent/
│   ├── core.py                    # SteelCoilAgent：LLM 路由、工具调用、返回组织
│   ├── tools.py                   # 给大模型看的 function calling schema
│   ├── image_download_route.py    # 盛隆/永锋检判原图拦截，必须互斥
│   ├── llm_client.py              # DeepSeek / OpenAI 兼容客户端
│   ├── vpn_manager.py             # 永锋 VPN 探测（打包带地址）
│   ├── data_fetcher.py            # 永锋打包带取数
│   │
│   ├── scrap/                     # 镔鑫废钢，独立子包
│   ├── shenglong/                 # 盛隆废钢：统计 + 3000 原图
│   └── yongfeng/                  # 烧结矿报表 + 检判原图（scrap_* 禁止 import 盛隆 dict）
│       ├── downloader.py          # srape-steel 按日按车下载
│       ├── scrap_client.py        # Cookie satoken；列表 current/size
│       ├── scrap_naming.py
│       ├── scrap_packager.py
│       └── scrap_remote_sync.py   # scp 到 yf_feigang，失败不中断
│
├── tools/
│   ├── yongfeng_export.py
│   ├── scrap_export.py
│   ├── shenglong_export.py
│   └── shenglong_master_export.py
│
├── tests/
│   ├── test_yongfeng_download.py
│   ├── test_image_download_route.py
│   ├── test_handbook_adversarial.py
│   ├── test_shenglong_unit.py
│   ├── test_shenglong_download.py
│   └── test_shenglong_remote_sync.py
│
└── downloads/                     # 生成产物，不要手写业务逻辑依赖这里
```

---

## 6. 核心工作流

```text
用户输入自然语言
  ↓
app.py 把消息交给 SteelCoilAgent
  ↓
core.py 的系统提示词 + tools.py schema 告诉大模型可调用哪些工具
  ↓
大模型选择工具并抽取参数
  ↓
core.py dispatch 到对应场景
  ↓
client.py 访问现场 API
  ↓
calculator.py / parser.py 计算业务指标
  ↓
excel_writer.py / ppt_builder.py 生成文件
  ↓
downloads/ 下展示给页面
```

开发时先判断要改哪一层：

- UI 文案 / 快捷按钮：改 `app.py`。
- 大模型能不能选对工具：改 `agent/core.py` 的系统提示词和 `agent/tools.py`。
- API 地址、登录、分页、详情字段：改对应 `client.py`。
- 统计公式、过滤规则：改对应 `calculator.py` / `parser.py`。
- Excel 格式：改对应 `excel_writer.py`。
- PPT 格式：改 `agent/scrap/ppt_builder.py`。

---

## 7. 实习生开发守则

### 7.1 不要混场景

永锋、镔鑫、盛隆是三个不同现场。不要把一个场景的字典、API、统计口径复制到另一个场景里直接用。

特别注意：

- 镔鑫的 `steelType` 编码和盛隆不同。
- 镔鑫“重废不分级”和盛隆“重废1/2/3归一化”不是一回事。
- 盛隆有检判员黑名单，镔鑫没有这套规则。
- 永锋检判原图不要 import `agent.shenglong.dict` / `ShenglongClient`，不要写入 `sl_feigang`。
- 不要用「永锋 + MINIO图像下载」去调盛隆 3000；裸的 `MINIO图像下载` 仍是盛隆快捷语。
- 不要改盛隆统计公式来“顺便”给永锋下载用。

### 7.2 改统计规则必须补测试

只要动了以下内容，必须改或新增测试：

- 准确率分母。
- 主料型是否正确。
- 扣重是否符合。
- 人员剔除。
- 单位换算。
- Excel Sheet1 统计周期概括。
- 多周期主表合并逻辑。

推荐先改 `tests/test_shenglong_unit.py` 或对应镔鑫测试，再改实现。

### 7.3 不要提交真实密钥

禁止把这些写死进代码：

- `ZHIPU_API_KEY`
- VPN 密码
- 现场系统账号密码的新版本
- cookie / token

本项目历史上 `config/settings.py` 有开发阶段默认值，交付或上传前要检查是否需要脱敏。

### 7.4 不要删除 downloads 里的真实产物

`downloads/` 里可能有演示用报表和截图。调试可以新建 `_unit_test/` 或 `_preview/`，不要随便清空整个目录。

---

## 8. 常见修改任务怎么做

### 8.1 新增一个页面快捷按钮

改 `app.py`：

1. 在 `_quick_prompts()` 加一条 prompt。
2. 在左侧按钮区加一个 `gr.Button`。
3. 在底部绑定 `btn.click(lambda: q["xxx"], outputs=msg)`。
4. 跑 UI 构建冒烟。

```bash
python -c "import app; app.build_ui(); print('UI OK')"
```

### 8.2 新增一个大模型工具

至少改两个文件：

- `agent/tools.py`：新增 function schema。
- `agent/core.py`：在 `_execute_tool` 里加分支，并实现 `_tool_xxx`。

如果用户很容易说错，还要改 `core.py` 的系统提示词，明确什么话术走新工具。

### 8.3 修改盛隆主表 Sheet1

主要看：

- `agent/shenglong/models.py` 的 `PeriodSummary`。
- `agent/shenglong/calculator.py` 的 `aggregate_period` / `aggregate_period_heavy_normalized`。
- `agent/shenglong/excel_writer.py` 的 `_write_one_period_block()` 和 `write_master_xlsx()`。

Sheet1 是每个周期 17 行，多个周期就是 17 行块往下排。

### 8.4 修改盛隆 Sheet2 明细

主要看：

- `agent/shenglong/excel_writer.py` 的 `_truck_row_values()`。
- 普通口径直接用 `truck.manual_main` / `truck.ai_main`。
- 重废归一化口径会先通过 `to_heavy_normalized_view()` 把单车展示切换为归一化结果。

### 8.5 修改盛隆重废归一化规则

主要看 `agent/shenglong/calculator.py`：

- `HEAVY_STEEL_TYPES` 在 `agent/shenglong/dict.py`，当前是 `{1, 2, 3}`。
- `_normalized_target_rates()` 负责归一化。
- `_judge_heavy_normalized_truck()` 负责单车是否正确。
- `aggregate_period_heavy_normalized()` 负责周期聚合。
- `to_heavy_normalized_view()` 负责 Sheet2 展示切换。

---

## 9. 验证命令

每次改完至少跑：

```bash
python -m pytest tests/ -x --tb=short -q
```

改永锋/盛隆检判原图时再加跑：

```bash
python -m pytest tests/test_handbook_adversarial.py tests/test_yongfeng_download.py \
  tests/test_image_download_route.py tests/test_shenglong_download.py -q
```

只看盛隆：

```bash
python tests/test_shenglong_unit.py
```

只看 UI 能否构建：

```bash
python -c "import app; app.build_ui(); print('UI OK')"
```

如果连了盛隆 VPN，真实导出普通主表：

```bash
python tools/shenglong_master_export.py \
  2026-04-14:2026-04-22 \
  2026-04-23:2026-04-29
```

如果连了盛隆 VPN，真实导出重废归一化主表：

```bash
python tools/shenglong_master_export.py \
  --heavy-normalized \
  2026-04-14:2026-04-22 \
  2026-04-23:2026-04-29 \
  2026-04-30:2026-05-06+ \
  2026-05-07:2026-05-13
```

---

## 10. 常见问题

### Q1. VPN 灯是红的

先用浏览器打开对应业务系统首页。打不开就是 VPN 或网络问题，不是代码问题。打开后再点页面上的“刷新 VPN 状态”。

### Q2. 只说“废钢”或只说“检判原图”，agent 反问

这是故意设计。镔鑫和盛隆都是废钢，盛隆和永锋都有检判原图，数据、API、字典都不同。指令里带【镔鑫】【盛隆】或【永锋】即可。两厂写在同一句里会拒绝，避免混下。

### Q3. `InvalidPathError: Dotfiles ...`

WPS/Excel 打开文件时可能生成 `.~xxx.xlsx` 临时锁文件。`app.py` 的 `_is_visible_file()` 已过滤 `.开头` 和 `~$开头` 文件。如果又出现类似报错，检查 `downloads/` 是否出现新类型隐藏文件，并加到过滤函数。

### Q4. 真实接口报 401 / token 失效

先确认 VPN，再确认账号密码，再看对应 `client.py` 的登录逻辑。

- 镔鑫 token：`satoken`。
- 盛隆 token：`scrape-steel-token`，token 路径是 `data.tokenInfo.tokenValue`。
- 永锋检判原图 token：Cookie 名 `satoken`，列表分页字段是 `current` / `size`（不要抄盛隆 `pageIndex`）。

### Q5. Excel 里出现很大的扣重，比如 26 或 2280

盛隆已加自动修正：人工扣重 >10 吨视为 kg 录入，自动 `/1000` 转吨。日志里会出现：

```text
扣重单位自动修正: 某某 录入 2280.000 → 视为 kg → 2.280 吨
```

### Q6. 普通主表和重废归一化主表有什么区别

普通主表：按原始主料型判断，主料名字一致且占比差异 &lt; 11% 才算正确。

重废归一化主表：只看重废1/2/3，把这三类归一化后再判断。人工没有任意重废1/2/3的车不进入准确率分母。扣重和价格统计不变。

### Q7. 盛隆图像还从 MinIO 下吗

不从 MinIO 下。只走 3000 业务系统的「智能判级照片」。先填保存路径，再发「下载 … 的【盛隆】检判原图」。多标签包要人工删图后确认打包。scp 到测试机失败不会停下载。

### Q8. 永锋说 MINIO / 3000 会下到盛隆吗

不会。带「永锋」的 `MINIO图像下载` / `3000网站图像下载` 走永锋 srape-steel。只有不带永锋的裸快捷语才是盛隆。永锋照片 URL 里的 MinIO `origin` 只是原图 HTTP 源，不是盛隆测试机。

---

## 11. 当前重点功能清单

| 功能 | 状态 | 关键文件 |
|---|---|---|
| 永锋打包带日统计 | 已实现 | `agent/data_fetcher.py` |
| 永锋异常图下载 | 已实现 | `agent/data_fetcher.py` |
| 永锋检判原图下载 | 已实现 | `agent/yongfeng/downloader.py`（srape-steel） |
| 永锋原图命名 / 打包 / scp | 已实现 | `scrap_naming.py` / `scrap_packager.py` / `scrap_remote_sync.py`（`yf_feigang`，失败不中断） |
| 永锋多标签分类数据集（人工确认） | 已实现 | `pack_multilabel_from_disk()`（永锋 packager） |
| 镔鑫日/区间统计 | 已实现 | `agent/scrap/` |
| 镔鑫错判图下载 | 已实现 | `agent/scrap/client.py` |
| 镔鑫 PPT | 已实现 | `agent/scrap/ppt_builder.py` |
| 盛隆日/区间统计 | 已实现 | `agent/shenglong/` |
| 盛隆单周期报表 | 已实现 | `agent/shenglong/excel_writer.py` |
| 盛隆多周期普通主表 | 已实现 | `write_master_xlsx()` |
| 盛隆多周期重废归一化主表 | 已实现 | `aggregate_period_heavy_normalized()` / `to_heavy_normalized_view()` |
| 盛隆 3000 检判原图下载 | 已实现 | `agent/shenglong/downloader.py` |
| 盛隆原图命名 / 平均料型打包 | 已实现 | `agent/shenglong/naming.py` / `packager.py` |
| 盛隆原图 scp 到推理测试机 | 已实现 | `agent/shenglong/remote_sync.py`（失败不中断） |
| 盛隆多标签分类数据集（人工确认） | 已实现 | `pack_multilabel_from_disk()` |

---

## 12. 给实习生的第一天任务建议

1. 跑通 `app.py`，打开页面。
2. 不连 VPN，先跑单测：

```bash
python -m pytest tests/ -x --tb=short -q
```

3. 阅读这 5 个文件：

```text
app.py
agent/core.py
agent/tools.py
agent/image_download_route.py
agent/yongfeng/downloader.py
```

4. 用 `tools/shenglong_master_export.py --help` 看 CLI 参数。
5. 找一个小文案改动，比如按钮文字，改完跑 UI 构建冒烟。
6. 再做业务逻辑改动，不要第一天就改统计公式。

---

最后更新：2026-09-07
