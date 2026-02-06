"""
数据库迁移脚本 - 添加质保功能字段
执行此脚本以更新现有数据库结构
"""
import sqlite3
import sys
from pathlib import Path

# 数据库路径
DB_PATH = Path(__file__).parent / "team_manage.db"


def migrate_database():
    """执行数据库迁移"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        print("开始数据库迁移...")
        
        # 检查 redemption_codes 表是否已有 has_warranty 字段
        cursor.execute("PRAGMA table_info(redemption_codes)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # 添加 has_warranty 字段
        if 'has_warranty' not in columns:
            print("添加 redemption_codes.has_warranty 字段...")
            cursor.execute("""
                ALTER TABLE redemption_codes 
                ADD COLUMN has_warranty INTEGER DEFAULT 0
            """)
            print("✓ 已添加 has_warranty 字段")
        else:
            print("✓ has_warranty 字段已存在")
        
        # 添加 warranty_expires_at 字段
        if 'warranty_expires_at' not in columns:
            print("添加 redemption_codes.warranty_expires_at 字段...")
            cursor.execute("""
                ALTER TABLE redemption_codes 
                ADD COLUMN warranty_expires_at DATETIME
            """)
            print("✓ 已添加 warranty_expires_at 字段")
        else:
            print("✓ warranty_expires_at 字段已存在")
        
        # 检查 redemption_records 表
        cursor.execute("PRAGMA table_info(redemption_records)")
        record_columns = [col[1] for col in cursor.fetchall()]
        
        # 添加 is_warranty_redemption 字段
        if 'is_warranty_redemption' not in record_columns:
            print("添加 redemption_records.is_warranty_redemption 字段...")
            cursor.execute("""
                ALTER TABLE redemption_records 
                ADD COLUMN is_warranty_redemption INTEGER DEFAULT 0
            """)
            print("✓ 已添加 is_warranty_redemption 字段")
        else:
            print("✓ is_warranty_redemption 字段已存在")
        
        # 提交更改
        conn.commit()
        print("\n✓ 数据库迁移完成!")
        
    except sqlite3.Error as e:
        print(f"\n✗ 数据库迁移失败: {e}", file=sys.stderr)
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"✗ 数据库文件不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    
    # 备份提示
    print(f"数据库路径: {DB_PATH}")
    print("建议在迁移前备份数据库文件!")
    response = input("是否继续? (y/n): ")
    
    if response.lower() == 'y':
        migrate_database()
    else:
        print("已取消迁移")
