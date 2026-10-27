"""VPN 连通性检测与半自动连接辅助"""
from __future__ import annotations

import logging
import subprocess
from typing import Tuple

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)


class VPNManager:
    """
    VPN 连接管理器

    工作流程:
      1. check_connectivity() 检测是否能访问永锋视觉系统
      2. 若不通，通过 get_connection_prompt() 返回连接指引
      3. 用户在聊天中输入 TOTP 验证码或回复"已连接"
      4. try_connect() 尝试打开 aTrust 并验证连通
    """

    def check_connectivity(self) -> bool:
        """尝试 HTTP 请求视觉系统，判断 VPN 是否已连通"""
        try:
            resp = httpx.get(
                settings.vpn.check_url,
                timeout=settings.vpn.timeout,
                follow_redirects=True,
            )
            connected = resp.status_code < 500
        except Exception:
            connected = False

        logger.info("VPN 连通性: %s", "已连接" if connected else "未连接")
        return connected

    def get_connection_prompt(self) -> str:
        """生成 VPN 连接指引消息，展示在聊天界面"""
        return (
            "**VPN 未连接**，无法访问永锋视觉系统。\n\n"
            "请按以下步骤操作：\n"
            f"1. 打开 **aTrust** 客户端\n"
            f"2. 接入地址：`{settings.vpn.gateway}`\n"
            f"3. 用户名：`{settings.vpn.username}`\n"
            f"4. 密码：`{settings.vpn.password}`\n"
            f"5. 打开手机 **Google Authenticator** 获取 6 位验证码\n"
            f"6. 在 aTrust 中输入验证码并点击连接\n\n"
            "连接成功后，在此处回复 **「已连接」** 即可继续查询。"
        )

    def try_connect(self, totp_code: str) -> Tuple[bool, str]:
        """
        用户提供 TOTP 后的连接尝试。

        流程: 打开 aTrust -> 提示用户用验证码完成连接 -> 检测连通性
        Returns: (是否成功, 提示消息)
        """
        self._try_open_atrust()

        if self.check_connectivity():
            return True, "VPN 连接成功！"

        return False, (
            "尚未检测到 VPN 连通。\n\n"
            "请在 aTrust 客户端中完成以下操作：\n"
            f"- 用户名：`{settings.vpn.username}`\n"
            f"- 密码：`{settings.vpn.password}`\n"
            f"- 验证码：**{totp_code}**\n"
            "- 点击「连接」\n\n"
            "完成后回复 **「已连接」**。"
        )

    def verify_manual_connect(self) -> Tuple[bool, str]:
        """用户回复"已连接"后，验证 VPN 是否真正连通"""
        if self.check_connectivity():
            return True, "VPN 已连通！"
        return False, "仍然无法连接到视觉系统，请确认 aTrust 已连接后重试。"

    def _try_open_atrust(self) -> None:
        """尝试在 macOS 上打开 aTrust 客户端"""
        try:
            subprocess.Popen(
                ["open", "-a", "aTrust"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("已尝试打开 aTrust 客户端")
        except FileNotFoundError:
            logger.warning("未找到 aTrust，请手动打开")
        except Exception as e:
            logger.warning("打开 aTrust 失败: %s", e)
