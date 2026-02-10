"""
管理员路由
处理管理员面板的所有页面和操作
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import json
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.database import get_db
from app.dependencies.auth import require_admin
from app.services.team import TeamService
from app.services.redemption import RedemptionService
from app.services.invite_record import invite_record_service
from app.utils.time_utils import get_now

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(
    prefix="/admin",
    tags=["admin"]
)

import json

# 服务实例
team_service = TeamService()
redemption_service = RedemptionService()


# 请求模型
class TeamImportRequest(BaseModel):
    """Team 导入请求"""
    import_type: str = Field(..., description="导入类型: single 或 batch")
    access_token: Optional[str] = Field(None, description="AT Token (单个导入)")
    refresh_token: Optional[str] = Field(None, description="Refresh Token (单个导入)")
    session_token: Optional[str] = Field(None, description="Session Token (单个导入)")
    client_id: Optional[str] = Field(None, description="Client ID (单个导入)")
    email: Optional[str] = Field(None, description="邮箱 (单个导入)")
    account_id: Optional[str] = Field(None, description="Account ID (单个导入)")
    content: Optional[str] = Field(None, description="批量导入内容")


class AddMemberRequest(BaseModel):
    """添加成员请求"""
    email: str = Field(..., description="成员邮箱")


class CodeGenerateRequest(BaseModel):
    """兑换码生成请求"""
    type: str = Field(..., description="生成类型: single 或 batch")
    code: Optional[str] = Field(None, description="自定义兑换码 (单个生成)")
    count: Optional[int] = Field(None, description="生成数量 (批量生成)")
    expires_days: Optional[int] = Field(None, description="有效期天数")
    has_warranty: bool = Field(False, description="是否为质保兑换码")
    warranty_days: int = Field(30, description="质保天数")


class TeamUpdateRequest(BaseModel):
    """Team 更新请求"""
    email: Optional[str] = Field(None, description="新邮箱")
    account_id: Optional[str] = Field(None, description="新 Account ID")
    access_token: Optional[str] = Field(None, description="新 Access Token")
    refresh_token: Optional[str] = Field(None, description="新 Refresh Token")
    session_token: Optional[str] = Field(None, description="新 Session Token")
    client_id: Optional[str] = Field(None, description="新 Client ID")
    max_members: Optional[int] = Field(None, description="最大成员数")
    team_name: Optional[str] = Field(None, description="Team 名称")
    status: Optional[str] = Field(None, description="状态: active/full/expired/error/banned")


class CodeUpdateRequest(BaseModel):
    """兑换码更新请求"""
    has_warranty: bool = Field(..., description="是否为质保兑换码")
    warranty_days: Optional[int] = Field(None, description="质保天数")

class BulkCodeUpdateRequest(BaseModel):
    """批量兑换码更新请求"""
    codes: List[str] = Field(..., description="兑换码列表")
    has_warranty: bool = Field(..., description="是否为质保兑换码")
    warranty_days: Optional[int] = Field(None, description="质保天数")


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    page: int = 1,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    member_email: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    管理员面板首页
    """
    try:
        from app.main import templates
        logger.info(f"管理员访问账号管理, search={search}, status_filter={status_filter}, page={page}")

        # 设置每页数量
        per_page = 20
        
        # 获取 Team 列表 (分页)
        teams_result = await team_service.get_all_teams(db, page=page, per_page=per_page, search=search, status_filter=status_filter, member_email=member_email)
        
        # 获取统计信息 (可以使用专用统计方法优化)
        all_teams_result = await team_service.get_all_teams(db, page=1, per_page=10000)
        all_teams = all_teams_result.get("teams", [])

        # 计算统计数据
        stats = {
            "total_teams": len(all_teams),
            "available_teams": len([t for t in all_teams if t.get("status") == "active" and t.get("current_members", 0) < t.get("max_members", 6)]),
            "banned_teams": len([t for t in all_teams if t.get("status") == "banned"]),
            "error_teams": len([t for t in all_teams if t.get("status") == "error"])
        }

        return templates.TemplateResponse(
            "admin/index.html",
            {
                "request": request,
                "user": current_user,
                "active_page": "dashboard",
                "teams": teams_result.get("teams", []),
                "stats": stats,
                "search": search,
                "pagination": {
                    "current_page": teams_result.get("current_page", page),
                    "total_pages": teams_result.get("total_pages", 1),
                    "total": teams_result.get("total", 0),
                    "per_page": per_page
                }
            }
        )
    except Exception as e:
        logger.error(f"加载管理员面板失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"加载管理员面板失败: {str(e)}"
        )


@router.post("/teams/{team_id}/delete")
async def delete_team(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    删除 Team

    Args:
        team_id: Team ID
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        删除结果
    """
    try:
        logger.info(f"管理员删除 Team: {team_id}")

        result = await team_service.delete_team(team_id, db)

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"删除 Team 失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"删除 Team 失败: {str(e)}"
            }
        )


@router.get("/teams/{team_id}/info")
async def get_team_info(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """获取 Team 详情 (包含解密后的 Token)"""
    try:
        result = await team_service.get_team_by_id(team_id, db)
        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=result
            )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": str(e)}
        )


@router.post("/teams/{team_id}/update")
async def update_team(
    team_id: int,
    update_data: TeamUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """更新 Team 信息"""
    try:
        result = await team_service.update_team(
            team_id=team_id,
            db_session=db,
            email=update_data.email,
            account_id=update_data.account_id,
            access_token=update_data.access_token,
            refresh_token=update_data.refresh_token,
            session_token=update_data.session_token,
            client_id=update_data.client_id,
            max_members=update_data.max_members,
            team_name=update_data.team_name,
            status=update_data.status
        )
        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": str(e)}
        )




@router.post("/teams/import")
async def team_import(
    import_data: TeamImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    处理 Team 导入

    Args:
        import_data: 导入数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        导入结果
    """
    try:
        logger.info(f"管理员导入 Team: {import_data.import_type}")

        if import_data.import_type == "single":
            # 单个导入
            if not import_data.access_token:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": "Access Token 不能为空"
                    }
                )

            result = await team_service.import_team_single(
                access_token=import_data.access_token,
                db_session=db,
                email=import_data.email,
                account_id=import_data.account_id,
                refresh_token=import_data.refresh_token,
                session_token=import_data.session_token,
                client_id=import_data.client_id
            )

            if not result["success"]:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content=result
                )

            return JSONResponse(content=result)

        elif import_data.import_type == "batch":
            # 批量导入使用 StreamingResponse
            async def progress_generator():
                async for status_item in team_service.import_team_batch(
                    text=import_data.content,
                    db_session=db
                ):
                    yield json.dumps(status_item, ensure_ascii=False) + "\n"

            return StreamingResponse(
                progress_generator(),
                media_type="application/x-ndjson"
            )

        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": "无效的导入类型"
                }
            )

    except Exception as e:
        logger.error(f"导入 Team 失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"导入失败: {str(e)}"
            }
        )





@router.get("/teams/{team_id}/members/list")
async def team_members_list(
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    获取 Team 成员列表 (JSON)

    Args:
        team_id: Team ID
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        成员列表 JSON
    """
    try:
        # 获取成员列表
        result = await team_service.get_team_members(team_id, db)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"获取成员列表失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"获取成员列表失败: {str(e)}"
            }
        )


@router.post("/teams/{team_id}/members/add")
async def add_team_member(
    team_id: int,
    member_data: AddMemberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    添加 Team 成员

    Args:
        team_id: Team ID
        member_data: 成员数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        添加结果
    """
    try:
        logger.info(f"管理员添加成员到 Team {team_id}: {member_data.email}")

        result = await team_service.add_team_member(
            team_id=team_id,
            email=member_data.email,
            db_session=db
        )

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"添加成员失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"添加成员失败: {str(e)}"
            }
        )


@router.post("/teams/{team_id}/members/{user_id}/delete")
async def delete_team_member(
    team_id: int,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    删除 Team 成员

    Args:
        team_id: Team ID
        user_id: 用户 ID
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        删除结果
    """
    try:
        logger.info(f"管理员从 Team {team_id} 删除成员: {user_id}")

        result = await team_service.delete_team_member(
            team_id=team_id,
            user_id=user_id,
            db_session=db
        )

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"删除成员失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"删除成员失败: {str(e)}"
            }
        )


@router.post("/teams/{team_id}/invites/revoke")
async def revoke_team_invite(
    team_id: int,
    member_data: AddMemberRequest, # 使用相同的包含 email 的模型
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    撤回 Team 邀请

    Args:
        team_id: Team ID
        member_data: 成员数据 (包含 email)
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        撤回结果
    """
    try:
        logger.info(f"管理员从 Team {team_id} 撤回邀请: {member_data.email}")

        result = await team_service.revoke_team_invite(
            team_id=team_id,
            email=member_data.email,
            db_session=db
        )

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"撤回邀请失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"撤回邀请失败: {str(e)}"
            }
        )


# ==================== 兑换码管理路由 ====================

@router.get("/codes", response_class=HTMLResponse)
async def codes_list_page(
    request: Request,
    page: int = 1,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    兑换码列表页面

    Args:
        request: FastAPI Request 对象
        page: 页码
        search: 搜索关键词
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        兑换码列表页面 HTML
    """
    try:
        from app.main import templates

        logger.info(f"管理员访问兑换码列表页面, search={search}")

        # 获取兑换码 (分页)
        per_page = 50
        codes_result = await redemption_service.get_all_codes(db, page=page, per_page=per_page, search=search)
        codes = codes_result.get("codes", [])
        total_codes = codes_result.get("total", 0)
        total_pages = codes_result.get("total_pages", 1)
        current_page = codes_result.get("current_page", 1)

        # 为了统计数据，我们需要获取所有统计（或者增加统计接口）
        # 这里暂时获取全部用于统计
        all_codes_result = await redemption_service.get_all_codes(db, page=1, per_page=10000)
        all_codes = all_codes_result.get("codes", [])

        # 计算统计数据
        stats = {
            "total": total_codes,
            "unused": len([c for c in all_codes if c["status"] == "unused"]),
            "used": len([c for c in all_codes if c["status"] == "used"]),
            "expired": len([c for c in all_codes if c["status"] == "expired"])
        }

        # 格式化日期时间
        from datetime import datetime
        for code in codes:
            if code.get("created_at"):
                dt = datetime.fromisoformat(code["created_at"])
                code["created_at"] = dt.strftime("%Y-%m-%d %H:%M")
            if code.get("expires_at"):
                dt = datetime.fromisoformat(code["expires_at"])
                code["expires_at"] = dt.strftime("%Y-%m-%d %H:%M")
            if code.get("used_at"):
                dt = datetime.fromisoformat(code["used_at"])
                code["used_at"] = dt.strftime("%Y-%m-%d %H:%M")

        return templates.TemplateResponse(
            "admin/codes/index.html",
            {
                "request": request,
                "user": current_user,
                "active_page": "codes",
                "codes": codes,
                "stats": stats,
                "search": search,
                "pagination": {
                    "current_page": current_page,
                    "total_pages": total_pages,
                    "total": total_codes,
                    "per_page": per_page
                }
            }
        )

    except Exception as e:
        logger.error(f"加载兑换码列表页面失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"加载页面失败: {str(e)}"
        )




@router.post("/codes/generate")
async def generate_codes(
    generate_data: CodeGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    处理兑换码生成

    Args:
        generate_data: 生成数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        生成结果
    """
    try:
        logger.info(f"管理员生成兑换码: {generate_data.type}")

        if generate_data.type == "single":
            # 单个生成
            result = await redemption_service.generate_code_single(
                db_session=db,
                code=generate_data.code,
                expires_days=generate_data.expires_days,
                has_warranty=generate_data.has_warranty,
                warranty_days=generate_data.warranty_days
            )

            if not result["success"]:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content=result
                )

            return JSONResponse(content=result)

        elif generate_data.type == "batch":
            # 批量生成
            if not generate_data.count:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": "生成数量不能为空"
                    }
                )

            result = await redemption_service.generate_code_batch(
                db_session=db,
                count=generate_data.count,
                expires_days=generate_data.expires_days,
                has_warranty=generate_data.has_warranty,
                warranty_days=generate_data.warranty_days
            )

            if not result["success"]:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content=result
                )

            return JSONResponse(content=result)

        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": "无效的生成类型"
                }
            )

    except Exception as e:
        logger.error(f"生成兑换码失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"生成失败: {str(e)}"
            }
        )


@router.post("/codes/{code}/delete")
async def delete_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    删除兑换码

    Args:
        code: 兑换码
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        删除结果
    """
    try:
        logger.info(f"管理员删除兑换码: {code}")

        result = await redemption_service.delete_code(code, db)

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"删除兑换码失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": f"删除失败: {str(e)}"
            }
        )


@router.get("/codes/export")
async def export_codes(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    导出兑换码为Excel文件

    Args:
        search: 搜索关键词
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        兑换码Excel文件
    """
    try:
        from fastapi.responses import Response
        from datetime import datetime
        import xlsxwriter
        from io import BytesIO

        logger.info("管理员导出兑换码为Excel")

        # 获取所有兑换码 (导出不分页，传入大数量)
        codes_result = await redemption_service.get_all_codes(db, page=1, per_page=100000, search=search)
        all_codes = codes_result.get("codes", [])
        
        # 结果可能带统计信息，我们只取 codes

        # 创建Excel文件到内存
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('兑换码列表')

        # 定义格式
        header_format = workbook.add_format({
            'bold': True,
            'fg_color': '#4F46E5',
            'font_color': 'white',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })

        cell_format = workbook.add_format({
            'align': 'left',
            'valign': 'vcenter',
            'border': 1
        })

        # 设置列宽
        worksheet.set_column('A:A', 25)  # 兑换码
        worksheet.set_column('B:B', 12)  # 状态
        worksheet.set_column('C:C', 18)  # 创建时间
        worksheet.set_column('D:D', 18)  # 过期时间
        worksheet.set_column('E:E', 30)  # 使用者邮箱
        worksheet.set_column('F:F', 18)  # 使用时间
        worksheet.set_column('G:G', 12)  # 质保时长

        # 写入表头
        headers = ['兑换码', '状态', '创建时间', '过期时间', '使用者邮箱', '使用时间', '质保时长(天)']
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)

        # 写入数据
        for row, code in enumerate(all_codes, start=1):
            status_text = {
                'unused': '未使用',
                'used': '已使用',
                'expired': '已过期'
            }.get(code['status'], code['status'])

            worksheet.write(row, 0, code['code'], cell_format)
            worksheet.write(row, 1, status_text, cell_format)
            worksheet.write(row, 2, code.get('created_at', '-'), cell_format)
            worksheet.write(row, 3, code.get('expires_at', '永久有效'), cell_format)
            worksheet.write(row, 4, code.get('used_by_email', '-'), cell_format)
            worksheet.write(row, 5, code.get('used_at', '-'), cell_format)
            worksheet.write(row, 6, code.get('warranty_days', '-') if code.get('has_warranty') else '-', cell_format)

        # 关闭workbook
        workbook.close()

        # 获取Excel数据
        excel_data = output.getvalue()
        output.close()

        # 生成文件名
        filename = f"redemption_codes_{get_now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        # 返回Excel文件
        return Response(
            content=excel_data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    except Exception as e:
        logger.error(f"导出兑换码失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导出失败: {str(e)}"
        )


@router.post("/codes/{code}/update")
async def update_code(
    code: str,
    update_data: CodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """更新兑换码信息"""
    try:
        result = await redemption_service.update_code(
            code=code,
            db_session=db,
            has_warranty=update_data.has_warranty,
            warranty_days=update_data.warranty_days
        )
        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": str(e)}
        )

@router.post("/codes/bulk-update")
async def bulk_update_codes(
    update_data: BulkCodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """批量更新兑换码信息"""
    try:
        result = await redemption_service.bulk_update_codes(
            codes=update_data.codes,
            db_session=db,
            has_warranty=update_data.has_warranty,
            warranty_days=update_data.warranty_days
        )
        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": str(e)}
        )


@router.get("/invite-records", response_class=HTMLResponse)
async def invite_records_page(
    request: Request,
    email: Optional[str] = None,
    code: Optional[str] = None,
    order_no: Optional[str] = None,
    source_type: Optional[str] = None,
    team_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: Optional[str] = "1",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    邀请记录页面

    Args:
        request: FastAPI Request 对象
        email: 邮箱筛选
        code: 兑换码筛选
        team_id: Team ID 筛选
        start_date: 开始日期
        end_date: 结束日期
        page: 页码
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        邀请记录页面 HTML
    """
    try:
        from app.main import templates
        from datetime import datetime

        # 解析参数
        try:
            actual_team_id = int(team_id) if team_id and team_id.strip() else None
        except (ValueError, TypeError):
            actual_team_id = None
            
        try:
            page_int = int(page) if page and page.strip() else 1
        except (ValueError, TypeError):
            page_int = 1
            
        logger.info(f"管理员访问邀请记录页面 (page={page_int})")

        # 获取记录（统一查询 invite_records）
        records_result = await invite_record_service.get_invite_records(
            db_session=db,
            email=email,
            source_code=code,
            order_no=order_no,
            team_id=actual_team_id,
            source_type=source_type,
            start_date=start_date,
            end_date=end_date,
            page=page_int,
            per_page=20
        )

        if not records_result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=records_result.get("error", "获取邀请记录失败")
            )

        stats_result = await invite_record_service.get_invite_stats(
            db_session=db,
            email=email,
            source_code=code,
            order_no=order_no,
            team_id=actual_team_id,
            source_type=source_type,
            start_date=start_date,
            end_date=end_date
        )

        stats = stats_result.get("stats", {
            "total": 0,
            "today": 0,
            "this_week": 0,
            "this_month": 0
        })

        paginated_records = records_result.get("records", [])

        for record in paginated_records:
            try:
                dt = datetime.fromisoformat(record["invited_at"]) if record.get("invited_at") else None
                if dt:
                    record["invited_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        total_records = records_result.get("total", 0)
        total_pages = records_result.get("total_pages", 1)

        return templates.TemplateResponse(
            "admin/invite_records/index.html",
            {
                "request": request,
                "user": current_user,
                "active_page": "invite_records",
                "records": paginated_records,
                "stats": stats,
                "filters": {
                    "email": email,
                    "code": code,
                    "order_no": order_no,
                    "source_type": source_type,
                    "team_id": team_id,
                    "start_date": start_date,
                    "end_date": end_date
                },
                "pagination": {
                    "current_page": records_result.get("current_page", page_int),
                    "total_pages": total_pages,
                    "total": total_records,
                    "per_page": records_result.get("per_page", 20)
                }
            }
        )

    except Exception as e:
        logger.error(f"获取邀请记录失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取邀请记录失败: {str(e)}"
        )


@router.get("/records", response_class=HTMLResponse)
async def records_page_redirect(
    request: Request,
    email: Optional[str] = None,
    code: Optional[str] = None,
    team_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: Optional[str] = "1",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """兼容旧路径，转为邀请记录页面"""
    return await invite_records_page(
        request=request,
        email=email,
        code=code,
        order_no=None,
        source_type=None,
        team_id=team_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        db=db,
        current_user=current_user
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    系统设置页面

    Args:
        request: FastAPI Request 对象
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        系统设置页面 HTML
    """
    try:
        from app.main import templates
        from app.services.settings import settings_service

        logger.info("管理员访问系统设置页面")

        # 获取当前配置
        proxy_config = await settings_service.get_proxy_config(db)
        log_level = await settings_service.get_log_level(db)
        mapay_config = await settings_service.get_mapay_config(db)
        payment_methods = await settings_service.get_payment_methods_config(db)

        return templates.TemplateResponse(
            "admin/settings/index.html",
            {
                "request": request,
                "user": current_user,
                "active_page": "settings",
                "proxy_enabled": proxy_config["enabled"],
                "proxy": proxy_config["proxy"],
                "log_level": log_level,
                "mapay_id": mapay_config["mapay_id"],
                "mapay_key": mapay_config["mapay_key"],
                "mapay_url": mapay_config["mapay_url"],
                "mapay_domain": mapay_config["mapay_domain"],
                "mapay_price": mapay_config["mapay_price"],
                "mapay_product_name": mapay_config["mapay_product_name"],
                "alipay_enabled": payment_methods["alipay_enabled"],
                "wxpay_enabled": payment_methods["wxpay_enabled"]
            }
        )

    except Exception as e:
        logger.error(f"获取系统设置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统设置失败: {str(e)}"
        )


class ProxyConfigRequest(BaseModel):
    """代理配置请求"""
    enabled: bool = Field(..., description="是否启用代理")
    proxy: str = Field("", description="代理地址")


class LogLevelRequest(BaseModel):
    """日志级别请求"""
    level: str = Field(..., description="日志级别")


@router.post("/settings/proxy")
async def update_proxy_config(
    proxy_data: ProxyConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    更新代理配置

    Args:
        proxy_data: 代理配置数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        更新结果
    """
    try:
        from app.services.settings import settings_service

        logger.info(f"管理员更新代理配置: enabled={proxy_data.enabled}, proxy={proxy_data.proxy}")

        # 验证代理地址格式
        if proxy_data.enabled and proxy_data.proxy:
            proxy = proxy_data.proxy.strip()
            if not (proxy.startswith("http://") or proxy.startswith("https://") or proxy.startswith("socks5://") or proxy.startswith("socks5h://")):
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": "代理地址格式错误,应为 http://host:port, socks5://host:port 或 socks5h://host:port"
                    }
                )

        # 更新配置
        success = await settings_service.update_proxy_config(
            db,
            proxy_data.enabled,
            proxy_data.proxy.strip() if proxy_data.proxy else ""
        )

        if success:
            # 清理 ChatGPT 服务的会话,确保下次请求使用新代理
            from app.services.chatgpt import chatgpt_service
            await chatgpt_service.clear_session()
            
            return JSONResponse(content={"success": True, "message": "代理配置已保存"})
        else:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "error": "保存失败"}
            )

    except Exception as e:
        logger.error(f"更新代理配置失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"更新失败: {str(e)}"}
        )


@router.post("/settings/log-level")
async def update_log_level(
    log_data: LogLevelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    更新日志级别

    Args:
        log_data: 日志级别数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        更新结果
    """
    try:
        from app.services.settings import settings_service

        logger.info(f"管理员更新日志级别: {log_data.level}")

        # 更新日志级别
        success = await settings_service.update_log_level(db, log_data.level)

        if success:
            return JSONResponse(content={"success": True, "message": "日志级别已保存"})
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "error": "无效的日志级别"}
            )

    except Exception as e:
        logger.error(f"更新日志级别失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"更新失败: {str(e)}"}
        )


# ========== 支付订单管理 ==========

@router.get("/orders", response_class=HTMLResponse)
async def orders_page_redirect(
    request: Request,
    email: Optional[str] = None,
    code: Optional[str] = None,
    order_no: Optional[str] = None,
    source_type: Optional[str] = "payment",
    team_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: Optional[str] = "1",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """兼容旧订单管理入口，统一转到邀请记录（默认支付来源）"""
    return await invite_records_page(
        request=request,
        email=email,
        code=code,
        order_no=order_no,
        source_type=source_type,
        team_id=team_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        db=db,
        current_user=current_user
    )


@router.post("/orders/{order_no}/manual-redeem")
async def manual_redeem_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """兼容旧手动兑换接口，内部仍可执行并落邀请记录"""
    try:
        from app.services.payment import payment_service

        logger.info(f"管理员手动兑换订单: {order_no}")

        result = await payment_service.manual_redeem(order_no, db)

        if not result["success"]:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result
            )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"手动兑换订单失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"操作失败: {str(e)}"}
        )


class MapayConfigRequest(BaseModel):
    """码支付配置请求"""
    mapay_id: str = Field("", description="商户ID")
    mapay_key: str = Field("", description="通信密钥")
    mapay_url: str = Field("https://pay.yueuo.cn", description="API地址")
    mapay_domain: str = Field("", description="网站域名")
    mapay_price: str = Field("19.9", description="支付金额")
    mapay_product_name: str = Field("GPT Team 会员", description="商品名称")


@router.post("/settings/mapay")
async def update_mapay_config(
    mapay_data: MapayConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    更新码支付配置

    Args:
        mapay_data: 码支付配置数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        更新结果
    """
    try:
        from app.services.settings import settings_service

        logger.info(f"管理员更新码支付配置")

        success = await settings_service.update_mapay_config(
            db,
            mapay_data.mapay_id,
            mapay_data.mapay_key,
            mapay_data.mapay_url,
            mapay_data.mapay_domain,
            mapay_data.mapay_price,
            mapay_data.mapay_product_name
        )

        if success:
            return JSONResponse(content={"success": True, "message": "码支付配置已保存"})
        else:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "error": "保存失败"}
            )

    except Exception as e:
        logger.error(f"更新码支付配置失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"更新失败: {str(e)}"}
        )


class PaymentMethodsRequest(BaseModel):
    """支付方式配置请求"""
    alipay_enabled: bool = Field(True, description="是否启用支付宝")
    wxpay_enabled: bool = Field(True, description="是否启用微信支付")


@router.post("/settings/payment-methods")
async def update_payment_methods_config(
    payment_data: PaymentMethodsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    更新支付方式配置

    Args:
        payment_data: 支付方式配置数据
        db: 数据库会话
        current_user: 当前用户（需要登录）

    Returns:
        更新结果
    """
    try:
        from app.services.settings import settings_service

        logger.info(f"管理员更新支付方式配置: alipay={payment_data.alipay_enabled}, wxpay={payment_data.wxpay_enabled}")

        success = await settings_service.update_payment_methods_config(
            db,
            payment_data.alipay_enabled,
            payment_data.wxpay_enabled
        )

        if success:
            return JSONResponse(content={"success": True, "message": "支付方式配置已保存"})
        else:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"success": False, "error": "保存失败"}
            )

    except Exception as e:
        logger.error(f"更新支付方式配置失败: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": f"更新失败: {str(e)}"}
        )


