"""
支付路由
处理支付订单创建和回调通知
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.payment import payment_service

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(
    prefix="/payment",
    tags=["payment"]
)


# 请求模型
class CreateOrderRequest(BaseModel):
    """创建订单请求"""
    email: EmailStr = Field(..., description="用户邮箱")
    pay_type: str = Field("alipay", description="支付方式: alipay/wxpay")
    price: Optional[float] = Field(None, description="支付金额 (可选)")
    product_name: Optional[str] = Field(None, description="商品名称 (可选)")


class QueryOrderRequest(BaseModel):
    """查询订单请求"""
    order_no: str = Field(..., description="订单号")


class QueryByEmailRequest(BaseModel):
    """根据邮箱查询订单请求"""
    email: EmailStr = Field(..., description="用户邮箱")


# 响应模型
class CreateOrderResponse(BaseModel):
    """创建订单响应"""
    success: bool
    order_no: Optional[str] = None
    pay_url: Optional[str] = None
    amount: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class OrderStatusResponse(BaseModel):
    """订单状态响应"""
    success: bool
    order_no: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[str] = None
    email: Optional[str] = None
    pay_type: Optional[str] = None
    created_at: Optional[str] = None
    paid_at: Optional[str] = None
    team_id: Optional[int] = None
    error: Optional[str] = None


@router.post("/create", response_model=CreateOrderResponse)
async def create_order(
    request: CreateOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    创建支付订单
    
    Args:
        request: 创建订单请求
        db: 数据库会话
        
    Returns:
        订单信息和支付链接
    """
    try:
        logger.info(f"创建支付订单: email={request.email}, pay_type={request.pay_type}")

        result = await payment_service.create_order(
            email=request.email,
            pay_type=request.pay_type,
            db_session=db,
            price=request.price,
            product_name=request.product_name
        )

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result["error"]
            )

        return CreateOrderResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建支付订单失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建订单失败: {str(e)}"
        )


@router.get("/notify")
async def payment_notify_get(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    支付回调通知 (GET方式)
    码支付默认使用GET方式回调
    """
    try:
        params = dict(request.query_params)
        logger.info(f"收到支付回调 (GET): {params}")

        result = await payment_service.handle_notify(params, db)

        if result["success"]:
            # 码支付要求返回 "success" 表示处理成功
            return PlainTextResponse("success")
        else:
            logger.warning(f"支付回调处理失败: {result.get('error')}")
            return PlainTextResponse("fail")

    except Exception as e:
        logger.error(f"处理支付回调失败: {e}")
        return PlainTextResponse("fail")


@router.post("/notify")
async def payment_notify_post(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    支付回调通知 (POST方式)
    """
    try:
        # 尝试解析表单数据
        try:
            form_data = await request.form()
            params = dict(form_data)
        except:
            # 如果表单解析失败，尝试解析JSON
            try:
                params = await request.json()
            except:
                # 最后尝试query参数
                params = dict(request.query_params)

        logger.info(f"收到支付回调 (POST): {params}")

        result = await payment_service.handle_notify(params, db)

        if result["success"]:
            return PlainTextResponse("success")
        else:
            logger.warning(f"支付回调处理失败: {result.get('error')}")
            return PlainTextResponse("fail")

    except Exception as e:
        logger.error(f"处理支付回调失败: {e}")
        return PlainTextResponse("fail")


@router.post("/status", response_model=OrderStatusResponse)
async def query_order_status(
    request: QueryOrderRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    查询订单状态
    
    Args:
        request: 查询请求
        db: 数据库会话
        
    Returns:
        订单状态信息
    """
    try:
        result = await payment_service.get_order_status(request.order_no, db)

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result["error"]
            )

        return OrderStatusResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询订单状态失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )


@router.get("/status/{order_no}", response_model=OrderStatusResponse)
async def get_order_status(
    order_no: str,
    db: AsyncSession = Depends(get_db)
):
    """
    查询订单状态 (GET方式)
    
    Args:
        order_no: 订单号
        db: 数据库会话
        
    Returns:
        订单状态信息
    """
    try:
        result = await payment_service.get_order_status(order_no, db)

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result["error"]
            )

        return OrderStatusResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询订单状态失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )


@router.post("/orders", response_model=Dict[str, Any])
async def query_orders_by_email(
    request: QueryByEmailRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    根据邮箱查询订单列表
    
    Args:
        request: 查询请求
        db: 数据库会话
        
    Returns:
        订单列表
    """
    try:
        result = await payment_service.get_orders_by_email(request.email, db)

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result["error"]
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询订单列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )



