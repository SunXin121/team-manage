"""
码支付服务
用于对接码支付平台，实现在线支付功能
API文档: https://pay.yueuo.cn/User/Doc.php
"""
import logging
import hashlib
import time
import secrets
from typing import Optional, Dict, Any
from datetime import timedelta
from urllib.parse import urlencode
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.config import settings
from app.models import PaymentOrder, Team
from app.utils.time_utils import get_now
from app.services.redeem_flow import redeem_flow_service
from app.services.invite_record import invite_record_service

logger = logging.getLogger(__name__)


class PaymentService:
    """码支付服务类"""

    def __init__(self):
        """初始化支付服务"""
        pass

    async def _get_config(self, db_session: AsyncSession) -> Dict[str, Any]:
        """
        获取码支付配置（优先从数据库读取，否则使用环境变量）
        """
        from app.services.settings import settings_service
        
        db_config = await settings_service.get_mapay_config(db_session)
        
        # 从数据库获取，如果为空则使用环境变量
        mapay_id = db_config.get("mapay_id") or settings.mapay_id
        mapay_key = db_config.get("mapay_key") or settings.mapay_key
        mapay_url = db_config.get("mapay_url") or settings.mapay_url
        mapay_domain = db_config.get("mapay_domain") or ""
        mapay_price = db_config.get("mapay_price") or str(settings.mapay_price)
        mapay_product_name = db_config.get("mapay_product_name") or settings.mapay_product_name
        
        return {
            "mapay_id": mapay_id,
            "mapay_key": mapay_key,
            "mapay_url": mapay_url.rstrip("/") if mapay_url else "",
            "mapay_domain": mapay_domain.rstrip("/") if mapay_domain else "",
            "price": float(mapay_price) if mapay_price else settings.mapay_price,
            "product_name": mapay_product_name
        }

    def _generate_order_no(self) -> str:
        """
        生成唯一订单号
        格式: 时间戳 + 随机字符串
        """
        timestamp = int(time.time() * 1000)
        random_str = secrets.token_hex(4).upper()
        return f"{timestamp}{random_str}"

    def _generate_sign(self, params: Dict[str, str], key: str) -> str:
        """
        生成签名（易支付签名算法）
        签名规则: 将参数按ASCII码排序后拼接，再加上密钥进行MD5
        参与签名的参数: money, name, notify_url, out_trade_no, pid, return_url, sitename(可选), type
        
        Args:
            params: 请求参数
            key: 商户密钥
            
        Returns:
            签名字符串
        """
        # 参与签名的参数（按字母顺序排列）
        sign_keys = ['money', 'name', 'notify_url', 'out_trade_no', 'pid', 'return_url', 'sitename', 'type']
        # 过滤掉空值参数
        sign_parts = []
        for k in sign_keys:
            v = params.get(k, '')
            if v:  # 只包含非空参数
                sign_parts.append(f"{k}={v}")
        
        sign_str = '&'.join(sign_parts) + key
        return hashlib.md5(sign_str.encode()).hexdigest()

    def _verify_sign(self, params: Dict[str, Any], key: str) -> bool:
        """
        验证回调签名
        回调签名规则: MD5(money={}&name={}&out_trade_no={}&pid={}&trade_no={}&trade_status=TRADE_SUCCESS&type={}{key})
        
        Args:
            params: 回调参数
            key: 商户密钥
            
        Returns:
            签名是否有效
        """
        # 按照固定顺序拼接参数
        sign_str = (
            f"money={params.get('money', '')}&"
            f"name={params.get('name', '')}&"
            f"out_trade_no={params.get('out_trade_no', '')}&"
            f"pid={params.get('pid', '')}&"
            f"trade_no={params.get('trade_no', '')}&"
            f"trade_status={params.get('trade_status', '')}&"
            f"type={params.get('type', '')}"
            f"{key}"
        )
        expected_sign = hashlib.md5(sign_str.encode()).hexdigest()
        return params.get('sign', '') == expected_sign

    async def create_order(
        self,
        email: str,
        pay_type: str,
        db_session: AsyncSession,
        price: Optional[float] = None,
        product_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建支付订单
        
        Args:
            email: 用户邮箱
            pay_type: 支付方式 (alipay/wxpay/qqpay)
            db_session: 数据库会话
            price: 支付金额 (可选，默认使用配置)
            product_name: 商品名称 (可选，默认使用配置)
            
        Returns:
            结果字典，包含订单信息和支付链接
        """
        try:
            # 获取配置
            config = await self._get_config(db_session)
            mapay_id = config["mapay_id"]
            mapay_key = config["mapay_key"]
            mapay_url = config["mapay_url"]
            mapay_domain = config["mapay_domain"]
            
            # 检查配置
            if not mapay_id or not mapay_key:
                return {
                    "success": False,
                    "error": "支付功能未配置，请在系统设置中配置码支付信息"
                }
            
            if not mapay_domain:
                return {
                    "success": False,
                    "error": "支付功能未配置，请在系统设置中配置网站域名"
                }

            # 生成订单号
            order_no = self._generate_order_no()
            actual_price = price if price else config["price"]
            actual_product_name = product_name if product_name else config["product_name"]
            
            # 动态拼接回调和跳转URL
            # notify_url: 异步回调地址，支付成功后由支付平台调用处理业务逻辑
            # return_url: 同步跳转地址，支付完成后用户浏览器跳转的地址
            notify_url = f"{mapay_domain}/api/payment/notify"
            return_url = f"{mapay_domain}/?payment_success=1&order_no={order_no}"

            # 构建请求参数（按易支付API格式）
            request_params = {
                "pid": mapay_id,
                "type": pay_type,  # alipay/wxpay/qqpay
                "out_trade_no": order_no,
                "notify_url": notify_url,
                "return_url": return_url,
                "name": actual_product_name,
                "money": f"{actual_price:.2f}",
                "sign_type": "MD5"
            }

            # 生成签名
            request_params["sign"] = self._generate_sign(request_params, mapay_key)

            logger.info(f"创建支付订单: order_no={order_no}, email={email}, price={actual_price}")

            # 创建订单记录（先保存到数据库）
            order = PaymentOrder(
                order_no=order_no,
                trade_no="",  # 支付成功后由回调更新
                email=email,
                amount=f"{actual_price:.2f}",
                pay_type=pay_type,
                status="pending",
                product_name=actual_product_name,
                pay_url="",  # 跳转式支付，不需要单独的pay_url
                qr_code="",
                expires_at=get_now() + timedelta(minutes=30)  # 30分钟有效期
            )

            db_session.add(order)
            await db_session.commit()

            # 构建支付跳转URL（使用 submit.php 跳转到支付页面）
            pay_url = f"{mapay_url}/submit.php?{urlencode(request_params)}"

            logger.info(f"支付订单创建成功: order_no={order_no}, pay_url={pay_url}")

            return {
                "success": True,
                "order_no": order_no,
                "pay_url": pay_url,
                "amount": f"{actual_price:.2f}",
                "expires_at": order.expires_at.isoformat() if order.expires_at else None
            }

        except Exception as e:
            await db_session.rollback()
            logger.error(f"创建支付订单失败: {e}")
            return {
                "success": False,
                "error": f"创建订单失败: {str(e)}"
            }

    async def handle_notify(
        self,
        params: Dict[str, Any],
        db_session: AsyncSession
    ) -> Dict[str, Any]:
        """
        处理支付回调通知（易支付格式）
        
        回调参数:
            pid: 商户ID
            trade_no: 易支付订单号
            out_trade_no: 商户订单号
            type: 支付方式
            name: 商品名称
            money: 商品金额
            trade_status: 支付状态 (TRADE_SUCCESS)
            sign: 签名
            sign_type: 签名类型 (MD5)
        
        Args:
            params: 回调参数
            db_session: 数据库会话
            
        Returns:
            处理结果
        """
        try:
            logger.info(f"收到支付回调: {params}")

            # 检查支付状态
            trade_status = params.get("trade_status", "")
            if trade_status != "TRADE_SUCCESS":
                logger.info(f"支付状态非成功: {trade_status}")
                return {
                    "success": False,
                    "error": f"支付状态: {trade_status}"
                }

            # 获取配置
            config = await self._get_config(db_session)
            mapay_key = config["mapay_key"]
            
            if not mapay_key:
                logger.error("无法验证回调签名: mapay_key未配置")
                return {
                    "success": False,
                    "error": "支付配置不完整"
                }

            # 验证签名
            if not self._verify_sign(params, mapay_key):
                logger.warning(f"支付回调签名验证失败: {params}")
                return {
                    "success": False,
                    "error": "签名验证失败"
                }

            # 获取订单号
            order_no = params.get("out_trade_no", "")
            trade_no = params.get("trade_no", "")
            
            if not order_no:
                logger.error("回调缺少订单号")
                return {
                    "success": False,
                    "error": "缺少订单号"
                }

            # 查询订单
            stmt = select(PaymentOrder).where(PaymentOrder.order_no == order_no)
            result = await db_session.execute(stmt)
            order = result.scalar_one_or_none()

            if not order:
                logger.error(f"订单不存在: {order_no}")
                return {
                    "success": False,
                    "error": "订单不存在"
                }

            # 检查订单状态
            if order.status == "paid":
                logger.info(f"订单已支付，跳过处理: {order_no}")
                return {
                    "success": True,
                    "message": "订单已处理"
                }

            if order.status not in ["pending"]:
                logger.warning(f"订单状态异常: {order_no}, status={order.status}")
                return {
                    "success": False,
                    "error": f"订单状态异常: {order.status}"
                }

            # 更新订单状态
            order.status = "paid"
            order.trade_no = trade_no
            order.paid_at = get_now()
            
            await db_session.commit()

            # 获取订单关联的邮箱
            email = order.email
            logger.info(f"订单支付成功: {order_no}, email={email}")

            # 自动邀请用户加入工作空间
            invite_result = await self._invite_user_to_team(email, order, db_session)
            
            if invite_result["success"]:
                order.status = "redeemed"
                order.redeemed_at = get_now()
                order.team_id = invite_result.get("team_id")

                invite_record_result = await invite_record_service.create_invite_record(
                    db_session=db_session,
                    email=email,
                    source_type="payment",
                    order_no=order.order_no,
                    pay_type=order.pay_type,
                    amount=order.amount,
                    trade_no=order.trade_no,
                    team_id=invite_result.get("team_id"),
                    account_id=invite_result.get("account_id"),
                    invited_at=order.redeemed_at
                )
                if not invite_record_result["success"]:
                    await db_session.rollback()
                    return {
                        "success": False,
                        "error": invite_record_result.get("error", "写入邀请记录失败")
                    }

                await db_session.commit()
                logger.info(f"用户自动加入Team成功: email={email}, team_id={invite_result.get('team_id')}")
            else:
                logger.warning(f"用户自动加入Team失败: email={email}, error={invite_result.get('error')}")

            return {
                "success": True,
                "message": "支付成功",
                "invite_result": invite_result
            }

        except Exception as e:
            await db_session.rollback()
            logger.error(f"处理支付回调失败: {e}")
            return {
                "success": False,
                "error": f"处理回调失败: {str(e)}"
            }

    async def _invite_user_to_team(
        self,
        email: str,
        order: PaymentOrder,
        db_session: AsyncSession
    ) -> Dict[str, Any]:
        """
        邀请用户加入工作空间
        
        Args:
            email: 用户邮箱
            order: 支付订单
            db_session: 数据库会话
            
        Returns:
            邀请结果
        """
        try:
            from app.services.encryption import encryption_service
            from app.services.chatgpt import chatgpt_service
            
            # 自动选择 Team
            select_result = await redeem_flow_service.select_team_auto(db_session)
            
            if not select_result["success"]:
                return {
                    "success": False,
                    "error": select_result.get("error", "没有可用的Team")
                }

            team_id = select_result["team_id"]

            # 获取 Team 信息
            stmt = select(Team).where(Team.id == team_id)
            result = await db_session.execute(stmt)
            team = result.scalar_one_or_none()

            if not team:
                return {
                    "success": False,
                    "error": "Team 不存在"
                }

            # 检查 Team 状态
            if team.status != "active":
                return {
                    "success": False,
                    "error": f"Team 状态异常: {team.status}"
                }

            if team.current_members >= team.max_members:
                return {
                    "success": False,
                    "error": "Team 已满"
                }

            # 解密 Token
            try:
                access_token = encryption_service.decrypt_token(team.access_token_encrypted)
            except Exception as e:
                logger.error(f"解密 Token 失败: {e}")
                return {
                    "success": False,
                    "error": "系统解密失败"
                }

            # 发送邀请
            invite_result = await chatgpt_service.send_invite(
                access_token, team.account_id, email, db_session
            )

            if not invite_result["success"]:
                return {
                    "success": False,
                    "error": invite_result.get("error", "邀请失败")
                }

            # 更新 Team 成员数
            team.current_members += 1
            if team.current_members >= team.max_members:
                team.status = "full"
            await db_session.commit()

            return {
                "success": True,
                "team_id": team_id,
                "team_name": team.team_name,
                "account_id": team.account_id,
                "message": f"已邀请 {email} 加入 {team.team_name}"
            }

        except Exception as e:
            logger.error(f"邀请用户加入Team失败: {e}")
            return {
                "success": False,
                "error": f"邀请失败: {str(e)}"
            }

    async def get_order_status(
        self,
        order_no: str,
        db_session: AsyncSession
    ) -> Dict[str, Any]:
        """
        查询订单状态
        
        Args:
            order_no: 订单号
            db_session: 数据库会话
            
        Returns:
            订单状态信息
        """
        try:
            stmt = select(PaymentOrder).where(PaymentOrder.order_no == order_no)
            result = await db_session.execute(stmt)
            order = result.scalar_one_or_none()

            if not order:
                return {
                    "success": False,
                    "error": "订单不存在"
                }

            # 检查是否过期
            if order.status == "pending" and order.expires_at and get_now() > order.expires_at:
                order.status = "expired"
                await db_session.commit()

            return {
                "success": True,
                "order_no": order.order_no,
                "status": order.status,
                "amount": order.amount,
                "email": order.email,
                "pay_type": order.pay_type,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "team_id": order.team_id
            }

        except Exception as e:
            logger.error(f"查询订单状态失败: {e}")
            return {
                "success": False,
                "error": f"查询失败: {str(e)}"
            }

    async def get_orders_by_email(
        self,
        email: str,
        db_session: AsyncSession,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        根据邮箱查询订单列表
        
        Args:
            email: 用户邮箱
            db_session: 数据库会话
            limit: 返回数量限制
            
        Returns:
            订单列表
        """
        try:
            stmt = select(PaymentOrder).where(
                PaymentOrder.email == email
            ).order_by(PaymentOrder.created_at.desc()).limit(limit)
            
            result = await db_session.execute(stmt)
            orders = result.scalars().all()

            return {
                "success": True,
                "orders": [
                    {
                        "order_no": order.order_no,
                        "status": order.status,
                        "amount": order.amount,
                        "pay_type": order.pay_type,
                        "created_at": order.created_at.isoformat() if order.created_at else None,
                        "paid_at": order.paid_at.isoformat() if order.paid_at else None
                    }
                    for order in orders
                ]
            }

        except Exception as e:
            logger.error(f"查询订单列表失败: {e}")
            return {
                "success": False,
                "error": f"查询失败: {str(e)}"
            }

    async def manual_redeem(
        self,
        order_no: str,
        db_session: AsyncSession
    ) -> Dict[str, Any]:
        """手动触发兑换（兼容旧管理流程）"""
        try:
            stmt = select(PaymentOrder).where(PaymentOrder.order_no == order_no)
            result = await db_session.execute(stmt)
            order = result.scalar_one_or_none()

            if not order:
                return {
                    "success": False,
                    "error": "订单不存在"
                }

            if order.status != "paid":
                return {
                    "success": False,
                    "error": f"订单状态不正确: {order.status}，只有已支付订单可以手动兑换"
                }

            invite_result = await self._invite_user_to_team(order.email, order, db_session)

            if invite_result["success"]:
                order.status = "redeemed"
                order.redeemed_at = get_now()
                order.team_id = invite_result.get("team_id")

                invite_record_result = await invite_record_service.create_invite_record(
                    db_session=db_session,
                    email=order.email,
                    source_type="payment",
                    order_no=order.order_no,
                    pay_type=order.pay_type,
                    amount=order.amount,
                    trade_no=order.trade_no,
                    team_id=invite_result.get("team_id"),
                    account_id=invite_result.get("account_id"),
                    invited_at=order.redeemed_at
                )
                if not invite_record_result["success"]:
                    await db_session.rollback()
                    return {
                        "success": False,
                        "error": invite_record_result.get("error", "写入邀请记录失败")
                    }

                await db_session.commit()

            return invite_result

        except Exception as e:
            await db_session.rollback()
            logger.error(f"手动兑换失败: {e}")
            return {
                "success": False,
                "error": f"兑换失败: {str(e)}"
            }

# 创建全局支付服务实例
payment_service = PaymentService()
