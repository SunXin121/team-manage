"""
质保相关路由
处理用户质保查询请求
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.warranty import warranty_service

router = APIRouter(
    prefix="/warranty",
    tags=["warranty"]
)


class WarrantyCheckRequest(BaseModel):
    """质保查询请求"""
    email: Optional[EmailStr] = None
    code: Optional[str] = None
    query: Optional[str] = None


class WarrantyCheckRecord(BaseModel):
    """质保查询单条记录"""
    code: str
    has_warranty: bool
    warranty_valid: bool
    warranty_expires_at: Optional[str]
    status: str
    used_at: Optional[str]
    team_id: Optional[int]
    team_name: Optional[str]
    team_status: Optional[str]
    team_expires_at: Optional[str]
    email: Optional[str] = None
    status_reason: Optional[str] = None
    can_reuse: Optional[bool] = None
    source_type: Optional[str] = None


class WarrantyCheckResponse(BaseModel):
    """质保查询响应"""
    success: bool
    has_warranty: bool
    warranty_valid: bool
    warranty_expires_at: Optional[str]
    banned_teams: list
    can_reuse: bool
    original_code: Optional[str]
    records: list[WarrantyCheckRecord] = []
    message: Optional[str]
    error: Optional[str]


class WarrantyReinviteRequest(BaseModel):
    """售后重邀请求"""
    email: EmailStr
    code: Optional[str] = None


class WarrantyReinviteResponse(BaseModel):
    """售后重邀响应"""
    success: bool
    message: Optional[str]
    team_info: Optional[dict]
    error: Optional[str]


@router.post("/check", response_model=WarrantyCheckResponse)
async def check_warranty(
    request: WarrantyCheckRequest,
    db_session: AsyncSession = Depends(get_db)
):
    """
    检查质保状态
    
    用户可以通过邮箱或兑换码查询质保状态
    """
    try:
        # 兼容旧前端：如果传 query，则自动识别邮箱/兑换码
        email = request.email
        code = request.code
        if request.query and not email and not code:
            query_text = request.query.strip()
            if query_text:
                if "@" in query_text:
                    email = query_text
                else:
                    code = query_text

        # 验证至少提供一个参数
        if not email and not code:
            raise HTTPException(
                status_code=400,
                detail="必须提供邮箱或兑换码"
            )
        
        # 调用质保服务
        result = await warranty_service.check_warranty_status(
            db_session,
            email=email,
            code=code
        )
        
        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "查询失败")
            )
        
        return WarrantyCheckResponse(
            success=True,
            has_warranty=result.get("has_warranty", False),
            warranty_valid=result.get("warranty_valid", False),
            warranty_expires_at=result.get("warranty_expires_at"),
            banned_teams=result.get("banned_teams", []),
            can_reuse=result.get("can_reuse", False),
            original_code=result.get("original_code"),
            records=result.get("records", []),
            message=result.get("message"),
            error=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"查询质保状态失败: {str(e)}"
        )


@router.post("/query", response_model=WarrantyCheckResponse)
async def query_warranty(
    request: WarrantyCheckRequest,
    db_session: AsyncSession = Depends(get_db)
):
    """兼容旧前端 /warranty/query，转发到 /warranty/check"""
    return await check_warranty(request=request, db_session=db_session)


@router.post("/reinvite", response_model=WarrantyReinviteResponse)
async def reinvite_warranty(
    request: WarrantyReinviteRequest,
    db_session: AsyncSession = Depends(get_db)
):
    """售后重邀：满足售后条件时，发送新 Team 邀请"""
    try:
        result = await warranty_service.reinvite_after_sales(
            db_session,
            email=request.email,
            code=request.code
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "售后重邀失败")
            )

        return WarrantyReinviteResponse(
            success=True,
            message=result.get("message"),
            team_info=result.get("team_info"),
            error=None
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"售后重邀失败: {str(e)}"
        )
