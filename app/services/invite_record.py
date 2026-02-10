"""
邀请记录服务
统一管理兑换码邀请与支付邀请记录
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InviteRecord, Team
from app.utils.time_utils import get_now

logger = logging.getLogger(__name__)

SUPPORTED_INVITE_SOURCE_TYPES = {
    "redeem_code",
    "payment",
    "after_sales",
    "admin_manual"
}


class InviteRecordService:
    """邀请记录服务类"""

    @staticmethod
    def _parse_date_start(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            return None

    @staticmethod
    def _parse_date_end(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
        except Exception:
            return None

    def _build_filters(
        self,
        email: Optional[str] = None,
        source_code: Optional[str] = None,
        order_no: Optional[str] = None,
        team_id: Optional[int] = None,
        source_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Any]:
        filters: List[Any] = []

        if email:
            filters.append(InviteRecord.email.ilike(f"%{email}%"))

        if source_code:
            filters.append(InviteRecord.source_code.ilike(f"%{source_code}%"))

        if order_no:
            filters.append(InviteRecord.order_no.ilike(f"%{order_no}%"))

        if team_id:
            filters.append(InviteRecord.team_id == team_id)

        if source_type in SUPPORTED_INVITE_SOURCE_TYPES:
            filters.append(InviteRecord.source_type == source_type)

        start_at = self._parse_date_start(start_date)
        if start_at:
            filters.append(InviteRecord.invited_at >= start_at)

        end_at = self._parse_date_end(end_date)
        if end_at:
            filters.append(InviteRecord.invited_at < end_at)

        return filters

    async def create_invite_record(
        self,
        db_session: AsyncSession,
        email: str,
        source_type: str,
        team_id: int,
        account_id: Optional[str] = None,
        source_code: Optional[str] = None,
        order_no: Optional[str] = None,
        pay_type: Optional[str] = None,
        amount: Optional[str] = None,
        trade_no: Optional[str] = None,
        is_warranty_redemption: bool = False,
        invited_at: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        创建邀请记录（不在此处 commit）
        对支付邀请按 order_no 去重，避免重复写入
        """
        try:
            if source_type not in SUPPORTED_INVITE_SOURCE_TYPES:
                return {
                    "success": False,
                    "created": False,
                    "record": None,
                    "error": f"不支持的 source_type: {source_type}"
                }

            if source_type == "payment" and order_no:
                stmt = select(InviteRecord).where(
                    and_(
                        InviteRecord.source_type == "payment",
                        InviteRecord.order_no == order_no
                    )
                )
                result = await db_session.execute(stmt)
                exists = result.scalar_one_or_none()
                if exists:
                    return {
                        "success": True,
                        "created": False,
                        "record": exists,
                        "error": None
                    }

            record = InviteRecord(
                email=email,
                source_type=source_type,
                source_code=source_code,
                order_no=order_no,
                pay_type=pay_type,
                amount=amount,
                trade_no=trade_no,
                team_id=team_id,
                account_id=account_id,
                is_warranty_redemption=is_warranty_redemption,
                invited_at=invited_at or get_now()
            )
            db_session.add(record)

            return {
                "success": True,
                "created": True,
                "record": record,
                "error": None
            }
        except Exception as e:
            logger.error(f"创建邀请记录失败: {e}")
            return {
                "success": False,
                "created": False,
                "record": None,
                "error": f"创建邀请记录失败: {str(e)}"
            }

    async def get_invite_records(
        self,
        db_session: AsyncSession,
        email: Optional[str] = None,
        source_code: Optional[str] = None,
        order_no: Optional[str] = None,
        team_id: Optional[int] = None,
        source_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
        per_page: int = 20
    ) -> Dict[str, Any]:
        """查询邀请记录（分页）"""
        try:
            filters = self._build_filters(
                email=email,
                source_code=source_code,
                order_no=order_no,
                team_id=team_id,
                source_type=source_type,
                start_date=start_date,
                end_date=end_date
            )

            count_stmt = select(func.count(InviteRecord.id))
            query_stmt = (
                select(InviteRecord, Team.team_name, Team.status)
                .join(Team, InviteRecord.team_id == Team.id, isouter=True)
            )

            if filters:
                count_stmt = count_stmt.where(and_(*filters))
                query_stmt = query_stmt.where(and_(*filters))

            total_result = await db_session.execute(count_stmt)
            total = total_result.scalar() or 0

            if page < 1:
                page = 1
            offset = (page - 1) * per_page

            query_stmt = query_stmt.order_by(InviteRecord.invited_at.desc()).offset(offset).limit(per_page)
            result = await db_session.execute(query_stmt)
            rows = result.all()

            records: List[Dict[str, Any]] = []
            for row in rows:
                invite_record = row[0]
                team_name = row[1]
                team_status = row[2]
                records.append({
                    "id": invite_record.id,
                    "email": invite_record.email,
                    "source_type": invite_record.source_type,
                    "source_code": invite_record.source_code,
                    "order_no": invite_record.order_no,
                    "pay_type": invite_record.pay_type,
                    "amount": invite_record.amount,
                    "trade_no": invite_record.trade_no,
                    "team_id": invite_record.team_id,
                    "team_name": team_name,
                    "team_status": team_status,
                    "account_id": invite_record.account_id,
                    "is_warranty_redemption": bool(invite_record.is_warranty_redemption),
                    "invited_at": invite_record.invited_at.isoformat() if invite_record.invited_at else None,
                })

            total_pages = (total + per_page - 1) // per_page if total > 0 else 1

            return {
                "success": True,
                "records": records,
                "total": total,
                "current_page": page,
                "total_pages": total_pages,
                "per_page": per_page,
                "error": None
            }
        except Exception as e:
            logger.error(f"查询邀请记录失败: {e}")
            return {
                "success": False,
                "records": [],
                "total": 0,
                "current_page": page,
                "total_pages": 1,
                "per_page": per_page,
                "error": f"查询邀请记录失败: {str(e)}"
            }

    async def get_invite_stats(
        self,
        db_session: AsyncSession,
        email: Optional[str] = None,
        source_code: Optional[str] = None,
        order_no: Optional[str] = None,
        team_id: Optional[int] = None,
        source_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """统计邀请记录（总计/今日/本周/本月）"""
        try:
            filters = self._build_filters(
                email=email,
                source_code=source_code,
                order_no=order_no,
                team_id=team_id,
                source_type=source_type,
                start_date=start_date,
                end_date=end_date
            )

            stmt = select(InviteRecord.invited_at)
            if filters:
                stmt = stmt.where(and_(*filters))

            result = await db_session.execute(stmt)
            time_rows = result.scalars().all()

            now = get_now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start - timedelta(days=today_start.weekday())
            month_start = today_start.replace(day=1)

            stats = {
                "total": len(time_rows),
                "today": 0,
                "this_week": 0,
                "this_month": 0
            }

            for invited_at in time_rows:
                if not invited_at:
                    continue
                if invited_at >= today_start:
                    stats["today"] += 1
                if invited_at >= week_start:
                    stats["this_week"] += 1
                if invited_at >= month_start:
                    stats["this_month"] += 1

            return {
                "success": True,
                "stats": stats,
                "error": None
            }
        except Exception as e:
            logger.error(f"统计邀请记录失败: {e}")
            return {
                "success": False,
                "stats": {
                    "total": 0,
                    "today": 0,
                    "this_week": 0,
                    "this_month": 0
                },
                "error": f"统计邀请记录失败: {str(e)}"
            }


invite_record_service = InviteRecordService()
