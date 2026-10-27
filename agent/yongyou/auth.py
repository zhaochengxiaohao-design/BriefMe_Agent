"""Playwright 登录与 storage_state 缓存。"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

USER_SELECTOR = '#admin_id'
PASS_SELECTOR = '#admin_pass'
SUBMIT_SELECTOR = 'button:has-text("登录")'


def _dump_page_info(page: Page, label: str = ""):
    logger.info("=== DEBUG PAGE: %s ===", label)
    logger.info("  URL: %s", page.url)
    try:
        body = page.inner_text('body')[:300]
        logger.info("  body (300): %s", body)
    except Exception:
        pass


def _try_fill_login_form(page: Page, username: str, password: str) -> bool:
    user_input = page.query_selector(USER_SELECTOR)
    pass_input = page.query_selector(PASS_SELECTOR)

    if not user_input or not pass_input:
        logger.warning("未找到登录表单 (#admin_id=%s, #admin_pass=%s)",
                       bool(user_input), bool(pass_input))
        return False

    if not user_input.is_visible() or not pass_input.is_visible():
        logger.warning("登录表单不可见")
        return False

    logger.info("填写账号: %s", username)
    user_input.click()
    time.sleep(0.3)
    user_input.fill('')
    user_input.type(username, delay=80)

    logger.info("填写密码")
    pass_input.click()
    time.sleep(0.3)
    pass_input.fill('')
    pass_input.type(password, delay=80)

    btn = page.query_selector(SUBMIT_SELECTOR)
    if btn and btn.is_visible():
        btn.click()
        logger.info("已点击登录按钮")
        return True

    logger.info("未找到登录按钮，按 Enter 提交...")
    pass_input.press("Enter")
    return True


def _is_login_page(page: Page) -> bool:
    try:
        body = page.inner_text('body')[:500]
        dashboard_keywords = ['今日作业', '进场车次', '判级车次', '废钢重量',
                              '历史作业', '我的常用', '扣杂率']
        if any(kw in body for kw in dashboard_keywords):
            return False
    except Exception:
        pass

    url_now = page.url.lower()
    if "login" in url_now:
        return True

    if page.locator('#admin_id').count() > 0 and page.locator('#admin_pass').count() > 0:
        return True

    return False


def _wait_for_login_form(page: Page, timeout: float = 60.0):
    logger.info("等待登录表单渲染 (最多 %.0fs)...", timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        user_el = page.query_selector('#admin_id')
        pass_el = page.query_selector('#admin_pass')
        if user_el and pass_el:
            if user_el.is_visible() and pass_el.is_visible():
                logger.info("登录表单已就绪 (%s)", time.strftime("%H:%M:%S"))
                return True
            logger.debug("表单 DOM 存在但不可见，继续等待...")
        else:
            all_inputs = page.query_selector_all('input')
            if all_inputs:
                logger.debug("页面有 %d 个 input，但不是 #admin_id / #admin_pass", len(all_inputs))
        time.sleep(2)

    logger.warning("等待登录表单超时 (%.0fs)", timeout)
    return False


def ensure_logged_in(
    context: BrowserContext,
    page: Page,
    username: str,
    password: str,
    record_url: str,
    base_url: str,
    storage_state_file: Optional[Path] = None,
) -> None:
    login_url = f"{base_url}/imp-ib-iv-igs-fe/ibd/igs/scheme/#/login"
    logger.info("打开登录页: %s", login_url)
    page.goto(login_url, wait_until="commit", timeout=30000)

    if not _wait_for_login_form(page, timeout=60):
        logger.info("登录表单未出现，检查是否已有有效会话...")
        page.goto(record_url, wait_until="commit", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        time.sleep(3)
        if _is_login_page(page):
            _wait_for_login_form(page, timeout=30)
        else:
            logger.info("确认无需登录 (已有有效会话)")
            if storage_state_file is not None:
                storage_state_file.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(storage_state_file))
            return

    logger.info("开始自动登录...")

    if not _try_fill_login_form(page, username, password):
        _dump_page_info(page, "填写失败")
        raise RuntimeError(
            "无法填写登录表单。请检查:\n"
            "  1. 登录页 DOM 是否变更 (查看上方 DEBUG)\n"
            "  2. 网络是否可达"
        )

    logger.info("等待登录跳转...")
    for i in range(15):
        time.sleep(5)
        if not _is_login_page(page):
            logger.info("登录成功！")
            break
    else:
        _dump_page_info(page, "登录超时")
        raise RuntimeError("登录失败：提交后仍在登录页。请检查账号密码。")

    if storage_state_file is not None:
        storage_state_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(storage_state_file))
        logger.info("会话已缓存至 %s", storage_state_file)
