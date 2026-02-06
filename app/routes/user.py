"""
用户路由
处理用户兑换页面
"""
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.config import settings

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(
    tags=["user"]
)


@router.get("/", response_class=HTMLResponse)
async def home_page(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    首页 - 融合支付和兑换码功能

    Args:
        request: FastAPI Request 对象
        db: 数据库会话

    Returns:
        首页 HTML
    """
    try:
        from app.main import templates
        from app.services.team import TeamService
        from app.services.settings import settings_service
        
        team_service = TeamService()
        remaining_spots = await team_service.get_total_available_spots(db)
        payment_methods = await settings_service.get_payment_methods_config(db)
        mapay_config = await settings_service.get_mapay_config(db)

        logger.info(f"用户访问首页，剩余车位: {remaining_spots}")

        return templates.TemplateResponse(
            "user/index.html",
            {
                "request": request,
                "remaining_spots": remaining_spots,
                "price": mapay_config["mapay_price"],
                "product_name": mapay_config["mapay_product_name"],
                "alipay_enabled": payment_methods["alipay_enabled"],
                "wxpay_enabled": payment_methods["wxpay_enabled"]
            }
        )

    except Exception as e:
        logger.error(f"渲染首页失败: {e}")
        return HTMLResponse(
            content=f"<h1>页面加载失败</h1><p>{str(e)}</p>",
            status_code=500
        )


@router.get("/redeem")
async def redeem_page():
    """
    兑换码页面（已废弃，重定向到首页）
    """
    return RedirectResponse(url="/", status_code=301)
