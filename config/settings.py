"""
项目配置
敏感信息建议通过环境变量注入，此处默认值仅用于开发阶段。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """读取本地 .env，已存在的环境变量不覆盖。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class VisionConfig:
    """永锋视觉系统配置（打包带项目）"""

    base_url: str = "http://vision.lg.china-yongfeng.com/packing-tape"
    username: str = "admin"
    password: str = "Cisdi_mv@8888"

    # F12 Headers 确认的完整路径（base_url 已包含 /packing-tape）
    login_endpoint: str = "/api/record/doLogin"
    history_endpoint: str = "/api/monitor/query-condition"
    statistic_endpoint: str = "/api/monitor/statistic"


@dataclass
class ScrapConfig:
    """废钢检判系统配置

    · 部署现场：镔鑫钢铁（与打包带 VPN 独立，走不同的专网）
    · 系统名：睿视废钢智能质检系统
    · 登录协议：POST form-urlencoded 明文密码 → 设置 Cookie satoken
      后续请求前端同时带 Cookie `satoken` 和 Header `token`，值相同
    """

    base_url: str = "http://172.31.1.102:8081"
    employee_id: str = "022499"
    password: str = "0123456"

    login_endpoint: str = "/fcs/auth/login"
    list_endpoint: str = "/fcs/intelligence/intelliTaskInfo/page"
    detail_endpoint: str = "/fcs/intelligence/intelliTaskInfo/getCheckDetail"

    # 统计过滤：人工主料型为以下 steelType 的车次不参与统计（仅保留在表格）
    # 2=杂摸, 4=中废（字典见 agent/scrap/dict.py）
    exclude_steel_types: tuple = (2, 4)

    # 业务目标值（用于"达标/未达标"判定）
    target_accuracy: float = 0.95
    target_avg_error_rate: float = 0.10
    target_weight_diff_kg: float = 100.0
    target_weight_ratio_lower: float = 0.5
    target_weight_ratio_upper: float = 1.5


@dataclass
class ShenglongConfig:
    """盛隆废钢检判系统配置

    · 部署现场：盛隆钢铁（与镔鑫、打包带 VPN 均独立，走第三张专网）
    · 系统名：睿视废钢智能质检系统（与镔鑫同产品，但后端不同部署）
    · 登录协议：POST + query string + Content-Length:0（镔鑫是 form-urlencoded body）
      token 位置：`data.tokenInfo.tokenValue`（镔鑫是 `data.tokenValue`）
      Cookie 名：`scrape-steel-token`（镔鑫是 `satoken`）
      后续请求同时带 Cookie + Header `token`，值相同
    """

    base_url: str = "http://172.16.16.101:3000"
    employee_id: str = "022499"
    password: str = "0123456"

    login_endpoint: str = "/api/auth/login"
    list_endpoint: str = "/api/intelligence/intelliTaskInfo/page"
    detail_endpoint: str = "/api/intelligence/intelliTaskInfo/getCheckDetail"

    cookie_name: str = "scrape-steel-token"

    # 业务目标值（图3注释）
    target_recognition_rate: float = 0.92  # 主料识别率 R 目标 ≥92%
    target_deduction_compliance_rate: float = 0.90  # 扣杂符合率目标 ≥90%
    # 扣杂准确判定：0.5 ≤ 比值 ≤ 1.5 OR |误差| < 0.151t（未到 151kg 都算正确）
    deduction_ratio_lower: float = 0.5
    deduction_ratio_upper: float = 1.5
    deduction_error_tolerance_ton: float = 0.151

    # 每天下完车次文件夹后，原样 scp 到推理测试机；失败只记日志不中断
    remote_host: str = "10.180.34.16"
    remote_port: int = 22
    remote_user: str = "cisdi"
    remote_image_root: str = (
        "/mnt/data01/embedded/projects/wangyutai/sl_feigang/test_images_full_car"
    )
    # 硬上限盖住「还在涨、只是慢」的传输。实习生电脑实测一天约 1 小时。
    # 真正的失败看 remote_scp_stall_sec：远程体积连续不增加。
    remote_scp_timeout_sec: int = 14400
    remote_scp_stall_sec: int = 600


@dataclass
class YongfengScrapConfig(ShenglongConfig):
    """永锋废钢图片下载配置，与盛隆配置完全分离。

    详情页智能判级照片 URL：
      /oss/waste-water-discharg/…?origin=http://10.233.224.206:9000
    该 origin 是 MinIO HTTP 原图源（下载已走 originImageUrl），不是盛隆
    sl_feigang 测试机。scp 主机用网页 origin 主机，远端目录用永锋专属路径，
    禁止写入盛隆 `sl_feigang`。ssh 用户网页未展示，沿用推理机 cisdi；
    失败只记日志，不中断本地下载。
    """

    base_url: str = "http://vision.lg.china-yongfeng.com/srape-steel"
    employee_id: str = "022232"
    password: str = "0123456"
    cookie_name: str = "satoken"

    remote_host: str = "10.233.224.206"
    remote_port: int = 22
    remote_user: str = "cisdi"
    remote_image_root: str = (
        "/mnt/data01/embedded/projects/wangyutai/yf_feigang/test_images_full_car"
    )
    remote_scp_timeout_sec: int = 1800


@dataclass
class VPNConfig:
    """aTrust VPN 配置"""

    gateway: str = "https://atrust.lg.china-yongfeng.com:1443"
    username: str = "杨雨昕"
    password: str = "cisdi123@8888"
    check_url: str = "http://vision.lg.china-yongfeng.com/packing-tape/"
    timeout: float = 5.0


@dataclass
class LLMConfig:
    """对话模型配置（默认 DeepSeek，OpenAI 兼容接口）"""

    api_key: str = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("ZHIPU_API_KEY", "")
    base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    model: str = os.getenv("LLM_MODEL", "deepseek-chat")


@dataclass
class TencentDocsConfig:
    """腾讯文档开放接口配置"""

    access_token: str = os.getenv("TENCENT_DOCS_ACCESS_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHQiOiI4NjUxNmY3ZWI2N2M0ZjZiOTIyNDk5ZjE3MDg2MjY2MiIsInR5cCI6MSwiZXhwIjoxNzgyNjE4MzI3LjYxNzAzLCJpYXQiOjE3ODAwMjYzMjcuNjE3MDMsInN1YiI6ImZhZGNkOWI1Yjg0MzQ1M2I4NzY2N2JmMjczZDQyOWJhIn0.jjjy2B4aZZ-WFBpsgO-rYg9-iLVDccOS4GzMiOj-7s0")
    client_id: str = os.getenv("TENCENT_DOCS_CLIENT_ID", "86516f7eb67c4f6b922499f170862662")
    open_id: str = os.getenv("TENCENT_DOCS_OPEN_ID", "fadcd9b5b843453b87667bf273d429ba")
    enabled: bool = os.getenv("TENCENT_DOCS_ENABLED", "0") == "1"


@dataclass
class YongfengConfig:
    """永锋烧结矿准确率配置"""

    analysis_base_url: str = "http://qbee.lg.china-yongfeng.com/"
    analysis_query_base_url: str = "http://iron.lg.china-yongfeng.com/"
    login_endpoint: str = "/auth/api/sysmgr/sso/login"
    account: str = "22499"
    password: str = "544bc44fb14b08be2557d80242c48783"
    visual_1_base_url: str = "http://vision.lg.china-yongfeng.com"
    visual_2_base_url: str = "http://vision.lg.china-yongfeng.com"
    visual_username: str = "test"
    visual_password: str = "Cisdi_mv@8888"
    analysis_token: str = (
        "Bear eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJjdXJyZW50VGltZU1pbGxpcyI6IjE3Nzk4Njg5MTU3MTIiLCJhY2Nlc3NDb2RlIjoicWIucWJlZS5hbGwiLCJuZWVkUmVmcmVzaCI6dHJ1ZSwiZXhwIjoxNzgwNDczNzE1LCJ1c2VySWQiOiJkZmM0OGU4ZDUxOTg0MmVkOTJiOTM4ZjA3ZDIxYmU4YyIsImFjY291bnRLZXkiOiJkZmM0OGU4ZDUxOTg0MmVkOTJiOTM4ZjA3ZDIxYmU4YyJ9.KKLmVZu8FA5zC07bNbTVik2-kHTpwVVj8AsX29XFQW8.Nzk3NDI0M2MzZjk0MTFlYmE2MWVmYTE2M2U5ZDIxYzM="
    )
    api_code: str = "st2"



@dataclass
class Settings:
    vision: VisionConfig = field(default_factory=VisionConfig)
    vpn: VPNConfig = field(default_factory=VPNConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tencent_docs: TencentDocsConfig = field(default_factory=TencentDocsConfig)
    yongfeng: YongfengConfig = field(default_factory=YongfengConfig)
    scrap: ScrapConfig = field(default_factory=ScrapConfig)
    shenglong: ShenglongConfig = field(default_factory=ShenglongConfig)
    yongfeng_scrap: YongfengScrapConfig = field(default_factory=YongfengScrapConfig)
    analysis_token: str = os.getenv("ANALYSIS_TOKEN", "")
    analysis_api_code: str = os.getenv("ANALYSIS_API_CODE", "st2")

    def validate(self) -> list[str]:
        """检查配置完整性，返回缺失项列表"""
        issues = []
        if not self.llm.api_key:
            issues.append("未配置 DEEPSEEK_API_KEY 环境变量")
        return issues


settings = Settings()
