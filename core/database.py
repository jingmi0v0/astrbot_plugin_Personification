"""
数据库初始化脚本
创建好感度和黑名单所需的数据库表
"""
import sqlite3
from pathlib import Path
from astrbot.api import logger
from astrbot.core.utils.path_utils import get_data_dir


async def init_database(db_path: str = None):
    """初始化数据库表
    
    Args:
        db_path: 数据库文件路径，默认为 data/plugins/personification.db
    """
    if not db_path:
        # 使用 AstrBot 的 data 目录
        data_dir = get_data_dir()
        db_path = str(data_dir / "plugins" / "personification.db")
    
    try:
        # 确保目录存在
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 连接数据库
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        
        # 创建好感度表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS affinity (
                user_id TEXT PRIMARY KEY,
                affinity INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
        """)
        
        # 创建黑名单表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'user',
                reason TEXT,
                timestamp INTEGER NOT NULL
            )
        """)
        
        # 创建状态持久化表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS status_cache (
                session_id TEXT PRIMARY KEY,
                status TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        
        # 创建消息历史表（可选，用于长期存储）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT,
                content TEXT,
                timestamp INTEGER NOT NULL
            )
        """)
        
        # 创建索引以提高查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_affinity_user ON affinity(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_id ON blacklist(id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status_session ON status_cache(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_session ON message_history(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON message_history(timestamp)")
        
        # 提交更改
        conn.commit()
        conn.close()
        
        logger.info(f"[Database] 数据库初始化完成: {db_path}")
        return True
        
    except Exception as e:
        logger.error(f"[Database] 数据库初始化失败: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    import asyncio
    asyncio.run(init_database())
