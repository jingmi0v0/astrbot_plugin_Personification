"""
拟人化插件 — 基于 chatluna-character 架构重写
"""
import asyncio
import time
import random
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .core.message_collector import MessageCollector
from .core.preset import Preset, PresetLoader
from .core.affinity import AffinitySystem
from .core.blacklist import BlacklistManager
from .core.qzone_system import QZoneSystem


@register("astrbot_plugin_personification", "jingmi0v0",
          "拟人化聊天 — 基于 chatluna-character 架构", "2.0.0")
class PersonificationPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.collector = None
        self._config_path = Path(__file__).parent / "config.yml"

    def _load_config(self) -> dict:
        import yaml
        if self._config_path.exists():
            with open(self._config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    async def initialize(self):
        logger.info("[Personification] 正在初始化...")

        config = self._load_config()

        # 加载预设
        preset_dir = Path(__file__).parent / (config.get('preset_path', 'resources/presets'))
        loader = PresetLoader(str(preset_dir))
        loader.load_all()
        default_name = config.get('defaultPreset', '')
        preset_from_loader = loader.get(default_name) if default_name else None

        if preset_from_loader:
            preset = preset_from_loader
            # 预设中的字段优先，config.yml 顶层字段作为兜底
            preset.config = config
            logger.info(f"[Personification] 加载预设: {preset.name}")
        else:
            # 没有预设则从 config.yml 顶层字段创建
            preset = Preset("default", {
                'system': config.get('system', ''),
                'input': config.get('input', ''),
                'status': config.get('status', ''),
                'nick_name': config.get('nick_name', []),
                'mute_keyword': config.get('mute_keyword', []),
                'name': config.get('name', ''),
            })
            preset.config = config
            logger.info("[Personification] 使用 config.yml 顶层配置")

        self.collector = MessageCollector(self.context, config, preset)

        # 好感度系统
        self.affinity = AffinitySystem(config)
        await self.affinity.initialize()

        # 黑名单系统
        self.blacklist = BlacklistManager(config)
        await self.blacklist.initialize()
        if self.collector:
            self.collector.set_blacklist(self.blacklist)
            self.collector.set_affinity(self.affinity)

        # QQ空间系统
        self.qzone_system = QZoneSystem(self.context, config, None)
        await self.qzone_system.initialize()

        logger.info(f"[Personification] 初始化完成（预设: {preset.name}）")

    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_message(self, event: AstrMessageEvent):
        if not self.collector:
            return
        try:
            await self.collector.handle_message(event)
            if event.get_extra("astrbot_personification_handled", False):
                event.should_call_llm(True)
                event.stop_event()
        except Exception as e:
            logger.error(f"[Personification] 处理消息失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    @filter.command("重载配置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reload_config(self, event: AstrMessageEvent):
        await self.initialize()
        yield event.plain_result("✅ 配置重载完成")

    @filter.command("查看好感度")
    async def view_affinity(self, event: AstrMessageEvent, user_id: str = None):
        if not user_id:
            user_id = event.get_sender_id()
        val = await self.affinity.get(user_id)
        name = event.get_sender_name() if user_id == event.get_sender_id() else f"用户{user_id}"
        yield event.plain_result(
            f"好感度：\n"
            f"用户：{name}\n"
            f"唯一标识符：{user_id}\n"
            f"好感度：{val}\n"
            f"状态：{"拉黑" if val <= -80 else "正常"}"
        )

    @filter.command("设置好感度")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def set_affinity(self, event: AstrMessageEvent, user_id: str, value: int):
        value = max(-100, min(100, value))
        await self.affinity.set(user_id, value)
        yield event.plain_result(f"已设置用户 {user_id} 的好感度为 {value}")

    @filter.command("清除好感度")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def clear_affinity(self, event: AstrMessageEvent, user_id: str):
        await self.affinity.clear(user_id)
        yield event.plain_result(f"已清除用户 {user_id} 的好感度")

    @filter.command("添加黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_blacklist(self, event: AstrMessageEvent, target_type: str, target_id: str):
        await self.blacklist.add(target_id, reason="", target_type=target_type)
        yield event.plain_result(f"已添加 {target_type} {target_id} 到黑名单")

    @filter.command("查看黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def view_blacklist(self, event: AstrMessageEvent):
        items = await self.blacklist.list_all()
        if not items:
            yield event.plain_result("黑名单为空")
            return
        result = "黑名单列表:\n"
        for item in items:
            ts = __import__('time').strftime('%Y-%m-%d %H:%M', __import__('time').localtime(item['timestamp'])) if item['timestamp'] else ''
            result += f"- {item['id']} [{item['type']}] (原因: {item['reason']}, 时间: {ts})\n"
        yield event.plain_result(result)

    @filter.command("移除黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def remove_blacklist(self, event: AstrMessageEvent, user_id: str):
        if await self.blacklist.remove(user_id):
            yield event.plain_result(f"已将用户 {user_id} 从黑名单中移除")
        else:
            yield event.plain_result(f"用户 {user_id} 不在黑名单中")

    @filter.command("发说说")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def publish_qzone(self, event: AstrMessageEvent, content: str = None):
        try:
            if not self.qzone_system or not self.qzone_system.enabled:
                yield event.plain_result("QQ空间系统未启用（config.yml 中 qzone.enabled: true）")
                return
            result = await self.qzone_system.qzone_adapter.publish_post(content=content)
            if result.get('success'):
                yield event.plain_result(f"✅ 成功发布QQ空间动态\n\n{result.get('content', '')}")
            else:
                yield event.plain_result(f"❌ 发布失败: {result.get('error')}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Personification] 发说说失败: {e}\n{tb}")
            yield event.plain_result(f"❌ 发说说失败: {e}")

    @filter.command("看说说")
    async def view_qzone(self, event: AstrMessageEvent, num: int = 3):
        try:
            if not self.qzone_system or not self.qzone_system.enabled:
                yield event.plain_result("QQ空间系统未启用（config.yml 中 qzone.enabled: true）")
                return
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
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Personification] 看说说失败: {e}\n{tb}")
            yield event.plain_result(f"❌ 看说说失败: {e}")

    async def terminate(self):
        if self.qzone_system:
            await self.qzone_system.shutdown()
        logger.info("[Personification] 关闭...")
