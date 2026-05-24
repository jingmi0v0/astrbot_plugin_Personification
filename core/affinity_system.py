"""
好感度系统 - 管理用户好感度
"""
import time
from pathlib import Path
from typing import Dict, Optional
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class AffinitySystem:
    """好感度系统，管理用户对机器人的好感度"""
    
    def __init__(self, context):
        self.context = context
        self.affinity_cache: Dict[str, int] = {}  # user_id -> affinity_value
        
        # 配置参数
        self.default_affinity = 0
        self.min_affinity = -100
        self.max_affinity = 100
    
    async def initialize(self):
        """初始化好感度系统"""
        logger.info("[AffinitySystem] 正在初始化...")
        
        # 从配置加载默认值
        config = self.context.get_config()
        affinity_config = config.get('affinity', {})
        
        self.default_affinity = affinity_config.get('default_value', 0)
        self.min_affinity = affinity_config.get('min_value', -100)
        self.max_affinity = affinity_config.get('max_value', 100)
        
        # 从数据库加载已有的好感度数据
        await self._load_from_database()
        
        logger.info(f"[AffinitySystem] 初始化完成，好感度范围: {self.min_affinity} ~ {self.max_affinity}")
    
    async def _load_from_database(self):
        """从数据库加载好感度数据"""
        try:
            import sqlite3
            
            # 使用 AstrBot 的 data 目录
            data_dir = get_astrbot_data_path()
            db_path = str(Path(data_dir) / "plugins" / "personification.db")
            db_file = Path(db_path)
            
            if not db_file.exists():
                logger.warning(f"[AffinitySystem] 数据库文件不存在: {db_path}")
                return
            
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            
            # 查询所有好感度记录
            cursor.execute("SELECT user_id, affinity FROM affinity")
            rows = cursor.fetchall()
            
            for row in rows:
                self.affinity_cache[row[0]] = row[1]
            
            conn.close()
            logger.info(f"[AffinitySystem] 从数据库加载了 {len(rows)} 条好感度记录")
                
        except Exception as e:
            logger.error(f"[AffinitySystem] 从数据库加载好感度失败: {e}")
    
    async def get_affinity(self, user_id: str) -> int:
        """获取用户的好感度
        
        Args:
            user_id: 用户ID
            
        Returns:
            好感度值
        """
        # 先从缓存中查找
        if user_id in self.affinity_cache:
            return self.affinity_cache[user_id]
        
        # 如果缓存中没有，返回默认值
        return self.default_affinity
    
    async def set_affinity(self, user_id: str, value: int):
        """设置用户的好感度
        
        Args:
            user_id: 用户ID
            value: 好感度值
        """
        # 限制好感度范围
        value = max(self.min_affinity, min(self.max_affinity, value))
        
        # 更新缓存
        self.affinity_cache[user_id] = value
        
        # 保存到数据库
        await self._save_to_database(user_id, value)
        
        logger.debug(f"[AffinitySystem] 设置用户 {user_id} 好感度为 {value}")
    
    async def update_affinity(self, user_id: str, delta: int) -> int:
        """更新用户的好感度（增量）
        
        Args:
            user_id: 用户ID
            delta: 好感度变化量（正数增加，负数减少）
            
        Returns:
            更新后的好感度值
        """
        current_affinity = await self.get_affinity(user_id)
        new_affinity = current_affinity + delta
        
        await self.set_affinity(user_id, new_affinity)
        
        logger.debug(f"[AffinitySystem] 用户 {user_id} 好感度变化: {current_affinity} -> {new_affinity} (Δ{delta:+d})")
        
        return new_affinity
    
    async def _save_to_database(self, user_id: str, value: int):
        """保存好感度到数据库"""
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
                "INSERT OR REPLACE INTO affinity (user_id, affinity, updated_at) VALUES (?, ?, ?)",
                (user_id, value, int(time.time()))
            )
            
            conn.commit()
            conn.close()
                
        except Exception as e:
            logger.error(f"[AffinitySystem] 保存好感度到数据库失败: {e}")
    
    async def reset_affinity(self, user_id: str):
        """重置用户的好感度为默认值
        
        Args:
            user_id: 用户ID
        """
        await self.set_affinity(user_id, self.default_affinity)
        logger.debug(f"[AffinitySystem] 重置用户 {user_id} 好感度为默认值 {self.default_affinity}")
    
    async def get_all_affinities(self) -> Dict[str, int]:
        """获取所有用户的好感度
        
        Returns:
            用户ID到好感度的映射字典
        """
        return self.affinity_cache.copy()
    
    async def get_users_by_affinity_range(self, min_val: int, max_val: int) -> list:
        """获取好感度在指定范围内的用户列表
        
        Args:
            min_val: 最小好感度
            max_val: 最大好感度
            
        Returns:
            符合条件的用户ID列表
        """
        result = []
        for user_id, affinity in self.affinity_cache.items():
            if min_val <= affinity <= max_val:
                result.append(user_id)
        
        return result
