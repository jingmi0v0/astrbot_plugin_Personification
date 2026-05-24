"""
QQ空间适配器 - 将QQ文件夹的源码适配到拟人化插件
"""
import sys
from pathlib import Path

# 添加QQ目录到Python路径
qq_dir = Path(__file__).parent.parent.parent.parent / "QQ"
if str(qq_dir) not in sys.path:
    sys.path.insert(0, str(qq_dir))

from astrbot.api import logger


class QZoneAdapter:
    """QQ空间适配器，桥接QQ文件夹的源码和拟人化插件"""
    
    def __init__(self, context, config, personification_manager):
        self.context = context
        self.config = config
        self.personification_manager = personification_manager
        
        # QQ空间相关组件（延迟初始化）
        self.qzone_api = None
        self.session = None
        self.service = None
        self.db = None
        self.llm_action = None
        self.sender = None
        
        # 配置参数
        qzone_config = config.get('qzone', {})
        self.enabled = qzone_config.get('enabled', False)
        
    async def initialize(self):
        """初始化QQ空间适配器"""
        if not self.enabled:
            logger.info("[QZoneAdapter] QQ空间系统已禁用")
            return
        
        try:
            logger.info("[QZoneAdapter] 正在初始化QQ空间适配器...")
            
            # 动态导入QQ模块
            from core.config import PluginConfig
            from core.qzone import QzoneAPI, QzoneSession
            from core.db import PostDB
            from core.llm_action import LLMAction
            from core.sender import Sender
            from core.service import PostService
            from astrbot.core import AstrBotConfig
            
            # 创建配置对象
            qq_config = AstrBotConfig(self.config)
            plugin_config = PluginConfig(qq_config, self.context)
            
            # 初始化会话
            self.session = QzoneSession(plugin_config)
            
            # 初始化QQ空间API
            self.qzone_api = QzoneAPI(self.session, plugin_config)
            
            # 初始化数据库
            self.db = PostDB(plugin_config)
            await self.db.initialize()
            
            # 初始化LLM动作生成器
            self.llm_action = LLMAction(plugin_config)
            
            # 初始化发送器
            self.sender = Sender(plugin_config)
            
            # 初始化服务层
            self.service = PostService(
                self.qzone_api,
                self.session,
                self.db,
                self.llm_action
            )
            
            logger.info("[QZoneAdapter] QQ空间适配器初始化完成")
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 初始化失败: {e}", exc_info=True)
            raise
    
    async def generate_qzone_content(self) -> str:
        """生成符合角色设定的QQ空间内容
        
        使用拟人化插件的配置和角色设定来生成内容
        
        Returns:
            生成的说说内容
        """
        try:
            # 获取角色设定（从config读取，无默认值）
            system_prompt = self.config.get('system', '')
            character_name = self.config.get('name') or '我'
            
            # 构建提示词，让AI根据角色设定生成内容
            prompt = f"""你是{character_name}，现在要发一条QQ空间动态。

请基于以下角色设定生成内容：
{system_prompt[:500]}  # 限制长度

要求：
1. 完全符合角色的说话风格和性格
2. 内容自然、真实，像真人发的动态
3. 长度适中（20-100字）
4. 可以包含日常生活的点滴、心情、感悟
5. 不要使用Markdown格式
6. 直接返回动态内容，不要有其他说明
7. 如果角色有特殊的说话习惯（如句尾加"喵~"），一定要保留

当前时间：{self._get_current_time()}

请生成一条QQ空间动态："""
            
            # 调用LLM
            provider_manager = self.context.provider_manager
            curr_provider = provider_manager.curr_provider
            
            if not curr_provider:
                logger.warning("[QZoneAdapter] 没有可用的LLM Provider")
                return self._get_default_content()
            
            result = await curr_provider.text_chat(
                prompt=prompt,
                session_id="qzone_generate"
            )
            
            content = result.get('completion', '') if result else ''
            
            if content:
                content = content.strip()
                # 限制长度
                if len(content) > 200:
                    content = content[:200] + "..."
                logger.info(f"[QZoneAdapter] 生成QQ空间内容: {content[:50]}...")
                return content
            else:
                return self._get_default_content()
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 生成内容失败: {e}")
            return self._get_default_content()
    
    async def publish_post(self, content: str = None, images: list = None) -> dict:
        """发布QQ空间动态
        
        Args:
            content: 动态内容，如果不提供则自动生成
            images: 图片URL列表
            
        Returns:
            发布结果
        """
        try:
            if not self.service:
                raise RuntimeError("QQ空间服务未初始化")
            
            # 如果没有提供内容，自动生成
            if not content:
                content = await self.generate_qzone_content()
            
            if not content and not images:
                raise ValueError("内容和图片不能同时为空")
            
            # 调用服务层发布
            from core.model import Post
            
            post = await self.service.publish_post(
                text=content,
                images=images or []
            )
            
            logger.info(f"[QZoneAdapter] 成功发布QQ空间动态: {post.tid}")
            
            return {
                'success': True,
                'tid': post.tid,
                'content': post.text,
                'images': post.images
            }
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 发布失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_recent_feeds(self, num: int = 5) -> list:
        """获取最近的动态
        
        Args:
            num: 获取数量
            
        Returns:
            动态列表
        """
        try:
            if not self.service:
                raise RuntimeError("QQ空间服务未初始化")
            
            posts = await self.service.query_feeds(
                pos=0,
                num=num,
                with_detail=False
            )
            
            return [
                {
                    'tid': post.tid,
                    'text': post.text,
                    'images': post.images,
                    'create_time': post.create_time,
                    'comments_count': len(post.comments)
                }
                for post in posts
            ]
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 获取动态失败: {e}")
            return []
    
    async def comment_on_feed(self, post_tid: int, content: str = None) -> dict:
        """评论指定的动态
        
        Args:
            post_tid: 动态ID
            content: 评论内容，如果不提供则自动生成
            
        Returns:
            评论结果
        """
        try:
            if not self.service:
                raise RuntimeError("QQ空间服务未初始化")
            
            # 获取动态
            posts = await self.service.query_feeds(
                pos=0,
                num=10,
                with_detail=True
            )
            
            target_post = None
            for post in posts:
                if post.tid == post_tid:
                    target_post = post
                    break
            
            if not target_post:
                return {'success': False, 'error': '未找到指定的动态'}
            
            # 如果没有提供评论内容，根据角色设定生成
            if not content:
                content = await self._generate_comment(target_post)
            
            if not content:
                return {'success': False, 'error': '生成评论内容失败'}
            
            # 执行评论
            await self.service.comment_posts(target_post)
            
            logger.info(f"[QZoneAdapter] 成功评论动态 {post_tid}")
            
            return {
                'success': True,
                'tid': post_tid,
                'comment': content
            }
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 评论失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    async def like_feed(self, post_tid: int) -> dict:
        """点赞指定的动态
        
        Args:
            post_tid: 动态ID
            
        Returns:
            点赞结果
        """
        try:
            if not self.service:
                raise RuntimeError("QQ空间服务未初始化")
            
            # 获取动态
            posts = await self.service.query_feeds(
                pos=0,
                num=10,
                with_detail=True
            )
            
            target_post = None
            for post in posts:
                if post.tid == post_tid:
                    target_post = post
                    break
            
            if not target_post:
                return {'success': False, 'error': '未找到指定的动态'}
            
            # 执行点赞
            await self.service.like_posts(target_post)
            
            logger.info(f"[QZoneAdapter] 成功点赞动态 {post_tid}")
            
            return {
                'success': True,
                'tid': post_tid
            }
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 点赞失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _generate_comment(self, post) -> str:
        """根据角色设定生成评论内容
        
        Args:
            post: 动态对象
            
        Returns:
            生成的评论内容
        """
        try:
            character_name = self.config.get('name', '我')
            system_prompt = self.config.get('system', '')
            
            prompt = f"""你是{character_name}，看到了一条QQ空间动态，需要发表评论。

角色设定：
{system_prompt[:500]}

动态内容：
{text}

图片：{', '.join(post.images[:3]) if post.images else '无'}

要求：
1. 评论要符合角色的性格和说话风格
2. 简短自然（10-50字）
3. 可以是赞美、调侃、共鸣等
4. 不要使用Markdown格式
5. 直接返回评论内容

请生成评论："""
            
            provider_manager = self.context.provider_manager
            curr_provider = provider_manager.curr_provider
            
            if not curr_provider:
                return "不错不错~"
            
            result = await curr_provider.text_chat(
                prompt=prompt,
                session_id="qzone_comment"
            )
            
            content = result.get('completion', '') if result else ''
            
            if content:
                return content.strip()[:100]
            else:
                return "不错不错~"
                
        except Exception as e:
            logger.error(f"[QZoneAdapter] 生成评论失败: {e}")
            return "不错不错~"
    
    def _get_default_content(self) -> str:
        """获取默认内容"""
        defaults = [
            "今天也是美好的一天呢~",
            "心情不错，记录一下",
            "今天的天气真好",
            "生活就是要开开心心的",
            "又是元气满满的一天"
        ]
        import random
        return random.choice(defaults)
    
    def _get_current_time(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y年%m月%d日 %H:%M")
    
    async def shutdown(self):
        """关闭QQ空间适配器"""
        logger.info("[QZoneAdapter] 正在关闭...")
        
        if self.qzone_api:
            await self.qzone_api.close()
        
        logger.info("[QZoneAdapter] 已关闭")
