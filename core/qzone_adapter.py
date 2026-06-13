"""
QQ空间适配器 - 适配本地 QQ 插件模块或内置桩模块
"""
from pathlib import Path
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.provider.entities import ProviderType


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
        self.llm = None
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
            
            # 从 personification config 的 qzone 节提取 QQ 插件所需配置
            qzone_cfg = self.config.get('qzone', {})
            
            # 构建一个完整的配置字典，包含 QQ 插件 PluginConfig 需要的所有字段
            full_config = {
                'manage_group': qzone_cfg.get('manage_group', ''),
                'pillowmd_style_dir': qzone_cfg.get('pillowmd_style_dir', ''),
                'cookies_str': qzone_cfg.get('cookies_str', ''),
                'timeout': qzone_cfg.get('timeout', 30),
                'show_name': qzone_cfg.get('show_name', True),
            }
            
            # llm 子配置
            llm_cfg = qzone_cfg.get('llm', {})
            full_config['llm'] = {
                'post_provider_id': llm_cfg.get('post_provider_id', ''),
                'post_prompt': llm_cfg.get('post_prompt', ''),
                'comment_provider_id': llm_cfg.get('comment_provider_id', ''),
                'comment_prompt': llm_cfg.get('comment_prompt', ''),
                'reply_provider_id': llm_cfg.get('reply_provider_id', ''),
                'reply_prompt': llm_cfg.get('reply_prompt', ''),
            }
            
            # source 子配置
            source_cfg = qzone_cfg.get('source', {})
            full_config['source'] = {
                'ignore_groups': source_cfg.get('ignore_groups', []),
                'ignore_users': source_cfg.get('ignore_users', []),
                'post_max_msg': source_cfg.get('post_max_msg', 20),
            }
            
            # trigger 子配置
            trigger_cfg = qzone_cfg.get('trigger', {})
            full_config['trigger'] = {
                'publish_cron': trigger_cfg.get('publish_cron', '0 0 * * *'),
                'publish_offset': trigger_cfg.get('publish_offset', 0),
                'comment_cron': trigger_cfg.get('comment_cron', '0 0 * * *'),
                'comment_offset': trigger_cfg.get('comment_offset', 0),
                'read_prob': trigger_cfg.get('read_prob', 0.3),
                'send_admin': trigger_cfg.get('send_admin', False),
                'like_when_comment': trigger_cfg.get('like_when_comment', False),
            }
            
            # 动态导入内置的 QQ 插件模块或降级到桩模块
            _using_stubs = False
            try:
                from .qq_plugin.config import PluginConfig
                from .qq_plugin.qzone import QzoneAPI, QzoneSession
                from .qq_plugin.db import PostDB
                from .qq_plugin.llm_action import LLMAction
                from .qq_plugin.sender import Sender
                from .qq_plugin.service import PostService, Post
            except ModuleNotFoundError:
                logger.warning("[QZoneAdapter] QQ 插件模块加载失败，使用内置桩模块")
                _using_stubs = True
                from .qq_stubs.config import PluginConfig
                from .qq_stubs.qzone import QzoneAPI, QzoneSession
                from .qq_stubs.db import PostDB
                from .qq_stubs.service import LLMAction, Sender, PostService, Post
            
            # 创建配置对象
            if _using_stubs:
                plugin_config = PluginConfig(full_config, self.context)
            else:
                # 写临时配置文件（AstrBotConfig 需要文件路径）
                _tmp_cfg_path = Path(get_astrbot_data_path()) / "plugins" / "personification_qzone_tmp.json"
                import json as _json
                _tmp_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                _tmp_cfg_path.write_text(_json.dumps(full_config, ensure_ascii=False), encoding="utf-8")
                from astrbot.core import AstrBotConfig as _ABC
                qq_config = _ABC(str(_tmp_cfg_path))
                plugin_config = PluginConfig(qq_config, self.context)
            
            # 自动从 AstrBot 平台注入 CQHttp client（aiocqhttp 已登录，不需要手动配 cookie）
            try:
                platform = self.context.get_platform("aiocqhttp")
                if platform and hasattr(platform, 'bot'):
                    plugin_config.client = platform.bot
                    logger.info("[QZoneAdapter] 已自动获取 QQ bot client，cookies 将自动获取")
            except Exception:
                pass

            # 初始化会话
            self.session = QzoneSession(plugin_config)
            
            # 初始化QQ空间API
            self.qzone_api = QzoneAPI(self.session, plugin_config)
            
            # 初始化数据库
            self.db = PostDB(plugin_config)
            await self.db.initialize()
            
            # 初始化LLM动作生成器
            self.llm = LLMAction(plugin_config)
            
            # 初始化发送器
            self.sender = Sender(plugin_config)
            
            # 初始化服务层
            self.service = PostService(
                self.qzone_api,
                self.session,
                self.db,
                self.llm
            )
            
            logger.info("[QZoneAdapter] QQ空间适配器初始化完成")
            
        except Exception as e:
            logger.error(f"[QZoneAdapter] 初始化失败: {e}", exc_info=True)
            raise
    
    async def generate_qzone_content(self) -> str:
        """生成符合角色设定的QQ空间内容
        
        与主插件使用相同的角色设定和状态上下文，确保拟人化一致
        
        Returns:
            生成的说说内容
        """
        try:
            # 获取完整的角色设定（原样传给 system_prompt，与主插件一致）
            character_system = self.config.get('system', '')
            character_name = self.config.get('name') or '我'
            
            # 获取当前角色状态（与主插件使用相同的 status 上下文）
            current_status = self.config.get('status', '')
            if self.personification_manager and hasattr(self.personification_manager, 'default_status'):
                current_status = self.personification_manager.default_status
            
            # 构建 user_prompt（包含状态上下文，与主插件的拟人化一致）
            user_prompt = f"""你现在要发一条QQ空间动态。

你当前的状态：
{current_status}

要求：
1. 完全符合你的角色设定和说话风格
2. 根据你当前的心情和状态自然表达
3. 内容自然、真实，像真人发的动态
4. 长度适中（20-100字）
5. 可以包含日常生活的点滴、心情、感悟
6. 不要使用Markdown格式
7. 直接返回动态内容，不要有其他说明

当前时间：{self._get_current_time()}

请生成一条QQ空间动态："""
            
            # 调用LLM（完整角色设定作为 system_prompt，与主插件一致）
            provider_manager = self.context.provider_manager
            curr_provider = provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
            
            if not curr_provider:
                logger.warning("[QZoneAdapter] 没有可用的LLM Provider")
                return self._get_default_content()
            
            result = await curr_provider.text_chat(
                prompt=user_prompt,
                session_id="personification_temp",
                system_prompt=character_system
            )
            
            content = result.completion_text if result else ''
            
            if content:
                content = content.strip()
                # 限制长度
                if len(content) > 200:
                    content = content[:200] + "..."
                logger.reply(f"生成QQ空间内容: {content[:50]}...")
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
            character_system = self.config.get('system', '')
            
            # 获取当前角色状态
            current_status = self.config.get('status', '')
            if self.personification_manager and hasattr(self.personification_manager, 'default_status'):
                current_status = self.personification_manager.default_status
            
            user_prompt = f"""你看到了一条QQ空间动态，需要发表评论。

你当前的状态：
{current_status}

动态内容：
{post.text}

图片：{', '.join(post.images[:3]) if post.images else '无'}

要求：
1. 评论要符合你的角色性格和说话风格
2. 根据你当前的心情和状态自然表达
3. 简短自然（10-50字）
4. 可以是赞美、调侃、共鸣等
5. 不要使用Markdown格式
6. 直接返回评论内容

请生成评论："""
            
            provider_manager = self.context.provider_manager
            curr_provider = provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
            
            if not curr_provider:
                return "不错不错~"
            
            result = await curr_provider.text_chat(
                prompt=user_prompt,
                session_id="personification_temp",
                system_prompt=character_system
            )
            
            content = result.completion_text if result else ''
            
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
            try:
                await self.qzone_api.close()
            except Exception:
                pass
        
        logger.info("[QZoneAdapter] 已关闭")
