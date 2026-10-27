"""页面操作模块：表格行识别、点击行加载详情、图片提取、日期筛选、翻页。"""
from __future__ import annotations

import re
import time
import logging
from typing import Dict, List, Tuple

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

ALLOWED_GRADES = {"精炉料-等级二", "精炉料-等级三", "重废-等级一", "重废-等级二"}

IMAGE_CATEGORIES = {
    "visualize_superdetect_": "危险物图",
    "visualize_special_":     "夹杂物图",
    "visualize_top_":         "判级识别图",
    "visualize_":             "判级识别图",
    "":                       "原图",
}


def _classify_image(url: str) -> str | None:
    if '/thumbnail/' in url or '_s.' in url.lower():
        return None
    fname = url.rsplit('/', 1)[-1] if '/' in url else url
    for prefix, category in IMAGE_CATEGORIES.items():
        if prefix == '':
            return category
        if fname.startswith(prefix):
            return category
    return "原图"


def get_table_rows(page: Page) -> list:
    for sel in [
        '.wui-table-body .wui-table-row',
        '.wui-table-body tr',
        '.wui-table-tbody tr',
        '[class*="table-body"] tr',
        '[class*="table"] tbody tr:not([class*="header"])',
        'table tbody tr',
    ]:
        rows = page.query_selector_all(sel)
        if rows:
            data_rows = []
            for r in rows:
                cls = r.get_attribute('class') or ''
                if 'thead' in cls or 'header' in cls:
                    continue
                text = r.inner_text().strip()
                if text:
                    data_rows.append(r)
            if data_rows:
                return data_rows
    return []


def get_grading_col_index(page: Page) -> int:
    ths = page.query_selector_all('.wui-table-thead-th')
    for idx, th in enumerate(ths):
        key = th.get_attribute('data-col-key') or ''
        if key == 'afGradingDef':
            logger.debug("人工级别列: 第 %d 列", idx)
            return idx
    logger.warning("未找到 data-col-key='afGradingDef' 列")
    return -1


def parse_primary_grade(grade_text: str) -> str:
    if not grade_text:
        return ""
    first = grade_text.split(",")[0].strip()
    first = re.sub(r'-\d+(?:\.\d+)?%$', '', first)
    return first


def get_row_grade(row, col_index: int) -> str:
    if col_index >= 0:
        cells = row.query_selector_all('td')
        if col_index < len(cells):
            title = cells[col_index].get_attribute('title') or cells[col_index].inner_text()
            grade = parse_primary_grade(title)
            if grade:
                return grade

    for cell in row.query_selector_all('td'):
        title = cell.get_attribute('title') or cell.inner_text()
        if title and re.search(r'等级[一二三四五六\d]', title):
            grade = parse_primary_grade(title)
            if grade:
                return grade
    return ""


def is_allowed_grade(grade: str) -> bool:
    return grade in ALLOWED_GRADES


# 列定义（与 excel_exporter 一致，去掉序号）
TABLE_COLUMNS = [
    ("carNumber",         "车牌号",        14),
    ("ibGradingDef",      "智能级别",      22),
    ("afGradingDef",      "人工级别",      26),
    ("ibOnRmimp",         "智能扣杂(kg)",  16),
    ("afRmimp",           "人工扣杂(kg)",  16),
    ("synthesizeThickness","智能综合厚度", 16),
    ("ib2Price",          "智能价格",      14),
    ("price",             "吨钢价格",      14),
    ("punMount",          "奖惩金额",      14),
    ("oversizeRatio",     "超尺寸占比(%)", 16),
    ("materialTypeMax",   "主料型及占比",  20),
    ("showThickThin",     "HM/MT模型及占比",18),
    ("sysSlagRatio",      "智能扣杂率(%)", 16),
    ("sysWarnSlagSum",    "报警物扣杂(kg)", 18),
    ("sysWarnFee",        "报警物扣费(元)", 16),
    ("baseRmimp",         "基础扣杂量(kg)", 16),
    ("lowGradeRmimp",     "最低级别扣杂量(kg)", 20),
    ("slagRmimp",         "渣土扣杂量(kg)", 16),
    ("compositeRmimp",    "综合严重程度扣杂量(kg)", 24),
    ("rasWeatherRmimp",   "雨雪天扣渣重量(kg)", 20),
    ("counterweightRmimp","配重块扣渣重量(kg)", 20),
    ("pigIronRmimp",      "生铁块扣渣重量(kg)", 20),
    ("oilDegree",         "油污严重程度",  16),
    ("rustDegree",        "锈蚀严重程度",  16),
    ("soilDegree",        "土杂严重程度",  16),
    ("compositeDegree",   "综合严重程度",  16),
    ("suckerCount",       "XP数量占比",    14),
    ("oilWarning",        "YW数量占比",    14),
    ("sysMohuWarning",    "MH数量占比",    14),
]


def get_row_cell_data(page: Page) -> list[dict]:
    """从当前页提取所有列数据（通过 JS 读取表头 data-col-key 映射）。

    一次 JS 调用提取整页数据，调用方在点击行之前调用。
    """
    col_keys = [c[0] for c in TABLE_COLUMNS]
    result = page.evaluate('''(targetKeys) => {
        const keyToIndex = {};
        const allThs = document.querySelectorAll('.wui-table-thead-th');
        allThs.forEach((th, i) => {
            const key = th.getAttribute('data-col-key') || '';
            if (targetKeys.indexOf(key) >= 0) {
                keyToIndex[key] = i;
            }
        });
        const rows = [];
        const trs = document.querySelectorAll(
            '.wui-table-body .wui-table-row, .wui-table-body tr, .wui-table-tbody tr, [class*="table-body"] tr'
        );
        trs.forEach(tr => {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 3) return;
            const row = {};
            for (const [key, idx] of Object.entries(keyToIndex)) {
                if (idx >= tds.length) continue;
                const td = tds[idx];
                const title = td.getAttribute('title');
                row[key] = (title || td.innerText || '').trim();
            }
            rows.push(row);
        });
        return rows;
    }''', col_keys)
    return result


def wait_spinner_gone(page: Page, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        spinner = page.query_selector('.wui-spin-full-screen')
        if not spinner:
            return
        time.sleep(0.5)
    logger.warning("加载遮罩超时 %ds", timeout)
    # 超时后强制移除遮罩
    _dismiss_spinner(page)


def _dismiss_spinner(page: Page):
    """通过 JS 隐藏全屏加载遮罩，解决首次登录遮罩不消失的问题。"""
    try:
        page.evaluate('''() => {
            const spinners = document.querySelectorAll('.wui-spin-full-screen, .wui-spin-backdrop');
            spinners.forEach(el => { el.style.display = 'none'; });
        }''')
        time.sleep(0.5)
        logger.info("已强制移除加载遮罩")
    except Exception:
        pass


def click_row_and_extract(
    page: Page,
    row,
    row_index: int,
    total: int,
    delay: float = 2.0,
) -> Tuple[str, Dict[str, List[str]]]:
    plate = f"row_{row_index}"
    plate_cell = None

    cells = row.query_selector_all('td')
    for cell in cells:
        title = (cell.get_attribute('title') or '').strip()
        if title and not title.isdigit() and len(title) >= 2:
            plate = title
            plate_cell = cell
            break

    if plate_cell is None:
        for cell in cells:
            title = (cell.get_attribute('title') or '').strip()
            if title:
                plate = title
                plate_cell = cell
                break

    if plate_cell is None:
        if cells:
            plate_cell = cells[0]
            plate = cells[0].inner_text().strip() or plate
        else:
            plate_cell = row

    logger.info("=" * 50)
    logger.info("[%d] %s", total + 1, plate.strip())
    logger.info("=" * 50)

    try:
        wait_spinner_gone(page, timeout=30)

        # 先关闭旧的详情面板（按 ESC），确保每次点击都从干净状态开始
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        try:
            plate_cell.click(timeout=5000)
        except Exception:
            logger.debug("正常点击被拦截，使用 force click")
            plate_cell.click(force=True, timeout=5000)

        time.sleep(delay)
        _wait_for_detail_section(page)

        # 等遮罩消失（detail 加载），超时后再给 10 秒宽限期
        wait_spinner_gone(page, timeout=15)
        time.sleep(2)

        logger.debug("触发图片懒加载...")
        _scroll_detail_section(page)

        classified = _extract_classified_images(page)
        total_imgs = sum(len(v) for v in classified.values())
        for cat, urls in classified.items():
            logger.info("  %s: %d 张", cat, len(urls))
        logger.info("  合计: %d 张图片", total_imgs)
        return plate.strip(), classified
    except Exception as e:
        logger.error("点击行失败: %s", e)
        return plate.strip(), {}


def _wait_for_detail_section(page: Page):
    try:
        page.wait_for_selector('img[src*="/uploadFile/"]', timeout=15000)
        time.sleep(3)
    except Exception:
        time.sleep(2)


def _scroll_detail_section(page: Page):
    container = None
    for sel in [
        '.wui-table-body', '[class*="detail"]', '[class*="result"]',
        '[class*="image"]', '[class*="preview"]', '[class*="scroll"]',
    ]:
        container = page.query_selector(sel)
        if container:
            break

    if not container:
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(1)
        return

    prev_height = 0
    for i in range(8):
        container.evaluate('el => el.scrollTop = el.scrollHeight')
        time.sleep(1)
        new_height = container.evaluate('el => el.scrollHeight')
        if new_height == prev_height and i > 0:
            break
        prev_height = new_height
        page.evaluate('window.scrollBy(0, 300)')
        time.sleep(0.5)


def _extract_classified_images(page: Page) -> Dict[str, List[str]]:
    classified: Dict[str, List[str]] = {}
    seen: set[str] = set()

    for img in page.query_selector_all('img[data-original]'):
        url = img.get_attribute('data-original') or ''
        if url and '/uploadFile/' in url and url not in seen:
            cat = _classify_image(url)
            if cat:
                seen.add(url)
                classified.setdefault(cat, []).append(url)

    for img in page.query_selector_all('img[src]'):
        url = img.get_attribute('src') or ''
        if url and '/uploadFile/' in url and url not in seen:
            cat = _classify_image(url)
            if cat:
                seen.add(url)
                classified.setdefault(cat, []).append(url)

    for attr in ['data-src', 'data-lazy-src']:
        for img in page.query_selector_all(f'img[{attr}]'):
            url = img.get_attribute(attr) or ''
            if url and '/uploadFile/' in url and url not in seen:
                cat = _classify_image(url)
                if cat:
                    seen.add(url)
                    classified.setdefault(cat, []).append(url)

    return classified


def extract_pie_charts(page: Page) -> List[Tuple[str, str]]:
    charts: List[Tuple[str, str]] = []

    result = page.evaluate('''() => {
        const charts = [];
        const pieDivs = document.querySelectorAll('[id^="PieChart"]');
        pieDivs.forEach((div, idx) => {
            const canvas = div.querySelector('canvas');
            if (!canvas) return;

            let label = '';
            const picture = div.closest('.picture');
            if (picture) {
                const pointName = picture.querySelector('.chart-point-name');
                if (pointName) label = pointName.innerText.trim();
            }
            if (!label) label = 'pie_' + idx;

            const dataUrl = canvas.toDataURL('image/png');
            charts.push({
                label: label.replace(/[\\\\/:*?"<>|]/g, '_'),
                dataUrl: dataUrl,
                idx: idx
            });
        });
        return charts;
    }''')

    label_count: dict[str, int] = {}
    for c in result:
        lbl = c['label']
        if lbl not in label_count:
            label_count[lbl] = 0
        else:
            label_count[lbl] += 1
        if label_count[lbl] > 0:
            lbl = f"{lbl}_{label_count[lbl]}"
        charts.append((lbl, c['dataUrl']))

    logger.info("提取到 %d 个饼图", len(charts))
    return charts


def click_history_data(page: Page):
    logger.info("进入历史数据...")
    link = page.query_selector('a[href="#/record"]:has-text("历史数据")')
    if not link:
        link = page.query_selector('span:has-text("历史数据")')
    if link:
        link.click()
        time.sleep(4)
        logger.info("已点击'历史数据'")


def apply_date_filter(page: Page, from_date: str = "", to_date: str = ""):
    if not from_date and not to_date:
        return

    logger.info("应用时间筛选: %s ~ %s", from_date or "(不限)", to_date or "(不限)")

    # 核心策略：wui-picker 的 #fromDate / #toDate 输入框，先清空再输入
    for date_id, date_val in [("#fromDate", from_date), ("#toDate", to_date)]:
        if not date_val:
            continue
        try:
            # 点击日历图标展开日期选择器
            page.click(f"span#{date_id.replace('#', '')}_suffix i", timeout=3000)
            time.sleep(0.5)
        except Exception:
            pass
        # Playwright 点击输入框 → 全选清除 → 键入新值
        try:
            inp = page.wait_for_selector(date_id, state="visible", timeout=3000)
            if inp:
                inp.click()
                time.sleep(0.2)
                page.keyboard.press("Control+a")
                page.keyboard.type(date_val, delay=30)
                page.keyboard.press("Enter")
                logger.info("已填写 %s: %s", date_id, date_val)
        except Exception:
            # 兜底: 用原生 setter 绕过框架拦截
            page.evaluate('''({id, val}) => {
                const inp = document.querySelector(id);
                if (inp) {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(inp, val);
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }''', {"id": date_id, "val": date_val})
            logger.info("JS 赋值 %s: %s", date_id, date_val)

    time.sleep(1)

    # 触发查询
    triggered = False
    for sel in [
        'button:has(.uf-search-light-2)',
        '.uf-search-light-2',
        'button:has-text("查询")',
        'button:has-text("搜索")',
        '[class*="search"]',
    ]:
        btn = page.query_selector(sel)
        if btn:
            try:
                btn.click()
                triggered = True
                logger.info("已点击查询按钮: %s", sel)
                break
            except Exception:
                continue

    if not triggered:
        page.keyboard.press("Enter")
        logger.info("已按 Enter 触发查询")

    time.sleep(3)
    wait_spinner_gone(page, timeout=15)


def change_page_size(page: Page, page_size: int = 100):
    time.sleep(1)
    for sel in [
        '.wui-pagination .wui-select',
        '[class*="pagination"] [class*="select"]',
        '.wui-table-footer [class*="select"]',
    ]:
        trigger = page.query_selector(sel)
        if trigger:
            trigger.click()
            time.sleep(0.5)
            break

    for opt_sel in [
        f'.wui-select-dropdown [title="{page_size}"]',
        f'[role="option"][title="{page_size}"]',
        f'.wui-select-item[title="{page_size}"]',
    ]:
        option = page.query_selector(opt_sel)
        if option:
            option.click()
            time.sleep(2)
            logger.info("每页显示 %d 条", page_size)
            return
    logger.warning("未能修改每页显示条数")


def go_to_next_page(page: Page) -> bool:
    for sel in [
        '.wui-pagination li:last-child:not([class*="disabled"])',
        '.wui-pagination [class*="next"]:not([class*="disabled"])',
        '[class*="pagination"] li[title="下一页"]:not([class*="disabled"])',
        'li[title="下一页"]:not([aria-disabled="true"])',
        '[class*="pagination"] li:last-child',
    ]:
        btn = page.query_selector(sel)
        if btn:
            cls = btn.get_attribute('class') or ''
            aria = btn.get_attribute('aria-disabled') or ''
            if 'disabled' in cls or aria == 'true':
                continue
            btn.click()
            time.sleep(2)
            return True
    return False


def wait_for_table(page: Page, timeout: float = 20.0):
    logger.info("等待表格加载...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = get_table_rows(page)
        if rows:
            logger.info("表格已就绪 (%d 行)", len(rows))
            return rows
        time.sleep(2)

    logger.error("表格未加载！正在 dump 页面信息...")
    try:
        logger.error("  当前 URL: %s", page.url)
        logger.error("  页面标题: %s", page.title())
        for sel in ['.wui-table', '[class*="table"]', 'table', '.wui-table-body']:
            cnt = len(page.query_selector_all(sel))
            if cnt > 0:
                logger.error("  %s: %d 个", sel, cnt)
        logger.error("  页面文本前500字: %s", page.inner_text('body')[:500])
    except Exception:
        pass
    return []
