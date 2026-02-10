"""
GPT Team 管理和兑换码自动邀请系统
FastAPI 应用入口文件
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import asyncio
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta

from contextlib import asynccontextmanager, suppress
# 导入路由
from app.routes import redeem, auth, admin, api, user, warranty, payment
from app.config import settings
from app.database import init_db, close_db, AsyncSessionLocal
from app.services.auth import auth_service
from app.utils.time_utils import get_now

# 获取项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"

from starlette.exceptions import HTTPException as StarletteHTTPException


async def _team_auto_sync_loop(
    stop_event: asyncio.Event,
    min_minutes: int,
    max_minutes: int
):
    """后台随机间隔同步 Team 状态"""
    from app.services.team import TeamService

    team_service = TeamService()

    while not stop_event.is_set():
        wait_minutes = random.randint(min_minutes, max_minutes)
        logger.info(f"Team 自动同步任务下一次将在 {wait_minutes} 分钟后执行")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_minutes * 60)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            async with AsyncSessionLocal() as session:
                result = await team_service.sync_all_teams(session)

            if result.get("success"):
                logger.info(
                    "Team 自动同步完成: 总数 %s, 成功 %s, 失败 %s",
                    result.get("total", 0),
                    result.get("success_count", 0),
                    result.get("failed_count", 0)
                )
            else:
                logger.warning(f"Team 自动同步失败: {result.get('error')}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Team 自动同步异常: {e}")


async def _expired_member_cleanup_loop(
    stop_event: asyncio.Event,
    expire_days: int
):
    """每日凌晨执行：扫描邀请记录并清理 30 天到期成员。"""
    from app.services.team import TeamService

    team_service = TeamService()

    while not stop_event.is_set():
        now = get_now()
        next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait_seconds = max(1, int((next_run - now).total_seconds()))
        
        # 因为是每日凌晨执行，所以等待时间一定是 < 24 小时，按"小时:分钟"格式显示更清晰
        hours = wait_seconds // 3600
        minutes = (wait_seconds % 3600) // 60

        logger.info(f"到期成员清理任务下一次将在 {next_run.strftime('%Y-%m-%d %H:%M:%S')} 执行（等待约 {hours}小时{minutes}分钟）")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            async with AsyncSessionLocal() as session:
                result = await team_service.cleanup_expired_members_by_invite_records(
                    db_session=session,
                    expire_days=expire_days
                )

            if result.get("success"):
                stats = result.get("stats", {})
                logger.info(
                    "到期成员清理完成: scanned=%s, deleted=%s, revoked=%s, skipped=%s, failed=%s",
                    stats.get("scanned", 0),
                    stats.get("deleted", 0),
                    stats.get("revoked", 0),
                    stats.get("skipped", 0),
                    stats.get("failed", 0)
                )
            else:
                logger.warning(f"到期成员清理失败: {result.get('error')}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"到期成员清理任务异常: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    启动时初始化数据库，关闭时释放资源
    """
    auto_sync_task = None
    auto_sync_stop_event = asyncio.Event()
    expired_member_cleanup_task = None
    expired_member_cleanup_stop_event = asyncio.Event()

    logger.info("系统正在启动，正在初始化数据库...")
    try:
        # 0. 确保数据库目录存在
        db_file = settings.database_url.split("///")[-1]
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        
        # 1. 创建数据库表
        await init_db()
        
        # 2. 运行自动数据库迁移
        from app.db_migrations import run_auto_migration
        run_auto_migration()
        
        # 3. 初始化管理员密码（如果不存在）
        async with AsyncSessionLocal() as session:
            await auth_service.initialize_admin_password(session)
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")

    if settings.team_auto_sync_enabled:
        min_minutes = max(1, settings.team_auto_sync_min_minutes)
        max_minutes = max(min_minutes, settings.team_auto_sync_max_minutes)

        if (
            min_minutes != settings.team_auto_sync_min_minutes
            or max_minutes != settings.team_auto_sync_max_minutes
        ):
            logger.warning(
                "检测到 Team 自动同步间隔配置异常，已自动修正为 %s-%s 分钟",
                min_minutes,
                max_minutes
            )

        auto_sync_task = asyncio.create_task(
            _team_auto_sync_loop(auto_sync_stop_event, min_minutes, max_minutes)
        )
        logger.info(f"Team 自动同步任务已启动，随机间隔 {min_minutes}-{max_minutes} 分钟")
    else:
        logger.info("Team 自动同步任务已禁用")

    if settings.expired_member_cleanup_enabled:
        expire_days = max(1, settings.expired_member_cleanup_days)
        if expire_days != settings.expired_member_cleanup_days:
            logger.warning(
                "检测到到期成员清理天数配置异常，已自动修正为 %s 天",
                expire_days
            )

        expired_member_cleanup_task = asyncio.create_task(
            _expired_member_cleanup_loop(
                expired_member_cleanup_stop_event,
                expire_days
            )
        )
        logger.info(f"到期成员清理任务已启动，每日凌晨执行，过期阈值 {expire_days} 天")
    else:
        logger.info("到期成员清理任务已禁用")

    yield

    if auto_sync_task:
        auto_sync_stop_event.set()
        try:
            await asyncio.wait_for(auto_sync_task, timeout=30)
        except asyncio.TimeoutError:
            auto_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_sync_task

    if expired_member_cleanup_task:
        expired_member_cleanup_stop_event.set()
        try:
            await asyncio.wait_for(expired_member_cleanup_task, timeout=30)
        except asyncio.TimeoutError:
            expired_member_cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await expired_member_cleanup_task

    # 关闭连接
    await close_db()
    logger.info("系统正在关闭，已释放数据库连接")


# 创建 FastAPI 应用实例
app = FastAPI(
    title="GPT Team 管理系统",
    description="ChatGPT Team 账号管理和兑换码自动邀请系统",
    version="0.1.0",
    lifespan=lifespan
)

# 全局异常处理
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """ 处理 HTTP 异常 """
    if exc.status_code in [401, 403]:
        # 检查是否是 HTML 请求
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/login")
    
    # 默认返回 JSON 响应（FastAPI 的默认行为）
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# 配置 Session 中间件
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="session",
    max_age=14 * 24 * 60 * 60,  # 14 天
    same_site="lax",
    https_only=False  # 开发环境设为 False，生产环境应设为 True
)

# 配置静态文件
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# 配置模板引擎
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# 添加模板过滤器
def format_datetime(dt):
    """格式化日期时间"""
    if not dt:
        return "-"
    if isinstance(dt, str):
        try:
            # 兼容包含时区信息的字符串
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except:
            return dt
    
    # 统一转换为北京时间显示 (如果它是 aware datetime)
    import pytz
    from app.config import settings
    if dt.tzinfo is None:
        # 如果是 naive datetime，假设它是本地时区（CST）的时间
        pass
    else:
        # 如果是 aware datetime，转换为目标时区
        tz = pytz.timezone(settings.timezone)
        dt = dt.astimezone(tz)
        
    return dt.strftime("%Y-%m-%d %H:%M")

def escape_js(value):
    """转义字符串用于 JavaScript"""
    if not value:
        return ""
    return value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")

templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["escape_js"] = escape_js

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 注册路由
app.include_router(user.router)  # 用户路由(根路径)
app.include_router(redeem.router)
app.include_router(warranty.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(payment.router, prefix="/api")  # 支付API路由


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面"""
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "user": None}
    )


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug
    )
