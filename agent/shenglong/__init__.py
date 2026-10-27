"""盛隆废钢检判（睿视废钢 · 部署于盛隆钢铁）

与镔鑫部署的同款产品，但前端路径、认证 Cookie 名、后端 JSON 结构都不同，
独立成一个子包以避免干扰镔鑫（agent.scrap）模块。
"""

from agent.shenglong.downloader import (
    DayDownloadResult,
    TruckDownloadResult,
    download_images_by_date_range,
    download_truck_images,
    iter_download_images,
)
