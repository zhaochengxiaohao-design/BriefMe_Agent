"""BriefMe · 多场景数据统计助手（by 中冶赛迪） —— Gradio 入口

UI 五档一次到位：
  档位 1: 主题 (Soft / 深蓝-橙色 / Inter 字体)
  档位 2: 顶部品牌栏 + VPN 状态灯
  档位 3: 左右分栏（侧边栏+聊天）
  档位 4: 预设 prompt 快捷按钮
  档位 5: 产物下载面板（xlsx + 错判图 Gallery）

Agent 业务逻辑完全不改，仅重写 app.py 布局。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gradio as gr
import httpx

from agent.core import SteelCoilAgent, _parse_save_path
from agent.image_download_route import (
    is_ambiguous_multilabel_pack,
    is_dual_plant_image_or_pack_request,
    is_shenglong_image_download,
    is_shenglong_multilabel_pack,
    is_yongfeng_image_download,
    is_yongfeng_multilabel_pack,
)
from agent.shenglong.calculator import last_complete_7_days
from agent.shenglong.downloader import (
    iter_download_images as iter_shenglong_images,
    parse_requested_dates as parse_shenglong_dates,
)
from agent.shenglong.packager import pack_multilabel_from_disk as pack_shenglong_multilabel
from agent.yongfeng.downloader import (
    iter_download_images as iter_yongfeng_images,
    parse_requested_dates as parse_yongfeng_dates,
)
from agent.yongfeng.scrap_packager import pack_multilabel_from_disk as pack_yongfeng_multilabel
from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

agent = SteelCoilAgent()

DOWNLOADS_ROOT = Path("downloads")
SCRAP_ROOT = DOWNLOADS_ROOT / "scrap"
SHENGLONG_ROOT = DOWNLOADS_ROOT / "shenglong"
YONGFENG_ROOT = DOWNLOADS_ROOT / "yongfeng"
BXSTEEL_ROOT = DOWNLOADS_ROOT / "bxsteel"
YONGYOU_ROOT = DOWNLOADS_ROOT / "yongyou"


# =====================================================================
# VPN 状态探测
# =====================================================================
def _check_packing_vpn() -> bool:
    """永锋专网是否连通（探测打包带业务域名，与烧结矿/检判原图同一 VPN）"""
    try:
        return agent.vpn.check_connectivity()
    except Exception:
        return False


def _check_scrap_vpn() -> bool:
    """镔鑫废钢 VPN 是否连通（3s 内能访问前端页）"""
    try:
        r = httpx.get(
            f"{settings.scrap.base_url}/fcs-web/",
            timeout=3.0,
            follow_redirects=True,
        )
        return r.status_code < 500
    except Exception:
        return False


def _check_shenglong_vpn() -> bool:
    """盛隆废钢 VPN 是否连通（3s 内能访问前端根页）"""
    try:
        r = httpx.get(
            f"{settings.shenglong.base_url}/",
            timeout=3.0,
            follow_redirects=True,
        )
        return r.status_code < 500
    except Exception:
        return False


def _check_yongyou_vpn() -> bool:
    """用友 VPN 是否连通（3s 内能访问前端页）"""
    try:
        r = httpx.get(
            "http://172.26.46.12:8890/",
            timeout=3.0,
            follow_redirects=True,
        )
        return r.status_code < 500
    except Exception:
        return False


def _status_html() -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    pt_ok = _check_packing_vpn()
    sc_ok = _check_scrap_vpn()
    sl_ok = _check_shenglong_vpn()
    yy_ok = _check_yongyou_vpn()
    pt_badge = _badge("永锋 VPN", pt_ok)
    sc_badge = _badge("镔鑫 VPN", sc_ok)
    sl_badge = _badge("盛隆 VPN", sl_ok)
    yy_badge = _badge("用友 VPN", yy_ok)
    return (
        f'<div class="status-bar">'
        f'<span class="today-chip">📅 {today_str}</span>'
        f"{pt_badge}{sc_badge}{sl_badge}{yy_badge}"
        f"</div>"
    )


def _badge(label: str, ok: bool) -> str:
    cls = "ok" if ok else "fail"
    text = "已连接" if ok else "未连接"
    return (
        f'<span class="status-badge">'
        f'<span class="status-dot {cls}"></span>'
        f"{label}：{text}"
        f"</span>"
    )


# =====================================================================
# 产物扫描（档位 5）
# =====================================================================
IMG_EXTS = {".jpg", ".jpeg", ".png"}


def _is_visible_file(p: Path) -> bool:
    name = p.name
    if name.startswith(".") or name.startswith("~$"):
        return False
    return True


def _scan_latest_artifacts() -> Tuple[Optional[str], Optional[str], List[str]]:
    roots = [r for r in (SCRAP_ROOT, SHENGLONG_ROOT, YONGFENG_ROOT, BXSTEEL_ROOT, YONGYOU_ROOT) if r.exists()]
    if not roots:
        return None, None, []

    xlsxs: List[Path] = []
    pptxs: List[Path] = []
    imgs: List[Path] = []
    for root in roots:
        xlsxs.extend(p for p in root.rglob("*.xlsx") if _is_visible_file(p))
        pptxs.extend(p for p in root.rglob("*.pptx") if _is_visible_file(p))
        for ext in IMG_EXTS:
            imgs.extend(p for p in root.rglob(f"*{ext}") if _is_visible_file(p))

    xlsxs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_xlsx = str(xlsxs[0]) if xlsxs else None

    pptxs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_pptx = str(pptxs[0]) if pptxs else None

    imgs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    img_paths = [str(p) for p in imgs[:20]]

    return latest_xlsx, latest_pptx, img_paths


# =====================================================================
# 聊天主回调
# =====================================================================
def _normalize_chat_history(history: list):
    normalized = []
    for item in history or []:
        if isinstance(item, dict) and "role" in item and "content" in item:
            normalized.append({"role": item["role"], "content": item.get("content") or ""})
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            user_text = (item[0] or "").strip()
            assistant_text = (item[1] or "").strip()
            if user_text:
                normalized.append({"role": "user", "content": user_text})
            if assistant_text:
                normalized.append({"role": "assistant", "content": assistant_text})
    return normalized


def append_message(message: str, history: list):
    history = _normalize_chat_history(history)
    message = (message or "").strip()
    if not message:
        return "", history, ""
    return "", history + [{"role": "user", "content": message}], message


def clear_chat():
    xlsx, pptx, imgs = _scan_latest_artifacts()
    return "", [], {"vpn_state": "unknown", "messages": []}, xlsx, pptx, imgs


# =====================================================================
# 快捷 prompt（档位 4）—— 点按钮填入输入框，由用户二次回车发送
# =====================================================================
def _quick_prompts() -> Dict[str, Dict[str, Dict[str, str]]]:
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    week_start = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    today_s = date.today().strftime("%Y-%m-%d")
    sl_week_start, sl_week_end = last_complete_7_days()
    return {
        "永锋钢铁": {
            "打包带钢卷": {
                "昨日打包带情况": "发昨天的打包带情况",
                "下载昨日打包带异常图": "下载昨天打包带的异常图片",
            },
            "烧结矿颗粒度": {
                "昨日准确率报表": f"生成 {yesterday} 的烧结矿颗粒度人工筛分 vs 视觉准确率报表",
                "指定区间准确率报表": "生成 2026-04-01 到 2026-04-07 的烧结矿颗粒度人工筛分 vs 视觉准确率报表",
                "支持哪些指令": (
                    "请列出你支持的所有功能和典型用法示例。"
                    "分【打包带钢卷 @ 永锋】【烧结矿颗粒度 @ 永锋】【检判原图下载 @ 永锋】"
                    "【废钢检判 @ 镔鑫】【球机图像下载 @ 镔鑫】【用友检判统计 @ 镔鑫】【废钢检判 @ 盛隆】回答。"
                ),
            },
            "检判原图下载": {
                "下载昨日检判原图": f"下载 {yesterday} 的【永锋】检判原图",
                "下载近 7 天检判原图": (
                    f"下载 {sl_week_start} 到 {sl_week_end} 的【永锋】检判原图"
                ),
                "指定日期下载": "下载 2026-09-01 的【永锋】检判原图",
                "指定多个日期下载": (
                    "下载 2026-08-31、2026-09-01 的【永锋】检判原图"
                ),
                "确认打包多标签（全部已筛日期）": (
                    "确认打包保存目录下已筛完的【永锋】废钢多标签分类数据集"
                ),
                "确认打包指定日期多标签": (
                    "确认打包 2026-09-01 的【永锋】废钢多标签分类数据集"
                ),
            },
        },
        "镔鑫钢铁": {
            "废钢检判": {
                "昨日废钢检判情况": f"发 {yesterday} 的【镔鑫】废钢检判情况",
                "近 7 天报表 + 错判图": (
                    f"导出 {week_start} 到 {today_s} 的【镔鑫】废钢检判报表并下载错判图"
                ),
                "近 7 天 PPT 汇报页": (
                    f"按 {week_start} 到 {today_s} 的【镔鑫】检判结果生成对应的 PPT 汇报页"
                ),
                "近 7 天各料型统计+汇报": (
                    f"统计 {week_start} 到 {today_s} 的【镔鑫】各料型周度数据并导出报表"
                ),
                "支持哪些指令": (
                    "请列出你支持的所有功能和典型用法示例。"
                    "分【打包带钢卷 @ 永锋】【废钢检判 @ 镔鑫】【球机图像下载 @ 镔鑫】【用友检判统计 @ 镔鑫】【废钢检判 @ 盛隆】五节回答。"
                ),
            },
            "球机图像下载+重命名": {
                "下载昨日球机图像": f"下载 {yesterday} 的镔鑫球机图像",
                "下载球机图像": f"下载 {week_start} 到 {today_s} 的镔鑫球机图像",
            },
            "用友检判统计": {
                "下载昨日检判结果图": f"下载 {yesterday} 的用友检判结果图片并生成统计 Excel",
                "下载用友检判结果图": f"下载 {week_start} 到 {today_s} 的用友检判结果图片并生成统计 Excel",
            },
        },
        "盛隆钢铁": {
            "检判原图下载": {
                "下载昨日检判原图": f"下载 {yesterday} 的【盛隆】检判原图",
                "下载近 7 天检判原图": (
                    f"下载 {sl_week_start} 到 {sl_week_end} 的【盛隆】检判原图"
                ),
                "指定日期下载": "下载 2026-08-01 的【盛隆】检判原图",
                "指定多个日期下载": (
                    "下载 2026-08-01、2026-08-03、2026-08-05 的【盛隆】检判原图"
                ),
                "确认打包多标签（全部已筛日期）": (
                    "确认打包保存目录下已筛完的【盛隆】废钢多标签分类数据集"
                ),
                "确认打包指定日期多标签": (
                    "确认打包 2026-08-01、2026-08-03 的【盛隆】废钢多标签分类数据集"
                ),
            },
            "废钢检判": {
                "昨日废钢检判情况": f"发 {yesterday} 的【盛隆】废钢检判情况",
                "近 7 天报表": f"导出 {sl_week_start} 到 {sl_week_end} 的【盛隆】废钢检判报表",
                "主表（多周期累积）": (
                    "生成【盛隆】主表，把这两个周期累积到一个 xlsx："
                    "2026-04-14 至 2026-04-22、2026-04-23 至 2026-04-29"
                ),
                "重废归一化主表": (
                    "生成【盛隆】重废1/2/3归一化准确率主表，把这几个周期累积到一个 xlsx："
                    "准确率统计时排除人工检判结果中没有任意重废1/2/3料型的车次；"
                    "2026-04-14 至 2026-04-22、2026-04-23 至 2026-04-29、"
                    "2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13；"
                    "其中 2026-04-30 至 2026-05-06、2026-05-07 至 2026-05-13 "
                    "当作一个统计周期进行统计"
                ),
            }
        },
    }


# =====================================================================
# 主题 + CSS（档位 1、2）
# =====================================================================
THEME = gr.themes.Soft(
    primary_hue="blue",
    secondary_hue="orange",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
)

CUSTOM_CSS = """
/* 顶部品牌栏 */
.brand-wrap { padding: 4px 0 16px; }
.brand-title {
    font-size: 28px;
    font-weight: 800;
    background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 60%, #f59e0b 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: 1.5px;
    line-height: 1.2;
}
.brand-org {
    display: inline-block;
    margin: 4px 0 2px;
    padding: 2px 10px;
    border-radius: 4px;
    background: rgba(59, 130, 246, 0.08);
    border-left: 3px solid #3b82f6;
    color: #1e3a8a;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.brand-subtitle {
    color: #64748b;
    font-size: 14px;
    margin-top: 4px;
}
.proj-chip {
    display: inline-block;
    margin: 4px 6px 0 0;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: 0.2px;
}
.proj-chip.pt-chip {
    background: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
}
.proj-chip.sc-chip {
    background: #fff7ed;
    color: #c2410c;
    border: 1px solid #fed7aa;
}
.proj-chip.sl-chip {
    background: #f0fdf4;
    color: #15803d;
    border: 1px solid #bbf7d0;
}

/* 状态栏 */
.status-bar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
    padding-top: 8px;
}
.today-chip {
    padding: 4px 10px;
    border-radius: 999px;
    background: #eef2ff;
    color: #3730a3;
    font-size: 13px;
    font-weight: 500;
}
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    background: #f1f5f9;
    color: #334155;
    font-size: 13px;
}
.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
}
.status-dot.ok { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
.status-dot.fail { background: #ef4444; box-shadow: 0 0 6px #ef4444; }

/* 侧边栏标题 */
.side-title {
    font-size: 13px;
    font-weight: 600;
    color: #475569;
    letter-spacing: 0.6px;
    margin: 10px 0 6px;
    text-transform: uppercase;
}

/* 侧边栏项目归属卡片 */
.proj-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 8px;
}
.proj-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 0;
    font-size: 13px;
}
.proj-row + .proj-row {
    border-top: 1px dashed #e2e8f0;
}
.proj-tag {
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 12px;
}
.proj-tag.pt {
    background: #eff6ff;
    color: #1d4ed8;
}
.proj-tag.sc {
    background: #fff7ed;
    color: #c2410c;
}
.proj-tag.sl {
    background: #f0fdf4;
    color: #15803d;
}
.proj-arrow {
    color: #94a3b8;
    font-weight: 600;
}
.proj-site {
    color: #0f172a;
    font-weight: 500;
}

/* 让聊天气泡更紧凑 */
.chatbot .message-wrap { padding: 8px 12px; }
.chatbot .message.user { justify-content: flex-end; }
.chatbot .message.user .bubble {
    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
    color: #fff;
}
.chatbot .message.assistant .bubble {
    background: #eff6ff;
    color: #0f172a;
}
"""


# =====================================================================
# UI 组装
# =====================================================================
def build_ui() -> gr.Blocks:
    q = _quick_prompts()

    def _update_businesses(project):
        first_business = next(iter(q[project]))
        first_command = next(iter(q[project][first_business]))
        return (
            gr.update(choices=list(q[project].keys()), value=first_business),
            gr.update(choices=list(q[project][first_business].keys()), value=first_command),
            q[project][first_business][first_command],
        )

    def _update_commands(project, business):
        first_command = next(iter(q[project][business]))
        return (
            gr.update(choices=list(q[project][business].keys()), value=first_command),
            q[project][business][first_command],
        )

    def _select_command(project, business, command):
        return q[project][business][command]

    with gr.Blocks(
        title="BriefMe · 多场景数据统计助手",
        fill_height=True,
    ) as demo:

        # ------- 档位 2 · 顶部品牌栏 -------
        with gr.Row(elem_classes="brand-wrap"):
            with gr.Column(scale=6):
                gr.HTML(
                    '<div class="brand-title">BriefMe · 多场景数据统计助手</div>'
                    '<div class="brand-org">中冶赛迪（重庆）信息技术有限公司</div>'
                    '<div class="brand-subtitle">'
                    "一个入口，多钢厂多场景 — 当前已接入："
                    '<span class="proj-chip pt-chip">打包带钢卷 @ 永锋钢铁</span>'
                    '<span class="proj-chip pt-chip">检判原图 @ 永锋钢铁</span>'
                    '<span class="proj-chip sc-chip">废钢检判 @ 镔鑫钢铁</span>'
                    '<span class="proj-chip sc-chip">球机图像 @ 镔鑫钢铁</span>'
                    '<span class="proj-chip sc-chip">用友检判图 @ 镔鑫</span>'
                    '<span class="proj-chip sl-chip">废钢检判 @ 盛隆钢铁</span>'
                    "</div>"
                )
            with gr.Column(scale=4):
                status_display = gr.HTML(_status_html())
                refresh_btn = gr.Button("🔄 刷新 VPN 状态", size="sm")

        # ------- 档位 3 · 左右分栏 -------
        with gr.Row():
            # --- 左侧：业务下拉 + 状态说明 ---
            with gr.Column(scale=1, min_width=260):
                gr.HTML('<div class="side-title">快捷指令</div>')

                project_select = gr.Dropdown(
                    choices=list(q.keys()),
                    value="永锋钢铁",
                    label="项目",
                )
                business_select = gr.Dropdown(
                    choices=list(q["永锋钢铁"].keys()),
                    value="打包带钢卷",
                    label="业务",
                )
                command_select = gr.Dropdown(
                    choices=list(q["永锋钢铁"]["打包带钢卷"].keys()),
                    value="昨日打包带情况",
                    label="指令",
                )
                command_preview = gr.Textbox(
                    label="待发送内容",
                    value=q["永锋钢铁"]["打包带钢卷"]["昨日打包带情况"],
                    interactive=False,
                    lines=4,
                )
                fill_btn = gr.Button("填入输入框", variant="primary", size="sm")
                
                def _update_businesses(project):
                    first_business = next(iter(q[project]))
                    first_command = next(iter(q[project][first_business]))
                    return (
                        gr.update(choices=list(q[project].keys()), value=first_business),
                        gr.update(choices=list(q[project][first_business].keys()), value=first_command),
                        q[project][first_business][first_command],
                    )

                def _update_commands(project, business):
                    first_command = next(iter(q[project][business]))
                    return (
                        gr.update(choices=list(q[project][business].keys()), value=first_command),
                        q[project][business][first_command],
                    )

                def _select_command(project, business, command):
                    return q[project][business][command]

                project_select.change(
                    _update_businesses,
                    inputs=project_select,
                    outputs=[business_select, command_select, command_preview],
                    queue=False,
                )
                business_select.change(
                    _update_commands,
                    inputs=[project_select, business_select],
                    outputs=[command_select, command_preview],
                    queue=False,
                )
                command_select.change(
                    _select_command,
                    inputs=[project_select, business_select, command_select],
                    outputs=command_preview,
                    queue=False,
                )
                
                gr.HTML(
                    '<div class="side-title">业务归属</div>'
                    '<div class="proj-card">'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag pt">打包带钢卷</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">永锋钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag pt">烧结矿颗粒度</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">永锋钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag pt">检判原图下载</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">永锋钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag sc">废钢检判</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">镔鑫钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag sc">球机图像下载+重命名</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">镔鑫钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag sc">用友检判结果图</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">镔鑫钢铁</span>'
                    '  </div>'
                    '  <div class="proj-row">'
                    '    <span class="proj-tag sl">废钢检判</span>'
                    '    <span class="proj-arrow">→</span>'
                    '    <span class="proj-site">盛隆钢铁</span>'
                    '  </div>'
                    '</div>'
                )

                download_dir = gr.Textbox(
                    label="图像保存路径（盛隆/永锋检判原图必填）",
                    placeholder="/Users/你的用户名/Desktop/检判原图",
                    lines=1,
                )

                gr.HTML('<div class="side-title">使用提示</div>')
                gr.Markdown(
                    "- 问**打包带**：用「钢卷 / 打包带 / 打数 / 应打数 / 永锋」等词\n"
                    "- 问**镔鑫废钢**：带【镔鑫】字样\n"
                    "- 问**盛隆废钢**：带【盛隆】字样\n"
                    "- 只说「废钢」不指明时，会反问你是镔鑫还是盛隆\n"
                    "- 只说「检判原图」不指明时，会反问你是盛隆还是永锋\n"
                    "- **盛隆主表**支持任意周期累积，按钮里改/补日期即可\n"
                    "- **重废归一化主表**会排除人工无任意重废1/2/3的车次\n"
                    "- **镔鑫球机图像**：在指令中写明日期、工号、密码即可\n"
                    "- **用友检判统计**：在指令中写明日期、账号、密码，自动下载图片+生成带图的 Excel\n"
                    "- **盛隆/永锋检判原图**：先填路径再下载；多标签包要人工删图后点「确认打包」\n"
                    "- 按钮只是填好文字，**回车**发送"
                )

            # --- 右侧：聊天主区 ---
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    height=520,
                    show_label=False,
                    render_markdown=True,
                    elem_classes="chatbot",
                    avatar_images=(None, None),
                    placeholder=(
                        "<center><br/>"
                        "👋 你好，我是 BriefMe，中冶赛迪的多场景数据统计助手<br/>"
                        "左侧点一下「快捷指令」就能开始，"
                        "或直接在下方输入问题<br/>"
                        "</center>"
                    ),
                )

                with gr.Row():
                    msg = gr.Textbox(
                        placeholder="输入消息，如：发 2026-04-15 的废钢检判情况 ...",
                        show_label=False,
                        scale=9,
                        container=False,
                        autofocus=True,
                        lines=3,
                    )
                    submit_btn = gr.Button("发送", scale=1, variant="primary")

                with gr.Row():
                    clear_btn = gr.Button("🗑 清空对话", size="sm")

                def _fill_message(text):
                    return text

                # ------- 档位 5 · 产物下载面板 -------
                with gr.Accordion(
                    "📁 最近生成的报表 / PPT / 错判图片",
                    open=False,
                ):
                    xlsx_init, pptx_init, imgs_init = _scan_latest_artifacts()
                    report_file = gr.File(
                        label="最新 Excel 报表（点击下载）",
                        value=xlsx_init,
                        interactive=False,
                    )
                    pptx_file = gr.File(
                        label="最新 PPT 汇报页（点击下载，仅镔鑫场景产出）",
                        value=pptx_init,
                        interactive=False,
                    )
                    gallery = gr.Gallery(
                        label="最近 20 张错判渲染图（点击放大）",
                        value=imgs_init,
                        columns=4,
                        height=260,
                        show_label=True,
                        allow_preview=True,
                    )
                # ------- 下载图片包 -------
                with gr.Accordion("📦 下载图片包", open=False):
                    download_zip_file = gr.File(label="点击下载 ZIP 文件到本地", interactive=False)


                # ------- 下载实时日志面板 -------
                with gr.Accordion("📋 下载实时日志", open=False):
                    log_file_display = gr.Textbox(
                        label="",
                        value="等待下载...",
                        interactive=False,
                        lines=15,
                        max_lines=20,
                        autoscroll=True,
                        show_label=False,
                        elem_id="log_textbox"
                    )
                    refresh_btn = gr.Button("🔄 手动刷新日志", size="sm")
                    
                    def refresh_log():
                        try:
                            log_file = Path("download_log.txt")
                            if log_file.exists():
                                content = log_file.read_text(encoding="utf-8")
                                if content.strip():
                                    return content
                            return "等待下载..."
                        except Exception as e:
                            return f"读取失败: {e}"
                    
                    refresh_btn.click(
                        refresh_log, 
                        outputs=log_file_display,
                        js="() => { setTimeout(() => { const el = document.querySelector('#log_textbox textarea'); if(el) { el.scrollTop = el.scrollHeight; } }, 200); setTimeout(() => { const el = document.querySelector('#log_textbox textarea'); if(el) { el.scrollTop = el.scrollHeight; } }, 500); }"
                    )
        session = gr.State({"vpn_state": "unknown", "messages": []})

# --- 绑定：输入/按钮 ---
        def _sync_append(message, history):
            history = _normalize_chat_history(history)
            message = (message or "").strip()
            if not message:
                return "", history
            return "", history + [{"role": "user", "content": message}]

        def pack_multilabel_handler(message, history, session, save_dir, *, yongfeng: bool = False):
            history = _normalize_chat_history(history)
            dest = (save_dir or "").strip() or _parse_save_path(message)
            dates = (parse_yongfeng_dates if yongfeng else parse_shenglong_dates)(message)
            pack_fn = pack_yongfeng_multilabel if yongfeng else pack_shenglong_multilabel
            site = "永锋" if yongfeng else "盛隆"
            xlsx, pptx, imgs = _scan_latest_artifacts()
            if not dest:
                history.append({
                    "role": "assistant",
                    "content": (
                        "请先在左侧填写「图像保存路径」（须与下载时同一目录），"
                        f"再确认打包{site}废钢多标签分类数据集。"
                    ),
                })
                yield history, session, xlsx, pptx, imgs, None
                return
            try:
                result = pack_fn(dest, dates=dates or None)
            except Exception as exc:
                history.append({"role": "assistant", "content": f"❌ 打包失败: {exc}"})
                yield history, session, xlsx, pptx, imgs, None
                return
            lines = [f"✅ 已按人工筛完后的原图打包「废钢多标签分类数据集」（{site}）"]
            for day in result.get("days") or []:
                if day.get("skipped"):
                    lines.append(f"- {day.get('date')}: 没有可打包的图，已跳过")
                else:
                    lines.append(
                        f"- {day.get('date')}: {day.get('images')} 张 → {day.get('zip')}"
                    )
            if not result.get("zip_files"):
                lines.append("没有生成任何压缩包。请确认目录里还有车次图片。")
            history.append({"role": "assistant", "content": "\n".join(lines)})
            yield history, session, xlsx, pptx, imgs, None

        def download_image_handler(message, history, session, save_dir, *, yongfeng: bool):
            history = _normalize_chat_history(history)
            dest = (save_dir or "").strip() or _parse_save_path(message)
            dates = (parse_yongfeng_dates if yongfeng else parse_shenglong_dates)(message)
            iter_fn = iter_yongfeng_images if yongfeng else iter_shenglong_images
            site = "永锋" if yongfeng else "盛隆"
            source = "srape-steel" if yongfeng else "3000"
            example = "永锋图像" if yongfeng else "盛隆图像"
            xlsx, pptx, imgs = _scan_latest_artifacts()
            if not dest:
                history.append({
                    "role": "assistant",
                    "content": (
                        "请先在左侧填写「图像保存路径」，例如 "
                        f"`/Users/你的用户名/Desktop/{example}`，再发送下载指令。"
                        "图会立刻写到这个目录，中断后重新跑同一日期即可续传。"
                    ),
                })
                yield history, session, xlsx, pptx, imgs, None
                return
            if not dates:
                history.append({
                    "role": "assistant",
                    "content": (
                        f"请写明日期。单日：下载 2026-09-01 的【{site}】检判原图；"
                        f"多日：下载 2026-08-31、2026-09-01 的【{site}】检判原图；"
                        f"连续区间：下载 2026-08-31 到 2026-09-01 的【{site}】检判原图。"
                    ),
                })
                yield history, session, xlsx, pptx, imgs, None
                return

            date_label = "、".join(dates)
            note = ""
            if not yongfeng and "MINIO图像下载" in message:
                note = "已改为走 3000 业务系统，不再扫描 MinIO 桶。\n"
            history.append({
                "role": "assistant",
                "content": (
                    f"{note}📥 开始从 {source} 下载 {date_label} 的【{site}】智能判级原图\n"
                    f"保存目录：{dest}\n"
                    "每下一车就会写到磁盘，可在该目录看进度。"
                ),
            })
            yield history, session, xlsx, pptx, imgs, None

            lines = [history[-1]["content"]]
            try:
                for event in iter_fn(output_dir=dest, dates=dates):
                    if event.get("type") == "done":
                        result = event.get("result") or {}
                        zips = result.get("zip_files") or []
                        zip_text = "\n".join(f"- {p}" for p in zips) if zips else "- 无"
                        scp_text = result.get("scp_report") or ""
                        lines.append(
                            f"✅ 完成：新下载 {result.get('success', 0)} 张，"
                            f"跳过 {result.get('skipped_existing', 0)} 张，"
                            f"失败 {result.get('failed', 0)} 张\n"
                            f"进度日志：{result.get('output_dir')}/download_progress.log\n"
                            f"实例/边缘分割包：\n{zip_text}\n"
                            f"{scp_text}\n"
                            "「废钢多标签分类数据集」还没打。"
                            "请按日期、按车次打开文件夹删掉不合格图，"
                            "再点指令「确认打包多标签」或在对话里说确认打包。"
                        )
                    else:
                        msg_line = event.get("message") or ""
                        if msg_line:
                            lines.append(msg_line)
                    history[-1]["content"] = "\n".join(lines[-50:])
                    xlsx, pptx, imgs = _scan_latest_artifacts()
                    yield history, session, xlsx, pptx, imgs, None
            except Exception as exc:
                lines.append(f"❌ 下载中断：{exc}\n已写入的文件仍在 {dest}，重跑同一日期会跳过已有文件。")
                history[-1]["content"] = "\n".join(lines[-50:])
                xlsx, pptx, imgs = _scan_latest_artifacts()
                yield history, session, xlsx, pptx, imgs, None

        # ========== 其他功能处理器（普通）==========
        def normal_handler(user_message, history, session, save_dir):
            history = _normalize_chat_history(history)
            
            if not user_message:
                xlsx, pptx, imgs = _scan_latest_artifacts()
                return history, session, xlsx, pptx, imgs, None

            # 添加用户消息
            # history.append({"role": "user", "content": user_message})
            
            agent._default_shenglong_output_dir = (save_dir or "").strip()
            agent._default_yongfeng_output_dir = (save_dir or "").strip()
            reply, session = agent.chat(user_message, session)
            
            history.append({"role": "assistant", "content": str(reply)})
            xlsx, pptx, imgs = _scan_latest_artifacts()
            return history, session, xlsx, pptx, imgs, None

        # ========== 路由处理器 ==========
        def route_handler(_pending_message, history, session, save_dir):
            user_message = ""
            if history and len(history) > 0:
                last_item = history[-1]
                if isinstance(last_item, dict):
                    content = last_item.get("content", "")
                    if isinstance(content, list):
                        text_parts = [str(item.get("text", "")) for item in content if isinstance(item, dict) and "text" in item]
                        user_message = "".join(text_parts).strip()
                    else:
                        user_message = str(content).strip()

            agent._default_shenglong_output_dir = (save_dir or "").strip()
            agent._default_yongfeng_output_dir = (save_dir or "").strip()
            if is_dual_plant_image_or_pack_request(user_message):
                xlsx, pptx, imgs = _scan_latest_artifacts()
                history = _normalize_chat_history(history)
                history.append({
                    "role": "assistant",
                    "content": (
                        "这句话里同时出现了永锋和盛隆。"
                        "请分开发送：下载 【永锋】检判原图，或下载 【盛隆】检判原图。"
                    ),
                })
                yield history, session, xlsx, pptx, imgs, None
            elif is_ambiguous_multilabel_pack(user_message):
                xlsx, pptx, imgs = _scan_latest_artifacts()
                history = _normalize_chat_history(history)
                history.append({
                    "role": "assistant",
                    "content": (
                        "请指明钢厂后再确认打包："
                        "确认打包【永锋】废钢多标签分类数据集，"
                        "或确认打包【盛隆】废钢多标签分类数据集。"
                    ),
                })
                yield history, session, xlsx, pptx, imgs, None
            elif is_yongfeng_multilabel_pack(user_message):
                for result in pack_multilabel_handler(
                    user_message, history, session, save_dir, yongfeng=True
                ):
                    yield result
            elif is_shenglong_multilabel_pack(user_message):
                for result in pack_multilabel_handler(
                    user_message, history, session, save_dir, yongfeng=False
                ):
                    yield result
            elif is_yongfeng_image_download(user_message):
                for result in download_image_handler(
                    user_message, history, session, save_dir, yongfeng=True
                ):
                    yield result
            elif is_shenglong_image_download(user_message):
                for result in download_image_handler(
                    user_message, history, session, save_dir, yongfeng=False
                ):
                    yield result
            else:
                result = normal_handler(user_message, history, session, save_dir)
                yield result

        # 绑定
        # msg.submit(
        #     _sync_append, 
        #     inputs=[msg, chatbot], 
        #     outputs=[msg, chatbot], 
        #     queue=False
        # ).then(
        #     route_handler,
        #     inputs=[msg, chatbot, session],
        #     outputs=[chatbot, session, report_file, pptx_file, gallery, download_zip_file],
        # )
        
        submit_btn.click(
            _sync_append,
            inputs=[msg, chatbot],
            outputs=[msg, chatbot], 
            queue=False,
        ).then(
            route_handler,
            inputs=[msg, chatbot, session, download_dir],
            outputs=[chatbot, session, report_file, pptx_file, gallery, download_zip_file],
        )

        clear_btn.click(
            clear_chat,
            outputs=[msg, chatbot, session, report_file, pptx_file, gallery],
        )

        refresh_btn.click(_status_html, outputs=status_display)

        def _update_businesses(project):
            first_business = next(iter(q[project]))
            first_command = next(iter(q[project][first_business]))
            return (
                gr.update(choices=list(q[project].keys()), value=first_business),
                gr.update(choices=list(q[project][first_business].keys()), value=first_command),
                q[project][first_business][first_command],
            )

        def _update_commands(project, business):
            first_command = next(iter(q[project][business]))
            return (
                gr.update(choices=list(q[project][business].keys()), value=first_command),
                q[project][business][first_command],
            )

        def _select_command(project, business, command):
            return q[project][business][command]

        project_select.change(
            _update_businesses,
            inputs=project_select,
            outputs=[business_select, command_select, command_preview],
            queue=False,
        )
        business_select.change(
            _update_commands,
            inputs=[project_select, business_select],
            outputs=[command_select, command_preview],
            queue=False,
        )
        command_select.change(
            _select_command,
            inputs=[project_select, business_select, command_select],
            outputs=command_preview,
            queue=False,
        )
        fill_btn.click(_fill_message, inputs=command_preview, outputs=msg, queue=False)

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=THEME,
        css=CUSTOM_CSS,
    )