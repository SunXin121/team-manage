"""
数据库自动迁移模块
在应用启动时自动检测并执行必要的数据库迁移
"""
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def get_db_path():
    """获取数据库文件路径"""
    from app.config import settings
    db_file = settings.database_url.split("///")[-1]
    return Path(db_file)


def column_exists(cursor, table_name, column_name):
    """检查表中是否存在指定列"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def run_auto_migration():
    """
    自动运行数据库迁移
    检测缺失的列并自动添加
    """
    db_path = get_db_path()
    
    if not db_path.exists():
        logger.info("数据库文件不存在，跳过迁移")
        return
    
    logger.info("开始检查数据库迁移...")
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        migrations_applied = []
        
        # 检查并添加质保相关字段
        if not column_exists(cursor, "redemption_codes", "has_warranty"):
            logger.info("添加 redemption_codes.has_warranty 字段")
            cursor.execute("""
                ALTER TABLE redemption_codes 
                ADD COLUMN has_warranty BOOLEAN DEFAULT 0
            """)
            migrations_applied.append("redemption_codes.has_warranty")
        
        if not column_exists(cursor, "redemption_codes", "warranty_expires_at"):
            logger.info("添加 redemption_codes.warranty_expires_at 字段")
            cursor.execute("""
                ALTER TABLE redemption_codes 
                ADD COLUMN warranty_expires_at DATETIME
            """)
            migrations_applied.append("redemption_codes.warranty_expires_at")
        
        if not column_exists(cursor, "redemption_codes", "warranty_days"):
            logger.info("添加 redemption_codes.warranty_days 字段")
            cursor.execute("""
                ALTER TABLE redemption_codes 
                ADD COLUMN warranty_days INTEGER DEFAULT 30
            """)
            migrations_applied.append("redemption_codes.warranty_days")
        
        if not column_exists(cursor, "redemption_records", "is_warranty_redemption"):
            logger.info("添加 redemption_records.is_warranty_redemption 字段")
            cursor.execute("""
                ALTER TABLE redemption_records 
                ADD COLUMN is_warranty_redemption BOOLEAN DEFAULT 0
            """)
            migrations_applied.append("redemption_records.is_warranty_redemption")

        # 检查并添加 Token 刷新相关字段
        if not column_exists(cursor, "teams", "refresh_token_encrypted"):
            logger.info("添加 teams.refresh_token_encrypted 字段")
            cursor.execute("ALTER TABLE teams ADD COLUMN refresh_token_encrypted TEXT")
            migrations_applied.append("teams.refresh_token_encrypted")

        if not column_exists(cursor, "teams", "session_token_encrypted"):
            logger.info("添加 teams.session_token_encrypted 字段")
            cursor.execute("ALTER TABLE teams ADD COLUMN session_token_encrypted TEXT")
            migrations_applied.append("teams.session_token_encrypted")

        if not column_exists(cursor, "teams", "client_id"):
            logger.info("添加 teams.client_id 字段")
            cursor.execute("ALTER TABLE teams ADD COLUMN client_id VARCHAR(100)")
            migrations_applied.append("teams.client_id")

        if not column_exists(cursor, "teams", "error_count"):
            logger.info("添加 teams.error_count 字段")
            cursor.execute("ALTER TABLE teams ADD COLUMN error_count INTEGER DEFAULT 0")
            migrations_applied.append("teams.error_count")

        # 检查并创建邀请记录表（统一支付订单与兑换使用记录）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='invite_records'")
        invite_table_exists = cursor.fetchone() is not None
        if not invite_table_exists:
            logger.info("创建 invite_records 表")
            cursor.execute("""
                CREATE TABLE invite_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email VARCHAR(255) NOT NULL,
                    source_type VARCHAR(20) NOT NULL,
                    source_code VARCHAR(32),
                    order_no VARCHAR(64),
                    pay_type VARCHAR(20),
                    amount VARCHAR(20),
                    trade_no VARCHAR(64),
                    team_id INTEGER NOT NULL,
                    account_id VARCHAR(100),
                    is_warranty_redemption BOOLEAN DEFAULT 0,
                    invited_at DATETIME,
                    FOREIGN KEY(team_id) REFERENCES teams(id)
                )
            """)
            migrations_applied.append("create_table.invite_records")

        # 补齐 invited_at 字段（兼容早期表结构）
        if not column_exists(cursor, "invite_records", "invited_at"):
            logger.info("添加 invite_records.invited_at 字段")
            cursor.execute("ALTER TABLE invite_records ADD COLUMN invited_at DATETIME")
            migrations_applied.append("invite_records.invited_at")

        # 创建 invite_records 索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_email ON invite_records(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_source ON invite_records(source_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_order_no ON invite_records(order_no)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_source_code ON invite_records(source_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_team ON invite_records(team_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invite_time ON invite_records(invited_at)")

        # 历史数据回填：兑换记录 -> 邀请记录
        cursor.execute("""
            INSERT INTO invite_records (
                email, source_type, source_code, team_id, account_id, is_warranty_redemption, invited_at
            )
            SELECT
                rr.email,
                'redeem_code',
                rr.code,
                rr.team_id,
                rr.account_id,
                COALESCE(rr.is_warranty_redemption, 0),
                rr.redeemed_at
            FROM redemption_records rr
            LEFT JOIN invite_records ir
                ON ir.source_type = 'redeem_code'
                AND ir.source_code = rr.code
                AND ir.email = rr.email
                AND ir.team_id = rr.team_id
                AND ir.invited_at = rr.redeemed_at
            WHERE ir.id IS NULL
        """)

        # 历史数据回填：已兑换支付订单 -> 邀请记录
        cursor.execute("""
            INSERT INTO invite_records (
                email, source_type, order_no, pay_type, amount, trade_no, team_id, invited_at
            )
            SELECT
                po.email,
                'payment',
                po.order_no,
                po.pay_type,
                po.amount,
                po.trade_no,
                po.team_id,
                COALESCE(po.redeemed_at, po.paid_at, po.created_at)
            FROM payment_orders po
            LEFT JOIN invite_records ir
                ON ir.source_type = 'payment'
                AND ir.order_no = po.order_no
            WHERE po.status = 'redeemed'
                AND po.team_id IS NOT NULL
                AND ir.id IS NULL
        """)
        
        # 提交更改
        conn.commit()
        
        if migrations_applied:
            logger.info(f"数据库迁移完成，应用了 {len(migrations_applied)} 个迁移: {', '.join(migrations_applied)}")
        else:
            logger.info("数据库已是最新版本，无需迁移")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"数据库迁移失败: {e}")
        raise


if __name__ == "__main__":
    # 允许直接运行此脚本进行迁移
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_auto_migration()
    print("迁移完成")
