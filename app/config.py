"""
应用配置模块
使用 Pydantic Settings 管理配置
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置"""

    # 应用配置
    app_name: str = "GPT Team 管理系统"
    app_version: str = "0.1.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8008
    debug: bool = True

    # 数据库配置
    # 建议在 Docker 中使用 data 目录挂载，以避免文件挂载权限或类型问题
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/team_manage.db"

    # 安全配置
    secret_key: str = "your-secret-key-here-change-in-production"
    admin_password: str = "admin123"

    # 日志配置
    log_level: str = "INFO"

    # 代理配置
    proxy: str = ""
    proxy_enabled: bool = False

    # JWT 配置
    jwt_verify_signature: bool = False

    # 时区配置
    timezone: str = "Asia/Shanghai"

    # Team 自动同步配置
    team_auto_sync_enabled: bool = True
    team_auto_sync_min_minutes: int = 5
    team_auto_sync_max_minutes: int = 10

    # 码支付配置
    mapay_id: str = ""  # 码支付商户ID
    mapay_key: str = ""  # 码支付通信密钥
    mapay_url: str = "https://api.mapay.top"  # 码支付API地址
    mapay_notify_url: str = ""  # 支付回调通知URL (如: https://yourdomain.com/api/payment/notify)
    mapay_return_url: str = ""  # 支付成功跳转URL (如: https://yourdomain.com/payment/result)
    mapay_price: float = 19.9  # 支付金额 (元)
    mapay_product_name: str = "GPT Team 会员"  # 商品名称

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# 创建全局配置实例
settings = Settings()
