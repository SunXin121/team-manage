"""
质保服务
处理用户质保查询和验证
"""
import logging
from typing import Optional, Dict, Any
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RedemptionRecord, Team, InviteRecord
from app.utils.time_utils import get_now

logger = logging.getLogger(__name__)

# 全局频率限制字典: {(type, key): last_time}
# type: 'email' or 'code'
_query_rate_limit = {}


class WarrantyService:
    """质保服务类"""

    async def _get_latest_invite_snapshot(
        self,
        db_session: AsyncSession,
        email: Optional[str] = None,
        code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取最新邀请快照（优先 invite_records，兼容 redemption_records）
        """
        normalized_email = email.strip().lower() if email else None
        normalized_code = code.strip() if code else None

        if not normalized_email and not normalized_code:
            return None

        if normalized_email:
            invite_stmt = (
                select(InviteRecord, Team)
                .outerjoin(Team, InviteRecord.team_id == Team.id)
                .where(func.lower(func.trim(InviteRecord.email)) == normalized_email)
                .where(InviteRecord.source_type.in_(["redeem_code", "payment", "after_sales"]))
                .order_by(InviteRecord.invited_at.desc(), InviteRecord.id.desc())
                .limit(1)
            )
        else:
            invite_stmt = (
                select(InviteRecord, Team)
                .outerjoin(Team, InviteRecord.team_id == Team.id)
                .where(InviteRecord.source_type == "redeem_code")
                .where(InviteRecord.source_code == normalized_code)
                .order_by(InviteRecord.invited_at.desc(), InviteRecord.id.desc())
                .limit(1)
            )

        result = await db_session.execute(invite_stmt)
        invite_row = result.first()
        if invite_row:
            invite_record, team = invite_row

            return {
                "email": invite_record.email,
                "source_type": invite_record.source_type,
                "code": invite_record.source_code,
                "order_no": invite_record.order_no,
                "invited_at": invite_record.invited_at,
                "team_id": invite_record.team_id,
                "team_name": team.team_name if team else None,
                "team_status": team.status if team else None,
                "team_expires_at": team.expires_at if team else None,
            }

        # 兼容历史数据：旧版本可能只有 redemption_records
        if normalized_email:
            redemption_stmt = (
                select(RedemptionRecord, Team)
                .outerjoin(Team, RedemptionRecord.team_id == Team.id)
                .where(func.lower(func.trim(RedemptionRecord.email)) == normalized_email)
                .order_by(RedemptionRecord.redeemed_at.desc(), RedemptionRecord.id.desc())
                .limit(1)
            )
        else:
            redemption_stmt = (
                select(RedemptionRecord, Team)
                .outerjoin(Team, RedemptionRecord.team_id == Team.id)
                .where(RedemptionRecord.code == normalized_code)
                .order_by(RedemptionRecord.redeemed_at.desc(), RedemptionRecord.id.desc())
                .limit(1)
            )

        result = await db_session.execute(redemption_stmt)
        redemption_row = result.first()
        if not redemption_row:
            return None

        redemption_record, team = redemption_row

        return {
            "email": redemption_record.email,
            "source_type": "redeem_code",
            "code": redemption_record.code,
            "order_no": None,
            "invited_at": redemption_record.redeemed_at,
            "team_id": redemption_record.team_id,
            "team_name": team.team_name if team else None,
            "team_status": team.status if team else None,
            "team_expires_at": team.expires_at if team else None,
        }

    def _judge_after_sales(
        self,
        invited_at,
        team_status: Optional[str]
    ) -> Dict[str, Any]:
        """
        售后规则：
        1) 按最新邀请时间起算 30 天
        2) 30 天内 + Team banned => 可售后
        3) 30 天内 + Team 非 banned => 正常
        4) 超过 30 天 => 过期
        """
        if not invited_at:
            return {
                "status": "unknown",
                "can_reuse": False,
                "warranty_valid": False,
                "expires_at": None,
                "message": "邀请时间缺失，无法判定售后状态"
            }

        expires_at = invited_at + timedelta(days=30)
        now = get_now()

        if now > expires_at:
            return {
                "status": "expired",
                "can_reuse": False,
                "warranty_valid": False,
                "expires_at": expires_at,
                "message": "售后已过期（超过30天）"
            }

        if team_status == "banned":
            return {
                "status": "after_sales_available",
                "can_reuse": True,
                "warranty_valid": True,
                "expires_at": expires_at,
                "message": "可售后：30天内且所在 Team 已被封"
            }

        return {
            "status": "normal",
            "can_reuse": False,
            "warranty_valid": True,
            "expires_at": expires_at,
            "message": "状态正常：所在 Team 未被封"
        }

    async def check_warranty_status(
        self,
        db_session: AsyncSession,
        email: Optional[str] = None,
        code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        检查用户质保状态

        Args:
            db_session: 数据库会话
            email: 用户邮箱
            code: 兑换码

        Returns:
            结果字典,包含 success, has_warranty, warranty_valid, warranty_expires_at,
            banned_teams, can_reuse, original_code, records, message, error
        """
        try:
            if not email and not code:
                return {
                    "success": False,
                    "error": "必须提供邮箱或兑换码"
                }

            # 0. 频率限制 (每个邮箱或每个码 30 秒只能查一次)
            now = get_now()
            normalized_email = email.strip().lower() if email else None
            normalized_code = code.strip() if code else None
            limit_key = ("email", normalized_email) if normalized_email else ("code", normalized_code)
            last_time = _query_rate_limit.get(limit_key)
            if last_time and (now - last_time).total_seconds() < 30:
                wait_time = int(30 - (now - last_time).total_seconds())
                return {
                    "success": False,
                    "error": f"查询太频繁,请 {wait_time} 秒后再试"
                }
            _query_rate_limit[limit_key] = now

            # 1. 按邮箱（或兑换码）查最新一条邀请记录
            latest_snapshot = await self._get_latest_invite_snapshot(
                db_session,
                email=normalized_email,
                code=normalized_code
            )

            if not latest_snapshot:
                return {
                    "success": True,
                    "has_warranty": False,
                    "warranty_valid": False,
                    "warranty_expires_at": None,
                    "banned_teams": [],
                    "can_reuse": False,
                    "original_code": None,
                    "records": [],
                    "message": "未找到相关邀请记录"
                }

            # 2. 按 30 天 + Team 状态判定售后状态
            judgement = self._judge_after_sales(
                invited_at=latest_snapshot.get("invited_at"),
                team_status=latest_snapshot.get("team_status")
            )

            source_code = latest_snapshot.get("code")
            display_code = source_code or "-"
            invited_at = latest_snapshot.get("invited_at")
            expires_at = judgement.get("expires_at")
            can_reuse = judgement.get("can_reuse", False)

            # 仅兑换码来源才能自动重兑；支付来源可显示售后可处理，但不返回原兑换码
            original_code = source_code if latest_snapshot.get("source_type") == "redeem_code" else None

            record = {
                "code": display_code,
                "has_warranty": True,
                "warranty_valid": judgement.get("warranty_valid", False),
                "warranty_expires_at": expires_at.isoformat() if expires_at else None,
                "status": judgement.get("status", "unknown"),
                "status_reason": judgement.get("message"),
                "can_reuse": can_reuse,
                "used_at": invited_at.isoformat() if invited_at else None,
                "team_id": latest_snapshot.get("team_id"),
                "team_name": latest_snapshot.get("team_name"),
                "team_status": latest_snapshot.get("team_status"),
                "team_expires_at": latest_snapshot.get("team_expires_at").isoformat() if latest_snapshot.get("team_expires_at") else None,
                "email": latest_snapshot.get("email"),
                "source_type": latest_snapshot.get("source_type")
            }

            banned_teams = []
            if latest_snapshot.get("team_status") == "banned":
                banned_teams.append({
                    "team_id": latest_snapshot.get("team_id"),
                    "team_name": latest_snapshot.get("team_name"),
                    "email": latest_snapshot.get("email"),
                    "banned_at": None
                })

            return {
                "success": True,
                "has_warranty": True,
                "warranty_valid": judgement.get("warranty_valid", False),
                "warranty_expires_at": expires_at.isoformat() if expires_at else None,
                "banned_teams": banned_teams,
                "can_reuse": can_reuse,
                "original_code": original_code,
                "records": [record],
                "message": judgement.get("message")
            }

        except Exception as e:
            logger.error(f"检查质保状态失败: {e}")
            return {
                "success": False,
                "error": f"检查质保状态失败: {str(e)}"
            }

    async def validate_warranty_reuse(
        self,
        db_session: AsyncSession,
        code: str,
        email: str
    ) -> Dict[str, Any]:
        """
        验证质保码是否可重复使用

        Args:
            db_session: 数据库会话
            code: 兑换码
            email: 用户邮箱

        Returns:
            结果字典,包含 success, can_reuse, reason, error
        """
        try:
            latest_snapshot = await self._get_latest_invite_snapshot(
                db_session,
                email=email,
                code=None
            )

            if not latest_snapshot:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "未找到邀请记录",
                    "error": None
                }

            judgement = self._judge_after_sales(
                invited_at=latest_snapshot.get("invited_at"),
                team_status=latest_snapshot.get("team_status")
            )

            if judgement.get("status") == "expired":
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "售后已过期（超过30天）",
                    "error": None
                }

            if latest_snapshot.get("team_status") != "banned":
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "所在 Team 未被封，状态正常",
                    "error": None
                }

            # 自动重兑仅支持最近一条记录本身是兑换码来源
            latest_code = (latest_snapshot.get("code") or "").strip()
            request_code = (code or "").strip()
            if latest_snapshot.get("source_type") != "redeem_code" or not latest_code:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "最新邀请不是兑换码来源，无法自动重兑",
                    "error": None
                }

            if latest_code != request_code:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": f"仅支持最近一次邀请使用的兑换码: {latest_code}",
                    "error": None
                }

            return {
                "success": True,
                "can_reuse": True,
                "reason": "30天内且所在 Team 已被封，可售后",
                "error": None
            }

        except Exception as e:
            logger.error(f"验证质保码重复使用失败: {e}")
            return {
                "success": False,
                "can_reuse": False,
                "reason": None,
                "error": f"验证失败: {str(e)}"
            }

    async def reinvite_after_sales(
        self,
        db_session: AsyncSession,
        email: str,
        code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        售后重邀：当满足售后条件时，给该邮箱发送新 Team 邀请

        规则：
        - 按邮箱最新邀请记录判定
        - 30天内 + Team 被封 => 允许重邀
        - 其余情况拒绝
        """
        try:
            normalized_email = (email or "").strip().lower()
            if not normalized_email:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": "邮箱不能为空"
                }

            latest_snapshot = await self._get_latest_invite_snapshot(
                db_session,
                email=normalized_email,
                code=None
            )
            if not latest_snapshot:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": "未找到邀请记录"
                }

            judgement = self._judge_after_sales(
                invited_at=latest_snapshot.get("invited_at"),
                team_status=latest_snapshot.get("team_status")
            )
            if judgement.get("status") != "after_sales_available":
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": judgement.get("message", "当前不可售后")
                }

            latest_code = (latest_snapshot.get("code") or "").strip()
            request_code = (code or "").strip()
            if request_code and latest_code and request_code != latest_code:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": f"仅支持最近一次邀请使用的标识: {latest_code}"
                }

            # 选择可用 Team（数据库状态）
            stmt = (
                select(Team)
                .where(Team.status == "active", Team.current_members < Team.max_members)
                .order_by(Team.expires_at.asc())
                .limit(1)
            )
            result = await db_session.execute(stmt)
            target_team = result.scalar_one_or_none()
            if not target_team:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": "没有可用的 Team"
                }

            from app.services.team import TeamService

            team_service = TeamService()
            invite_result = await team_service.add_team_member(
                target_team.id,
                latest_snapshot.get("email"),
                db_session,
                source_type="after_sales"
            )
            if not invite_result.get("success"):
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": invite_result.get("error", "发送邀请失败")
                }

            return {
                "success": True,
                "message": invite_result.get("message", "已发送新 Team 邀请"),
                "team_info": {
                    "team_id": target_team.id,
                    "team_name": target_team.team_name,
                    "account_id": target_team.account_id,
                    "expires_at": target_team.expires_at.isoformat() if target_team.expires_at else None
                },
                "error": None
            }

        except Exception as e:
            logger.error(f"售后重邀失败: {e}")
            return {
                "success": False,
                "message": None,
                "team_info": None,
                "error": f"售后重邀失败: {str(e)}"
            }


# 创建全局质保服务实例
warranty_service = WarrantyService()
