"""
QQ空间系统 - 自动发送QQ空间动态
使用QQ文件夹的源码进行适配
"""
import asyncio
import random
import time
from datetime import datetime
from typing import List, Optional
from astrbot.api import logger


class QZoneSystem:
    """QQ空间系统，负责自动发送QQ空间动态"""
    
    def __init__(self, context, config, personification_manager):
        self.context = context
        self.config = config
        self.personification_manager = personification_manager
        
        # QQ空间适配器（惰性导入，避免依赖外部 QQ 插件导致加载失败）
        from .qzone_adapter import QZoneAdapter
        self.adapter = QZoneAdapter(context, config, personification_manager)
        self.qzone_adapter = self.adapter  # 别名，兼容 main.py 调用
        
        # 定时任务
        self.post_scheduler_task = None
        self.is_running = False
        
        # 配置参数
        qzone_config = config.get('qzone', {})
        self.enabled = qzone_config.get('enabled', False)
        self.post_interval_hours = qzone_config.get('post_interval_hours', 6)
        self.post_time_range = qzone_config.get('post_time_range', {'start': 8, 'end': 22})
        self.auto_generate_content = qzone_config.get('auto_generate_content', True)
    
    async def initialize(self):
        """初始化QQ空间系统"""
        logger.info("[QZoneSystem] 正在初始化...")
        
        if not self.enabled:
            logger.info("[QZoneSystem] QQ空间系统已禁用")
            return
        
        # 初始化适配器（会加载QQ文件夹的源码）
        await self.adapter.initialize()
        
        # 启动定时发帖任务
        if self.enabled:
            self.start_scheduler()
        
        logger.info("[QZoneSystem] 初始化完成")
    

    
    def start_scheduler(self):
        """启动定时发帖调度器"""
        if self.post_scheduler_task and not self.post_scheduler_task.done():
            logger.warning("[QZoneSystem] 定时发帖任务已在运行")
            return
        
        self.is_running = True
        self.post_scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("[QZoneSystem] 定时发帖任务已启动")
    
    async def stop_scheduler(self):
        """停止定时发帖调度器"""
        self.is_running = False
        
        if self.post_scheduler_task:
            self.post_scheduler_task.cancel()
            try:
                await self.post_scheduler_task
            except asyncio.CancelledError:
                pass
        
        logger.info("[QZoneSystem] 定时发帖任务已停止")
    
    async def _scheduler_loop(self):
        """调度器主循环"""
        logger.info("[QZoneSystem] 调度器开始运行")
        
        while self.is_running:
            try:
                # 检查是否在当前允许的时间范围内
                current_hour = datetime.now().hour
                start_hour = self.post_time_range.get('start', 8)
                end_hour = self.post_time_range.get('end', 22)
                
                if start_hour <= current_hour < end_hour:
                    # 在允许的时间范围内，执行发帖
                    await self._post_to_qzone()
                    
                    # 等待下一个发帖周期
                    interval_seconds = self.post_interval_hours * 3600
                    logger.info(f"[QZoneSystem] 下次发帖将在 {self.post_interval_hours} 小时后")
                    
                    # 分段等待，以便能够及时响应停止信号
                    wait_steps = 12  # 分成12步等待
                    for _ in range(wait_steps):
                        if not self.is_running:
                            break
                        await asyncio.sleep(interval_seconds / wait_steps)
                else:
                    # 不在允许的时间范围内，等待1小时再检查
                    logger.debug(f"[QZoneSystem] 当前时间 {current_hour}:00 不在发帖时间范围内 ({start_hour}:00-{end_hour}:00)")
                    await asyncio.sleep(3600)
                    
            except asyncio.CancelledError:
                logger.info("[QZoneSystem] 调度器被取消")
                break
            except Exception as e:
                logger.error(f"[QZoneSystem] 调度器循环出错: {e}", exc_info=True)
                # 出错后等待一段时间再继续
                await asyncio.sleep(300)
    
    async def _post_to_qzone(self):
        """发送QQ空间动态"""
        try:
            logger.info("[QZoneSystem] 准备发送QQ空间动态...")
            
            # 生成动态内容
            content = await self._generate_post_content()
            
            if not content:
                logger.warning("[QZoneSystem] 生成的动态内容为空，跳过发送")
                return
            
            # 发送到QQ空间
            success = await self._send_post(content)
            
            if success:
                logger.info(f"[QZoneSystem] 成功发送QQ空间动态: {content[:50]}...")
            else:
                logger.error("[QZoneSystem] 发送QQ空间动态失败")
            
        except Exception as e:
            logger.error(f"[QZoneSystem] 发送QQ空间动态失败: {e}", exc_info=True)
    
    async def _generate_post_content(self) -> str:
        """生成动态内容"""
        try:
            if self.auto_generate_content:
                # 使用LLM生成拟人化的动态内容
                content = await self._generate_with_llm()
                return content
            else:
                # 从预设模板中随机选择
                templates = self.config.get('qzone', {}).get('templates', [])
                if templates:
                    template = random.choice(templates)
                    # 可以添加一些变量替换
                    content = template.format(
                        time=datetime.now().strftime("%H:%M"),
                        date=datetime.now().strftime("%Y-%m-%d")
                    )
                    return content
                else:
                    return "今天也是美好的一天呢~"
        
        except Exception as e:
            logger.error(f"[QZoneSystem] 生成动态内容失败: {e}")
            return "今日心情不错~"
    
    async def _generate_with_llm(self) -> str:
        """使用适配器生成拟人化动态内容（基于角色设定）"""
        try:
            # 使用适配器生成符合角色设定的内容
            content = await self.adapter.generate_qzone_content()
            return content
        
        except Exception as e:
            logger.error(f"[QZoneSystem] 生成内容失败: {e}")
            return "今天也是美好的一天呢~"
    
    async def _send_post(self, content: str) -> bool:
        """发送动态到QQ空间（使用适配器）
        
        Args:
            content: 动态内容
            
        Returns:
            是否发送成功
        """
        try:
            # 使用适配器发布动态
            result = await self.adapter.publish_post(content=content)
            
            if result.get('success'):
                logger.info(f"[QZoneSystem] 成功发布动态 (tid={result.get('tid')})")
                return True
            else:
                logger.error(f"[QZoneSystem] 发布失败: {result.get('error')}")
                return False
            
        except Exception as e:
            logger.error(f"[QZoneSystem] 发送动态失败: {e}", exc_info=True)
            return False
    
    async def manual_post(self, content: str = None) -> bool:
        """手动发送动态
        
        Args:
            content: 动态内容，如果不提供则自动生成
            
        Returns:
            是否发送成功
        """
        if not content:
            content = await self._generate_post_content()
        
        return await self._send_post(content)
    
    async def shutdown(self):
        """关闭QQ空间系统"""
        logger.info("[QZoneSystem] 正在关闭...")
        await self.stop_scheduler()
        logger.info("[QZoneSystem] 已关闭")
