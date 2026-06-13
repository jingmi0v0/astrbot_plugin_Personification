"""消息收集器 — 完整管线（消息存储、过滤、触发、回复生成）"""
import time
import random
import asyncio
from collections import defaultdict
from typing import Optional

from astrbot.api import logger
from astrbot.core.message.components import Plain, At
from astrbot.core.message.message_event_result import MessageChain

from .types import GroupLock
from .chat_pipeline import ChatPipeline
from .rest import RestSystem
from .trigger import TriggerStore
from .filter import ActivitySystem
from .response_parser import parse_response, clean_response_text


class MessageCollector:
    """核心服务 — 对应 chatluna-character MessageCollector + 所有子插件"""

    def __init__(self, context, config: dict, preset):
        self.context = context
        self.config = config
        self.preset = preset

        # 子系统
        self.chat = ChatPipeline(context, config, preset)
        self.rest = RestSystem(config)
        self.triggers = TriggerStore()
        self.activity = ActivitySystem(config)

        # 数据
        self.messages: dict[str, list] = defaultdict(list)
        self.locks: dict[str, GroupLock] = {}
        self.active_convs: dict[str, dict] = {}
        self._mute_state: dict[str, float] = {}
        self.message_interval_count: dict[str, int] = defaultdict(int)

        # 快捷
        self.nicknames = config.get('nick_name', [])
        self.character_name = config.get('name', '')
        self._blacklist = None
        self._affinity = None

    def set_blacklist(self, bl):
        self._blacklist = bl

    def set_affinity(self, af):
        self._affinity = af

    # ── 配置合并（群聊/私聊 → 全局覆盖） ──

    def _cfg(self, key: str, default=None, session_key: str = None):
        """读取配置，支持群/用户级覆盖"""
        val = self.config.get(key)
        if val is not None:
            return val
        return default

    def _group_cfg(self, key: str, default=None):
        """从 globalGroupConfig 读取"""
        gc = self.config.get('globalGroupConfig', {})
        return gc.get(key, default)

    def _private_cfg(self, key: str, default=None):
        pc = self.config.get('globalPrivateConfig', {})
        return pc.get(key, default)

    # ═══════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════

    async def handle_message(self, event) -> Optional[str]:
        sender_id = event.get_sender_id()
        is_group = bool(event.get_group_id())
        session_key = event.get_group_id() if is_group else sender_id
        message_str = event.message_str.strip()

        # 自身消息过滤
        try:
            if sender_id == event.get_self_id():
                return None
        except Exception:
            pass

        # 黑名单过滤（用户级 + 群级）
        if self._blacklist is not None:
            if await self._blacklist.is_blacklisted(sender_id):
                return None
            if is_group and await self._blacklist.is_blacklisted(session_key):
                return None

        # 空消息过滤（纯@不过滤）
        if not message_str:
            if not self._has_at(event):
                return None

        # NOTSAY 命令过滤（不触发回复也不进历史）
        notsay = self.config.get('NOTSAY', [])
        if message_str in notsay or message_str.strip('/') in notsay:
            return None

        # 取消各类计时器
        self._cancel_timers(session_key, sender_id)

        # 活跃对话追踪
        self._track_active_conv(session_key, sender_id, message_str)

        # 休息/唤醒
        resting = self.rest.is_resting(session_key)
        awake = self.rest.is_awake(session_key)
        if resting and not awake:
            if not await self.rest.try_wakeup(session_key, event):
                return None
            awake = True

        # 闭嘴
        if self._check_mute(session_key, message_str):
            return None

        # 存消息
        self._store_msg(session_key, sender_id, event, message_str)
        self.triggers.set_last_session(session_key, sender_id, message_str)
        self.activity.on_message(session_key, sender_id)
        self.message_interval_count[session_key] += 1

        # 判断触发
        trigger = await self._decide_trigger(event, session_key, sender_id, is_group, awake)
        if not trigger:
            return None

        # 阻止默认回复
        event.stop_event()
        event.set_extra("astrbot_personification_handled", True)

        # 生成回复
        await self._do_reply(event, session_key, sender_id, trigger, awake)

        # 活跃对话
        if trigger in ("mentioned", "active_conversation", "private_chat"):
            self.active_convs[session_key] = {"user_id": sender_id, "other_count": 0}

        self.activity.on_reply(session_key, is_group)
        self._set_cooldown(session_key)

        return trigger

    # ═══════════════════════════════════════════
    # 触发判断
    # ═══════════════════════════════════════════

    async def _decide_trigger(self, event, session_key: str, sender_id: str,
                               is_group: bool, awake: bool) -> Optional[str]:
        # 冷却检查
        if self._in_cooldown(session_key):
            return None

        # next_reply
        last = self.triggers._last_session.get(session_key, {})
        elapsed = time.time() - last.get('time', 0) if last else 999
        nr = self.triggers.check_next_reply(session_key, sender_id, elapsed)
        if nr:
            return "next_reply"

        # @/昵称
        if self._check_mentioned(event):
            msg = event.message_str.strip()
            if not msg or not self._has_at(event):
                return None  # 纯@→忽略（或者改为沉默检测）
            return "mentioned"

        # 活跃对话
        conv = self.active_convs.get(session_key)
        if conv and sender_id == conv['user_id']:
            conv['other_count'] = 0
            return "active_conversation"

        # 群聊触发
        if is_group:
            # 消息等待时间
            if self.activity.check_message_wait(session_key):
                return None

            trigger = self.activity.check_activity_trigger(session_key, is_group)
            if trigger:
                return trigger

        # 私聊
        if not is_group:
            return "private_chat"

        return None

    # ═══════════════════════════════════════════
    # 回复生成
    # ═══════════════════════════════════════════

    async def _do_reply(self, event, session_key: str, sender_id: str,
                         trigger_reason: str, is_groggy: bool):
        # 获取当前用户好感度
        aff_val = None
        if hasattr(self, '_affinity') and self._affinity:
            try:
                aff_val = await self._affinity.get(sender_id)
            except Exception:
                pass

        # 调用 chat pipeline（完整 prompt 构建 + LLM + 解析）
        parsed = await self.chat.process(
            event, session_key, self.messages.get(session_key, []),
            trigger_reason, sender_id, is_groggy,
            affinity_value=aff_val
        )
        if not parsed:
            return

        # 更新状态
        if parsed.get('status'):
            self.chat.status_cache[session_key] = parsed['status']
            self.chat.status_cache[f"user_{sender_id}"] = parsed['status']

        # 发送
        if parsed.get('messages'):
            await self._send_msgs(parsed['messages'], event, session_key)

        # 动作
        if parsed.get('actions'):
            await self._exec_actions(parsed['actions'], session_key)

    # ═══════════════════════════════════════════
    # 发送
    # ═══════════════════════════════════════════

    async def _send_msgs(self, msgs: list, event, session_key: str):
        max_len = self.config.get('max_msg_length', 30)
        base = self.config.get('typing_time', 3)
        per_char = self.config.get('typing_per_char', 0.3)
        inter = self.config.get('inter_msg_delay', 3)

        for i, msg in enumerate(msgs):
            if msg['type'] != 'text' or not msg.get('content'):
                continue
            content = clean_response_text(msg['content'])
            if not content:
                logger.warning(f"[Persona] 回复内容异常泄露，已拦截")
                continue
            if len(content) > max_len:
                content = content[:max_len]
            delay = (base if i == 0 else 0) + per_char * len(content)
            await asyncio.sleep(delay)
            await self._send_one(content, event, session_key)
            if i < len(msgs) - 1 and inter > 0:
                await asyncio.sleep(inter)

    async def _send_one(self, content: str, event, session_key: str):
        try:
            await event.send(MessageChain([Plain(content)]))
            try:
                bot_id = event.get_self_id()
            except Exception:
                bot_id = "bot"
            self.messages[session_key].append({
                'sender_id': bot_id,
                'sender_name': self.character_name or "Bot",
                'content': content,
                'timestamp': time.time(),
                'is_bot': True,
            })
        except Exception as e:
            logger.error(f"发送失败: {e}")

    # ═══════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════

    def _store_msg(self, key: str, sid: str, event, msg: str):
        max_msgs = self.config.get('max_messages', 40)
        self.messages[key].append({
            'sender_id': sid,
            'sender_name': event.get_sender_name() or '?',
            'content': msg,
            'timestamp': time.time(),
        })
        if len(self.messages[key]) > max_msgs:
            self.messages[key] = self.messages[key][-max_msgs:]

    def _check_mentioned(self, event) -> bool:
        msg = event.message_str or ''
        for nick in self.nicknames:
            if nick in msg:
                return True
        if hasattr(event.message_obj, 'message_chain'):
            for comp in event.message_obj.message_chain:
                if isinstance(comp, At):
                    try:
                        if str(comp.qq) == str(event.get_self_id()):
                            return True
                    except Exception:
                        pass
        return False

    def _has_at(self, event) -> bool:
        if hasattr(event.message_obj, 'message_chain'):
            for comp in event.message_obj.message_chain:
                if isinstance(comp, At):
                    try:
                        if str(comp.qq) == str(event.get_self_id()):
                            return True
                    except Exception:
                        pass
        return False

    def _check_mute(self, key: str, msg: str) -> bool:
        for kw in self.config.get('mute_keyword', []):
            if kw in msg:
                t = self.config.get('mute_time', 60)
                self._mute_state[key] = time.time() + t
                return True
        if key in self._mute_state:
            if time.time() < self._mute_state[key]:
                return True
            del self._mute_state[key]
        return False

    def _in_cooldown(self, key: str) -> bool:
        if key in self._mute_state:
            return time.time() < self._mute_state[key]
        return False

    def _set_cooldown(self, key: str):
        cd = self.config.get('speak_cooldown', 30)
        if isinstance(cd, dict):
            cd = random.randint(cd.get('min', 20), cd.get('max', 60))
        if cd > 0:
            self._mute_state[key] = time.time() + cd

    def _track_active_conv(self, key: str, sid: str, msg: str):
        conv = self.active_convs.get(key)
        if conv and sid != conv['user_id']:
            conv['other_count'] += 1
            max_o = self.config.get('active_conv_max_other_msgs', 3)
            if conv['other_count'] >= max_o:
                del self.active_convs[key]
        elif conv and sid == conv['user_id']:
            for kw in self.config.get('active_conv_end_keywords', []):
                if kw in msg:
                    del self.active_convs[key]
                    break

    def _cancel_timers(self, key: str, sid: str):
        pass

    async def _exec_actions(self, actions: list, key: str):
        for act in actions:
            if act['type'] == 'next_reply':
                self.triggers.set_next_reply(key, act.get('reason', ''))
            elif act['type'] == 'memory_add':
                c = act.get('content', '')
                if c:
                    self.chat.long_memory[key].append({
                        'content': c, 'timestamp': time.time(), 'author': 'bot'
                    })
            elif act['type'] == 'affinity':
                uid = act.get('user_id', '')
                delta = act.get('delta', 0)
                action = act.get('action', 'increase')
                if uid and hasattr(self, '_affinity') and self._affinity:
                    current = await self._affinity.get(uid)
                    new_val = current + (delta if action == 'increase' else -delta)
                    new_val = max(-100, min(100, new_val))
                    await self._affinity.set(uid, new_val)
                    logger.info(f"[Persona] 好感度 {action} {delta}: {uid} -> {new_val}")
                    # 自动拉黑
                    threshold = self.config.get('affinity', {}).get('blacklist_threshold', -80)
                    if new_val <= threshold and self._blacklist:
                        if not await self._blacklist.is_blacklisted(uid):
                            await self._blacklist.add(uid, f"好感度过低({new_val})自动拉黑")
                            logger.info(f"[Persona] 用户 {uid} 因好感度过低自动拉黑")
            elif act['type'] == 'poke':
                logger.info(f"[Persona] 戳 {act.get('user_id')}")
