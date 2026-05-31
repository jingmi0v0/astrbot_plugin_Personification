"""
ARSTBOT 拟人化插件主入口
实现类似 chatluna-character 的拟人化聊天功能
"""
import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.components import Plain, Image, At

from .core.personification_manager import PersonificationManager
from .core.affinity_system import AffinitySystem
from .core.blacklist_manager import BlacklistManager
from .core.qzone_system import QZoneSystem
from .core.database import init_database


@register("astrbot_plugin_Personification", "jingmi0v0", "让机器人聊天变得拟人化，支持好感度、黑名单和QQ空间系统", "1.0.0")
class PersonificationPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        
        # 初始化各个子系统
        self.personification_manager = None
        self.affinity_system = None
        self.blacklist_manager = None
        self.qzone_system = None
        
        # 插件配置
        self.config = None
        
    async def initialize(self):
        """插件初始化"""
        logger.info("[Personification] 正在初始化拟人化插件...")
        
        # 初始化数据库
        await init_database()
        
        # 加载插件自身配置（config.yml）
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parent / "config.yml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
            logger.info(f"[Personification] 已加载配置: {config_path}, name={self.config.get('name', 'N/A')}")
        else:
            self.config = {}
            logger.warning(f"[Personification] 配置文件不存在: {config_path}")
        
        # 初始化好感度系统
        self.affinity_system = AffinitySystem(self.context, self.config)
        await self.affinity_system.initialize()
        
        # 初始化黑名单管理器
        self.blacklist_manager = BlacklistManager(self.context)
        await self.blacklist_manager.initialize()
        
        # 初始化拟人化管理器
        self.personification_manager = PersonificationManager(
            context=self.context,
            config=self.config,
            affinity_system=self.affinity_system,
            blacklist_manager=self.blacklist_manager
        )
        await self.personification_manager.initialize()
        
        # 初始化QQ空间系统
        self.qzone_system = QZoneSystem(
            context=self.context,
            config=self.config,
            personification_manager=self.personification_manager
        )
        await self.qzone_system.initialize()
        
        logger.info("[Personification] 拟人化插件初始化完成")
    
    @filter.command("设置好感度")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def set_affinity(self, event: AstrMessageEvent, user_id: str, value: int):
        """设置指定用户的好感度（仅管理员）
        
        Args:
            user_id: 用户ID
            value: 好感度值
        """
        try:
            # 获取好感度范围配置
            min_affinity = self.config.get('affinity', {}).get('min_value', -100)
            max_affinity = self.config.get('affinity', {}).get('max_value', 100)
            
            # 验证好感度范围
            if value < min_affinity or value > max_affinity:
                yield event.plain_result(f"好感度必须在 {min_affinity} 到 {max_affinity} 之间")
                return
            
            # 设置好感度
            await self.affinity_system.set_affinity(user_id, value)
            
            # 检查是否达到拉黑阈值
            blacklist_threshold = self.config.get('affinity', {}).get('blacklist_threshold', -80)
            if value <= blacklist_threshold:
                await self.blacklist_manager.add_to_blacklist(user_id, "好感度过低自动拉黑")
                yield event.plain_result(f"已设置用户 {user_id} 的好感度为 {value}，已自动加入黑名单")
            else:
                yield event.plain_result(f"已设置用户 {user_id} 的好感度为 {value}")
                
        except Exception as e:
            logger.error(f"[Personification] 设置好感度失败: {e}")
            yield event.plain_result(f"设置好感度失败: {str(e)}")
    
    @filter.command("查看好感度")
    async def view_affinity(self, event: AstrMessageEvent, user_id: str = None):
        """查看好感度
        
        Args:
            user_id: 用户ID，不填则查看当前用户
        """
        try:
            # 如果未指定用户ID，使用当前用户
            if not user_id:
                user_id = event.get_sender_id()
            
            # 获取好感度
            affinity = await self.affinity_system.get_affinity(user_id)
            
            # 获取用户名称
            user_name = event.get_sender_name() if user_id == event.get_sender_id() else f"用户{user_id}"
            
            # 获取配置信息
            blacklist_threshold = self.config.get('affinity', {}).get('blacklist_threshold', -80)
            
            # 检查是否在黑名单中
            is_blacklisted = await self.blacklist_manager.is_in_blacklist(user_id)
            status = "拉黑" if is_blacklisted or affinity <= blacklist_threshold else "正常"
            
            # 获取当前情绪（从状态缓存中读取）
            current_emotion = "平静"
            session_id = user_id
            if hasattr(self.personification_manager, 'status_cache'):
                status_data = self.personification_manager.status_cache.get(session_id, {})
                if isinstance(status_data, dict):
                    current_emotion = status_data.get('心情', '平静')
                elif isinstance(status_data, str):
                    # 如果状态是字符串，尝试解析
                    import re
                    emotion_match = re.search(r'心情[:：]\s*["\']?([^"\'\n]+)["\']?', status_data)
                    if emotion_match:
                        current_emotion = emotion_match.group(1).strip()
            
            # 格式化输出
            result = f"好感度：\n"
            result += f"用户：{user_name}\n"
            result += f"唯一标识符：{user_id}\n"
            result += f"好感度：{affinity}\n"
            result += f"当前与你对话的情绪：{current_emotion}\n"
            result += f"状态：{status}"
            
            yield event.plain_result(result)
                
        except Exception as e:
            logger.error(f"[Personification] 查看好感度失败: {e}")
            yield event.plain_result(f"查看好感度失败: {str(e)}")
    
    @filter.command("清除好感度")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def clear_affinity(self, event: AstrMessageEvent, user_id: str):
        """清除指定用户的好感度（仅管理员）
        
        Args:
            user_id: 用户ID
        """
        try:
            # 清除好感度（设置为默认值）
            default_affinity = self.config.get('affinity', {}).get('default_value', 0)
            await self.affinity_system.set_affinity(user_id, default_affinity)
            
            # 如果在黑名单中，从黑名单移除
            if await self.blacklist_manager.is_in_blacklist(user_id):
                await self.blacklist_manager.remove_from_blacklist(user_id)
            
            yield event.plain_result(f"已清除用户 {user_id} 的好感度，重置为 {default_affinity}")
                
        except Exception as e:
            logger.error(f"[Personification] 清除好感度失败: {e}")
            yield event.plain_result(f"清除好感度失败: {str(e)}")
    
    @filter.command("查看黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def view_blacklist(self, event: AstrMessageEvent):
        """查看黑名单列表（仅管理员）"""
        try:
            blacklist = await self.blacklist_manager.get_blacklist()
            
            if not blacklist:
                yield event.plain_result("黑名单为空")
                return
            
            result = "黑名单列表:\n"
            for item in blacklist:
                result += f"- {item['id']} (原因: {item['reason']}, 时间: {item['timestamp']})\n"
            
            yield event.plain_result(result)
                
        except Exception as e:
            logger.error(f"[Personification] 查看黑名单失败: {e}")
            yield event.plain_result(f"查看黑名单失败: {str(e)}")
    
    @filter.command("移除黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def remove_blacklist(self, event: AstrMessageEvent, user_id: str):
        """从黑名单中移除用户（仅管理员）
        
        Args:
            user_id: 用户ID
        """
        try:
            if await self.blacklist_manager.remove_from_blacklist(user_id):
                yield event.plain_result(f"已将用户 {user_id} 从黑名单中移除")
            else:
                yield event.plain_result(f"用户 {user_id} 不在黑名单中")
                
        except Exception as e:
            logger.error(f"[Personification] 移除黑名单失败: {e}")
            yield event.plain_result(f"移除黑名单失败: {str(e)}")
    
    @filter.command("发说说")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def publish_qzone(self, event: AstrMessageEvent, content: str = None):
        """手动发送QQ空间动态（仅管理员）
        
        Args:
            content: 动态内容，不填则自动生成
        """
        try:
            if not self.qzone_system or not self.qzone_system.enabled:
                yield event.plain_result("QQ空间系统未启用")
                return
            
            # 发布动态
            result = await self.qzone_system.qzone_adapter.publish_post(content=content)
            
            if result.get('success'):
                yield event.plain_result(f"✅ 成功发布QQ空间动态\n\n{result.get('content', '')}")
            else:
                yield event.plain_result(f"❌ 发布失败: {result.get('error')}")
                
        except Exception as e:
            logger.error(f"[Personification] 发布QQ空间失败: {e}")
            yield event.plain_result(f"发布失败: {str(e)}")
    
    @filter.command("看说说")
    async def view_qzone_feeds(self, event: AstrMessageEvent, num: int = 3):
        """查看最近的QQ空间动态
        
        Args:
            num: 查看数量，默认3条
        """
        try:
            if not self.qzone_system or not self.qzone_system.enabled:
                yield event.plain_result("QQ空间系统未启用")
                return
            
            # 获取动态
            feeds = await self.qzone_system.qzone_adapter.get_recent_feeds(num=num)
            
            if not feeds:
                yield event.plain_result("暂无动态")
                return
            
            result = f"📱 最近的QQ空间动态（共{len(feeds)}条）:\n\n"
            for i, feed in enumerate(feeds, 1):
                result += f"{i}. {feed['text'][:50]}...\n"
                if feed.get('images'):
                    result += f"   🖼️ {len(feed['images'])}张图片\n"
                result += f"   💬 {feed['comments_count']}条评论\n\n"
            
            yield event.plain_result(result)
                
        except Exception as e:
            logger.error(f"[Personification] 查看动态失败: {e}")
            yield event.plain_result(f"查看失败: {str(e)}")
    
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        """处理消息事件"""
        try:
            # 获取发送者ID和会话类型
            sender_id = event.get_sender_id()
            is_group = bool(event.get_group_id())  # 如果有group_id则是群聊
            session_id = event.get_group_id() if is_group else sender_id
            
            # 检查是否在黑名单中
            if await self.blacklist_manager.is_in_blacklist(sender_id):
                logger.debug(f"[Personification] 用户 {sender_id} 在黑名单中，忽略消息")
                return
            
            if is_group and await self.blacklist_manager.is_in_blacklist(session_id):
                logger.debug(f"[Personification] 群 {session_id} 在黑名单中，忽略消息")
                return
            
            # 处理拟人化回复
            await self.personification_manager.handle_message(event)
            
            # 如果事件已被处理，阻止后续传播
            if event.get_extra("astrbot_personification_handled", False):
                event.stop_event()
                logger.debug(f"[Personification] 事件已处理，阻止AstrBot默认回复")
            
        except Exception as e:
            logger.error(f"[Personification] 处理消息失败: {e}")
    
    async def terminate(self):
        """插件卸载/停用时的清理工作"""
        logger.info("[Personification] 正在关闭拟人化插件...")
        
        if self.qzone_system:
            await self.qzone_system.shutdown()
        
        if self.personification_manager:
            await self.personification_manager.shutdown()
        
        logger.info("[Personification] 拟人化插件已关闭")
