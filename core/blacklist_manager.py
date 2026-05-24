"""
黑名单管理器 - 管理用户和群的黑名单
"""
import time
from pathlib import Path
from typing import Dict, List, Optional
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class BlacklistManager:
    """黑名单管理器，管理被拉黑的用户和群"""
    
    def __init__(self, context):
        self.context = context
        self.blacklist_cache: Dict[str, dict] = {}  # id -> {id, type, reason, timestamp}
        
    async def initialize(self):
        """初始化黑名单管理器"""
        logger.info("[BlacklistManager] 正在初始化...")
        
        # 从数据库加载黑名单数据
        await self._load_from_database()
        
        logger.info(f"[BlacklistManager] 初始化完成，当前黑名单数量: {len(self.blacklist_cache)}")
    
    async def _load_from_database(self):
        """从数据库加载黑名单数据"""
        try:
            import sqlite3
            
            # 使用 AstrBot 的 data 目录
            data_dir = get_astrbot_data_path()
            db_path = str(Path(data_dir) / "plugins" / "personification.db")
            db_file = Path(db_path)
            
            if not db_file.exists():
                logger.warning(f"[BlacklistManager] 数据库文件不存在: {db_path}")
                return
            
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            
            # 查询所有黑名单记录
            cursor.execute("SELECT id, type, reason, timestamp FROM blacklist")
            rows = cursor.fetchall()
            
            for row in rows:
                self.blacklist_cache[row[0]] = {
                    'id': row[0],
                    'type': row[1],
                    'reason': row[2],
                    'timestamp': row[3]
                }
            
            conn.close()
            logger.info(f"[BlacklistManager] 从数据库加载了 {len(rows)} 条黑名单记录")
                
        except Exception as e:
            logger.error(f"[BlacklistManager] 从数据库加载黑名单失败: {e}")
    
    async def add_to_blacklist(self, target_id: str, reason: str = "", target_type: str = "user"):
        """将目标加入黑名单
        
        Args:
            target_id: 目标ID（用户ID或群ID）
            reason: 拉黑原因
            target_type: 目标类型，'user' 或 'group'
        """
        # 添加到缓存
        self.blacklist_cache[target_id] = {
            'id': target_id,
            'type': target_type,
            'reason': reason,
            'timestamp': int(time.time())
        }
        
        # 保存到数据库
        await self._save_to_database(target_id, reason, target_type)
        
        logger.info(f"[BlacklistManager] 已将 {target_type} {target_id} 加入黑名单，原因: {reason}")
        
        # 如果是群聊，尝试退群
        if target_type == 'group':
            await self._leave_group(target_id)
    
    async def remove_from_blacklist(self, target_id: str) -> bool:
        """从黑名单中移除目标
        
        Args:
            target_id: 目标ID
            
        Returns:
            是否成功移除
        """
        if target_id in self.blacklist_cache:
            del self.blacklist_cache[target_id]
            
            # 从数据库中删除
            await self._remove_from_database(target_id)
            
            logger.info(f"[BlacklistManager] 已将 {target_id} 从黑名单中移除")
            return True
        
        return False
    
    async def is_in_blacklist(self, target_id: str) -> bool:
        """检查目标是否在黑名单中
        
        Args:
            target_id: 目标ID
            
        Returns:
            是否在黑名单中
        """
        return target_id in self.blacklist_cache
    
    async def get_blacklist(self) -> List[dict]:
        """获取黑名单列表
        
        Returns:
            黑名单列表
        """
        return list(self.blacklist_cache.values())
    
    async def get_blacklist_by_type(self, target_type: str) -> List[dict]:
        """获取指定类型的黑名单列表
        
        Args:
            target_type: 目标类型，'user' 或 'group'
            
        Returns:
            黑名单列表
        """
        return [item for item in self.blacklist_cache.values() if item['type'] == target_type]
    
    async def _save_to_database(self, target_id: str, reason: str, target_type: str):
        """保存黑名单到数据库"""
        try:
            import sqlite3
            import time
            
            # 使用 AstrBot 的 data 目录
            data_dir = get_astrbot_data_path()
            db_path = str(Path(data_dir) / "plugins" / "personification.db")
            db_file = Path(db_path)
            
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            
            # 使用UPSERT语句
            cursor.execute(
                "INSERT OR REPLACE INTO blacklist (id, type, reason, timestamp) VALUES (?, ?, ?, ?)",
                (target_id, target_type, reason, int(time.time()))
            )
            
            conn.commit()
            conn.close()
                
        except Exception as e:
            logger.error(f"[BlacklistManager] 保存黑名单到数据库失败: {e}")
    
    async def _remove_from_database(self, target_id: str):
        """从数据库中删除黑名单记录"""
        try:
            import sqlite3
            
            # 使用 AstrBot 的 data 目录
            data_dir = get_astrbot_data_path()
            db_path = str(Path(data_dir) / "plugins" / "personification.db")
            db_file = Path(db_path)
            
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM blacklist WHERE id = ?", (target_id,))
            
            conn.commit()
            conn.close()
                
        except Exception as e:
            logger.error(f"[BlacklistManager] 从数据库删除黑名单失败: {e}")
    
    async def _leave_group(self, group_id: str):
        """退出指定的群聊
        
        Args:
            group_id: 群ID
        """
        try:
            # 获取平台适配器
            platform = self.context.platform_manager
            
            # TODO: 调用平台适配器的退群API
            # 这需要根据具体的平台适配器实现
            logger.info(f"[BlacklistManager] 尝试退出群聊 {group_id}")
            
            # 示例代码（需要根据实际平台适配器调整）:
            # bot = platform.get_bot_by_id(...)
            # await bot.leave_group(group_id)
            
        except Exception as e:
            logger.error(f"[BlacklistManager] 退出群聊 {group_id} 失败: {e}")
    
    async def check_and_auto_blacklist(self, user_id: str, affinity_value: int, threshold: int):
        """检查并自动拉黑（基于好感度阈值）
        
        Args:
            user_id: 用户ID
            affinity_value: 当前好感度
            threshold: 拉黑阈值
        """
        if affinity_value <= threshold:
            await self.add_to_blacklist(
                user_id, 
                f"好感度过低({affinity_value})，低于阈值({threshold})",
                "user"
            )
            logger.info(f"[BlacklistManager] 用户 {user_id} 因好感度 {affinity_value} 低于阈值 {threshold} 被自动拉黑")
