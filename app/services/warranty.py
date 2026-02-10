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


class WarrantyService:
    """质保服务类"""

    async def _get_original_invite_snapshot(
        self,
        db_session: AsyncSession,
        email: Optional[str] = None,
        code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取原始邀请快照（仅 redeem_code/payment），用于锚定30天质保窗口。
        兼容 redemption_records 历史数据。
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
                .where(InviteRecord.source_type.in_(["redeem_code", "payment"]))
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

    async def _get_current_invite_snapshot(
        self,
        db_session: AsyncSession,
        email: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取当前（最新）邀请快照（含 after_sales），用于判断用户当前所在 Team 的状态。
        """
        normalized_email = email.strip().lower() if email else None
        if not normalized_email:
            return None

        invite_stmt = (
            select(InviteRecord, Team)
            .outerjoin(Team, InviteRecord.team_id == Team.id)
            .where(func.lower(func.trim(InviteRecord.email)) == normalized_email)
            .where(InviteRecord.source_type.in_(["redeem_code", "payment", "after_sales"]))
            .order_by(InviteRecord.invited_at.desc(), InviteRecord.id.desc())
            .limit(1)
        )

        result = await db_session.execute(invite_stmt)
        invite_row = result.first()
        if not invite_row:
            return None

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

    def _judge_after_sales(
        self,
        invited_at,
        team_status: Optional[str]
    ) -> Dict[str, Any]:
        """
        售后规则：
        1) 按原始邀请时间（redeem_code/payment）起算 30 天
        2) 30 天内 + 当前 Team banned => 可售后
        3) 30 天内 + 当前 Team 非 banned => 正常
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

            normalized_email = email.strip().lower() if email else None
            normalized_code = code.strip() if code else None

            # 1. 获取原始邀请记录（redeem_code/payment），用于锚定30天窗口
            original_snapshot = await self._get_original_invite_snapshot(
                db_session,
                email=normalized_email,
                code=normalized_code
            )

            if not original_snapshot:
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

            # 2. 获取当前最新记录（含 after_sales），用于判断当前 Team 状态
            lookup_email = normalized_email or original_snapshot.get("email", "").strip().lower()
            current_snapshot = await self._get_current_invite_snapshot(
                db_session,
                email=lookup_email
            )
            # 如果没有 current（不太可能），fallback 到 original
            if not current_snapshot:
                current_snapshot = original_snapshot

            # 3. 用原始 invited_at 算窗口 + 当前 team_status 判封禁
            judgement = self._judge_after_sales(
                invited_at=original_snapshot.get("invited_at"),
                team_status=current_snapshot.get("team_status")
            )

            source_code = original_snapshot.get("code")
            display_code = source_code or "-"
            invited_at = original_snapshot.get("invited_at")
            expires_at = judgement.get("expires_at")
            can_reuse = judgement.get("can_reuse", False)

            # 仅兑换码来源才能自动重兑；支付来源可显示售后可处理，但不返回原兑换码
            original_code = source_code if original_snapshot.get("source_type") == "redeem_code" else None

            # record 中 team_* 使用当前快照（用户当前所在 Team）
            record = {
                "code": display_code,
                "has_warranty": True,
                "warranty_valid": judgement.get("warranty_valid", False),
                "warranty_expires_at": expires_at.isoformat() if expires_at else None,
                "status": judgement.get("status", "unknown"),
                "status_reason": judgement.get("message"),
                "can_reuse": can_reuse,
                "used_at": invited_at.isoformat() if invited_at else None,
                "team_id": current_snapshot.get("team_id"),
                "team_name": current_snapshot.get("team_name"),
                "team_status": current_snapshot.get("team_status"),
                "team_expires_at": current_snapshot.get("team_expires_at").isoformat() if current_snapshot.get("team_expires_at") else None,
                "email": current_snapshot.get("email"),
                "source_type": original_snapshot.get("source_type")
            }

            banned_teams = []
            if current_snapshot.get("team_status") == "banned":
                banned_teams.append({
                    "team_id": current_snapshot.get("team_id"),
                    "team_name": current_snapshot.get("team_name"),
                    "email": current_snapshot.get("email"),
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
            normalized_email = email.strip().lower() if email else None

            # 获取原始记录（锚定30天窗口）
            original_snapshot = await self._get_original_invite_snapshot(
                db_session,
                email=normalized_email,
                code=None
            )

            if not original_snapshot:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "未找到邀请记录",
                    "error": None
                }

            # 获取当前记录（判断当前 Team 状态）
            lookup_email = normalized_email or original_snapshot.get("email", "").strip().lower()
            current_snapshot = await self._get_current_invite_snapshot(
                db_session,
                email=lookup_email
            )
            if not current_snapshot:
                current_snapshot = original_snapshot

            judgement = self._judge_after_sales(
                invited_at=original_snapshot.get("invited_at"),
                team_status=current_snapshot.get("team_status")
            )

            if judgement.get("status") == "expired":
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "售后已过期（超过30天）",
                    "error": None
                }

            if current_snapshot.get("team_status") != "banned":
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "所在 Team 未被封，状态正常",
                    "error": None
                }

            # 自动重兑仅支持原始记录是兑换码来源
            original_code = (original_snapshot.get("code") or "").strip()
            request_code = (code or "").strip()
            if original_snapshot.get("source_type") != "redeem_code" or not original_code:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": "原始邀请不是兑换码来源，无法自动重兑",
                    "error": None
                }

            if original_code != request_code:
                return {
                    "success": True,
                    "can_reuse": False,
                    "reason": f"仅支持原始邀请使用的兑换码: {original_code}",
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

            # 获取原始记录（锚定30天窗口）
            original_snapshot = await self._get_original_invite_snapshot(
                db_session,
                email=normalized_email,
                code=None
            )
            if not original_snapshot:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": "未找到邀请记录"
                }

            # 获取当前记录（判断当前 Team 状态）
            current_snapshot = await self._get_current_invite_snapshot(
                db_session,
                email=normalized_email
            )
            if not current_snapshot:
                current_snapshot = original_snapshot

            judgement = self._judge_after_sales(
                invited_at=original_snapshot.get("invited_at"),
                team_status=current_snapshot.get("team_status")
            )
            if judgement.get("status") != "after_sales_available":
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": judgement.get("message", "当前不可售后")
                }

            original_code = (original_snapshot.get("code") or "").strip()
            request_code = (code or "").strip()
            if request_code and original_code and request_code != original_code:
                return {
                    "success": False,
                    "message": None,
                    "team_info": None,
                    "error": f"仅支持原始邀请使用的标识: {original_code}"
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
                current_snapshot.get("email"),
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

            # 计算质保到期时间（基于原始邀请时间）
            original_invited_at = original_snapshot.get("invited_at")
            warranty_expires_at = (original_invited_at + timedelta(days=30)).isoformat() if original_invited_at else None

            return {
                "success": True,
                "message": invite_result.get("message", "已发送新 Team 邀请"),
                "team_info": {
                    "team_id": target_team.id,
                    "team_name": target_team.team_name,
                    "account_id": target_team.account_id,
                    "expires_at": target_team.expires_at.isoformat() if target_team.expires_at else None,
                    "warranty_expires_at": warranty_expires_at
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
