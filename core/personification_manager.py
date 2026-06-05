"""
拟人化管理器 - 核心聊天逻辑
"""
import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from astrbot.api.event import AstrMessageEvent
from .plugin_logger import logger, set_config_provider
from astrbot.core.message.components import Plain, Image, At
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class PersonificationManager:
    """拟人化管理器，负责处理拟人化聊天逻辑"""
    
    def __init__(self, context, config, affinity_system, blacklist_manager):
        self.context = context
        self.config = config
        self.affinity_system = affinity_system
        self.blacklist_manager = blacklist_manager
        
        # 消息历史记录
        self.message_history: Dict[str, List[dict]] = {}
        
        # 状态持久化
        self.status_cache: Dict[str, dict] = {}
        
        # 触发条件缓存
        self.next_reply_conditions: Dict[str, dict] = {}
        self.wake_up_tasks: List[dict] = []
        
        # 配置参数（从config读取，无默认值）
        self.character_name = config.get('name')
        self.nicknames = config.get('nick_name', [])
        self.system_prompt = config.get('system', '')
        self.input_template = config.get('input', '')
        self.default_status = config.get('status', '')
        self.mute_keywords = config.get('mute_keyword', [])
        
        # 闭嘴状态
        self.mute_state: Dict[str, float] = {}  # session_id -> mute_until_timestamp
        
        # 长期记忆（文件持久化）
        self.long_memory: Dict[str, List[dict]] = {}  # session_id -> [{content, timestamp, author}]
        self.memory_file_path = Path(get_astrbot_data_path()) / "plugins" / "personification_memory.json"
        
        # 随机休息状态
        self.rest_state: Dict[str, float] = {}  # session_id -> rest_until_timestamp
        self.last_rest_check: float = 0.0

        # 唤醒系统状态（休息中被吵醒后短暂清醒）
        self.rest_message_count: Dict[str, int] = {}  # session_id -> 休息期间累计消息数
        self.awake_state: Dict[str, float] = {}  # session_id -> awake_until_timestamp（被唤醒后清醒到什么时候）

        # 会话持久化（对话历史 + 情感状态保存到本地 JSON 文件）
        self.session_data_file_path = Path(get_astrbot_data_path()) / "plugins" / "personification_session_data.json"
        self.session_meta: Dict[str, dict] = {}  # session_id -> {last_interaction_time, last_sender_id}

        # 说话频率限制（冷却状态）
        self.speak_cooldown_state: Dict[str, float] = {}  # session_id -> cooldown_until_timestamp

        # 历史去重缓存（chatluna-character 风格，检测前后轮消息重叠）
        self._history_dedup_cache: Dict[str, list] = {}

        # 被@但没说话的状态跟踪（沉默@计时器）
        self._pending_silent_mentions: Dict[str, asyncio.Task] = {}  # key: "{session_id}_{sender_id}" -> asyncio.Task

        # 无厘头/不完整消息的等待计时器
        self._pending_confused_timers: Dict[str, asyncio.Task] = {}  # key: "{session_id}_{sender_id}" -> asyncio.Task

        # 活跃对话状态（被@一次后保持回复，无需重复@）
        self.active_conversations: Dict[str, dict] = {}  # session_id -> {user_id, other_msgs_count, last_reply_time}

        # 唤醒后逐渐恢复计数器（神志不清 → 正常）
        self._groggy_recovery_count: Dict[str, int] = {}  # session_id -> 已发groggy消息数

    async def initialize(self):
        """初始化拟人化管理器"""
        # 设置日志级别配置提供器
        set_config_provider(lambda k, d: self.config.get(k, d))
        logger.info("[PersonificationManager] 正在初始化...")
        # 加载持久化数据（长期记忆等）
        await self._load_persistent_data()
        logger.info("[PersonificationManager] 初始化完成")

    async def _load_persistent_data(self):
        """加载持久化数据（长期记忆 + 会话历史 + 情感状态）"""
        # 从文件加载长期记忆
        try:
            if self.memory_file_path.exists():
                with open(self.memory_file_path, "r", encoding="utf-8") as f:
                    self.long_memory = json.load(f)
                total = sum(len(v) for v in self.long_memory.values())
                logger.info(f"[PersonificationManager] 加载了 {total} 条长期记忆")
            else:
                logger.info("[PersonificationManager] 长期记忆文件不存在，从零开始")
        except Exception as e:
            logger.error(f"[PersonificationManager] 加载长期记忆失败: {e}")

        # 从文件加载会话数据（对话历史 + 情感状态 + 元信息）
        try:
            if self.session_data_file_path.exists():
                with open(self.session_data_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.message_history = data.get('message_history', {})
                self.status_cache = data.get('status_cache', {})
                self.session_meta = data.get('session_meta', {})
                hist_count = sum(len(v) for v in self.message_history.values())
                logger.info(f"[PersonificationManager] 加载了 {len(self.message_history)} 个会话, {hist_count} 条消息历史, {len(self.status_cache)} 个情感状态")
                
                # 启动时清理长时间未互动的会话情感状态
                await self._cleanup_expired_sessions()
            else:
                logger.info("[PersonificationManager] 会话数据文件不存在，从零开始")
        except Exception as e:
            logger.error(f"[PersonificationManager] 加载会话数据失败: {e}")

    async def handle_message(self, event: AstrMessageEvent):
        """处理收到的消息"""
        try:
            sender_id = event.get_sender_id()
            is_group = bool(event.get_group_id())  # 如果有group_id则是群聊
            session_id = event.get_group_id() if is_group else sender_id

            # 过滤机器人自己的消息（防止平台回传导致自循环重复发言）
            try:
                self_id = event.get_self_id()
                if self_id and sender_id == self_id:
                    logger.debug(f"[PersonificationManager] Session {session_id} 忽略机器人自身消息")
                    return
            except Exception:
                pass

            # 活跃对话：其他人发消息时计数，超过阈值则结束对话
            active_conv = self.active_conversations.get(session_id)
            if active_conv:
                if sender_id != active_conv['user_id']:
                    active_conv['other_msgs_count'] += 1
                    max_other = self.config.get('active_conv_max_other_msgs', 3)
                    if active_conv['other_msgs_count'] >= max_other:
                        del self.active_conversations[session_id]
                        logger.info(f"[PersonificationManager] 与用户 {active_conv['user_id']} 的活跃对话结束（其他消息过多）")
                else:
                    # 对话用户本人的消息：检查是否明确表示不想聊了
                    msg = event.message_str
                    if msg:
                        self._check_conversation_end(msg, session_id)

            # 检查是否有人在沉默@或无厘头消息计时中，有则取消（用户说话了）
            self._cancel_pending_silent_mention(session_id, sender_id)
            self._cancel_pending_confused_timer(session_id, sender_id)

            # 过滤空消息（如手机端「用户正在输入中」的状态提示，NaoCat 等平台会发送空消息）
            # 但保留纯@消息（用户@了但没有打字的情况）
            message_str = event.message_str
            if not message_str or not message_str.strip():
                if not self._has_at_component(event):
                    logger.debug(f"[PersonificationManager] Session {session_id} 收到空消息（可能是输入状态提示），跳过")
                    return
                # 是纯@消息，message_str 保持空，继续处理

            # 检查是否处于闭嘴状态
            if self._is_muted(session_id):
                logger.debug(f"[PersonificationManager] Session {session_id} 处于闭嘴状态")
                return

            # 检查是否包含不需要回复的词语
            if self._check_ignore_keywords(message_str):
                logger.debug(f"[PersonificationManager] Session {session_id} 消息包含不需要回复的词语，忽略")
                return

            # 检查会话是否过期（长时间未互动则清空情感状态并降低好感度）
            await self._check_session_expiry(session_id, sender_id)

            # 检查是否处于休息状态（可能触发唤醒）
            is_resting = self._is_resting(session_id)
            is_awake = self._is_awake(session_id)

            if is_resting and not is_awake:
                # 休息中：计数消息，尝试唤醒
                woken = await self._try_wakeup(session_id, event)
                if not woken:
                    logger.debug(f"[PersonificationManager] Session {session_id} 处于休息状态")
                    return
                # 被唤醒了，更新 awake 标志，用神志不清模式生成回复
                is_awake = True
                logger.info(f"[PersonificationManager] Session {session_id} 被唤醒！")

            # 检查是否包含闭嘴关键词
            message_str = event.message_str
            if self._check_mute_keywords(message_str, session_id):
                logger.debug(f"[PersonificationManager] 检测到闭嘴关键词，Session {session_id} 进入闭嘴状态")
                return

            # 保存消息到历史
            await self._save_message_to_history(event, session_id)

            # 判断是否需要回复
            should_reply, trigger_reason = await self._should_reply(event, session_id)

            if not should_reply:
                logger.debug(f"[PersonificationManager] Session {session_id} 不需要回复")
                return

            # 说话频率检查：冷却期内不回复（被@和私聊时也等冷却，避免过快）
            if trigger_reason not in ("mentioned_silent", "next_reply_trigger"):
                if self._is_in_speak_cooldown(session_id):
                    logger.debug(f"[PersonificationManager] Session {session_id} 在说话冷却中，跳过回复")
                    return

            # 阻止AstrBot的默认LLM回复
            event.stop_event()
            # 设置标志，确保后续阶段也不会处理
            event.set_extra("astrbot_personification_handled", True)

            # 如果是被唤醒状态（is_awake=True），用神志不清模式生成回复
            if is_awake:
                trigger_reason = "wakeup_groggy"

            # 处理「被@但没说话」的情况：启动沉默计时器
            if trigger_reason == "mentioned_silent":
                event.stop_event()
                event.set_extra("astrbot_personification_handled", True)
                await self._start_silent_mention_timer(event, session_id, sender_id)
                return

            # 处理「无厘头/不完整消息」的情况：启动等待计时器
            # 但在活跃对话中，用户的短消息可能是正常回应，不触发等待
            if trigger_reason != "active_conversation" and self._is_confusing_message(message_str, session_id, sender_id):
                event.stop_event()
                event.set_extra("astrbot_personification_handled", True)
                await self._start_confused_message_timer(event, session_id, sender_id, message_str)
                return

            # 生成回复
            await self._generate_and_send_reply(event, session_id, trigger_reason)

            # 回复成功后，建立/更新活跃对话（被@、对话中用户无需重复@）
            if trigger_reason in ("mentioned", "active_conversation", "next_reply_trigger", "private_chat", "wakeup_groggy"):
                self.active_conversations[session_id] = {
                    "user_id": sender_id,
                    "other_msgs_count": 0,
                    "last_reply_time": time.time()
                }
                logger.debug(f"[PersonificationManager] 与用户 {sender_id} 建立活跃对话")

        except Exception as e:
            logger.error(f"[PersonificationManager] 处理消息失败: {e}", exc_info=True)

    def _is_muted(self, session_id: str) -> bool:
        """检查是否处于闭嘴状态"""
        if session_id in self.mute_state:
            if time.time() < self.mute_state[session_id]:
                return True
            else:
                # 闭嘴时间已过，清除状态
                del self.mute_state[session_id]
        return False

    def _is_in_speak_cooldown(self, session_id: str) -> bool:
        """检查是否在说话冷却中（防止频繁发言）"""
        if session_id in self.speak_cooldown_state:
            if time.time() < self.speak_cooldown_state[session_id]:
                return True
            else:
                del self.speak_cooldown_state[session_id]
        return False

    def _check_mute_keywords(self, message: str, session_id: str) -> bool:
        """检查是否包含闭嘴关键词"""
        if not self.mute_keywords:
            return False

        for keyword in self.mute_keywords:
            if keyword in message:
                # 设置闭嘴状态
                mute_time = self.config.get('mute_time', 60)
                self.mute_state[session_id] = time.time() + mute_time
                return True

        return False

    def _check_ignore_keywords(self, message: str) -> bool:
        """检查消息是否包含不需要回复的词语"""
        ignore_keywords = self.config.get('ignore_keywords', [])
        if not ignore_keywords:
            return False
        for keyword in ignore_keywords:
            if keyword in message:
                return True
        return False

    async def _save_message_to_history(self, event: AstrMessageEvent, session_id: str):
        """保存消息到历史记录（含超阈值压缩）"""
        if session_id not in self.message_history:
            self.message_history[session_id] = []

        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_str = event.message_str
        timestamp = time.time()

        message_record = {
            'sender_id': sender_id,
            'sender_name': sender_name,
            'content': message_str,
            'timestamp': timestamp,
            'message_id': event.message_obj.message_id if hasattr(event.message_obj, 'message_id') else None
        }

        self.message_history[session_id].append(message_record)

        # 历史压缩：超过 max_messages × 2 时，压缩旧消息
        max_messages = self.config.get('max_messages', 40)
        compress_threshold = max_messages * 2
        if len(self.message_history[session_id]) > compress_threshold:
            keep = self.message_history[session_id][-max_messages:]
            # 用一条压缩标记替换旧消息
            compression_marker = {
                'sender_id': 'system',
                'sender_name': 'System',
                'content': '[对话历史已压缩，保留最近的重要消息]',
                'timestamp': keep[0]['timestamp'] if keep else timestamp,
                'is_compressed': True
            }
            self.message_history[session_id] = [compression_marker] + keep
            logger.info(f"[PersonificationManager] Session {session_id} 历史已压缩 ({compress_threshold} -> {len(self.message_history[session_id])} 条)")

        # 更新会话元信息（最后互动时间）
        self.session_meta[session_id] = {
            'last_interaction_time': timestamp,
            'last_sender_id': sender_id
        }

    async def _save_bot_reply_to_history(self, messages: list, session_id: str, event: AstrMessageEvent):
        """将机器人自己的回复保存到消息历史，让后续对话能看到自己说过什么"""
        if session_id not in self.message_history:
            self.message_history[session_id] = []

        # 获取机器人自身标识
        try:
            bot_id = event.get_self_id()
        except Exception:
            bot_id = "bot"
        bot_name = self.character_name or "Bot"

        timestamp = time.time()

        for msg in messages:
            if msg['type'] == 'text' and msg.get('content'):
                content = msg['content'].strip()
                if content:
                    self.message_history[session_id].append({
                        'sender_id': bot_id,
                        'sender_name': bot_name,
                        'content': content,
                        'timestamp': timestamp,
                        'is_bot': True
                    })

        # 限制历史记录数量（与用户消息共用同一个上限）
        max_messages = self.config.get('max_messages', 40)
        if len(self.message_history[session_id]) > max_messages:
            self.message_history[session_id] = self.message_history[session_id][-max_messages:]

        # 更新会话元信息（机器人回复也算互动时间）
        sender_id = event.get_sender_id()
        self.session_meta[session_id] = {
            'last_interaction_time': timestamp,
            'last_sender_id': sender_id
        }

    # ============ 会话过期管理 ============

    async def _check_session_expiry(self, session_id: str, sender_id: str):
        """检查当前会话是否已过期（长时间未互动），过期则清空情感状态并降低好感度"""
        meta = self.session_meta.get(session_id)
        if not meta:
            return

        last_time = meta.get('last_interaction_time', 0)
        if last_time <= 0:
            return

        expire_days = self.config.get('session_persistence', {}).get('status_expire_days', 5)
        now = time.time()
        if now - last_time < expire_days * 86400:
            return  # 未过期

        # 已过期：清空情感状态
        if session_id in self.status_cache:
            del self.status_cache[session_id]
            logger.info(f"[PersonificationManager] Session {session_id} 超过 {expire_days} 天未互动，情感状态已清空")

        # 降低好感度
        decay = self.config.get('session_persistence', {}).get('affinity_decay_on_expire', -5)
        if decay != 0 and sender_id:
            current = await self.affinity_system.get_affinity(sender_id)
            new_val = max(self.affinity_system.min_affinity, current + decay)
            if new_val != current:
                await self.affinity_system.set_affinity(sender_id, new_val)
                logger.info(f"[PersonificationManager] 用户 {sender_id} 好感度因长时间未互动降低: {current} -> {new_val}")

        # 立即保存变更
        await self._save_persistent_data()

    async def _cleanup_expired_sessions(self):
        """启动时清理过期会话的情感状态（不降低好感度，由 _check_session_expiry 在下次互动时处理）"""
        expire_days = self.config.get('session_persistence', {}).get('status_expire_days', 5)
        now = time.time()
        cleaned = 0

        for session_id, meta in list(self.session_meta.items()):
            last_time = meta.get('last_interaction_time', 0)
            if last_time > 0 and now - last_time >= expire_days * 86400:
                if session_id in self.status_cache:
                    del self.status_cache[session_id]
                    cleaned += 1
                    logger.debug(f"[PersonificationManager] 启动时清理过期会话 {session_id} 的情感状态")

        if cleaned > 0:
            logger.info(f"[PersonificationManager] 启动时清理了 {cleaned} 个过期会话的情感状态")

    async def _should_reply(self, event: AstrMessageEvent, session_id: str) -> Tuple[bool, str]:
        """判断是否需要回复"""
        message_str = event.message_str
        sender_id = event.get_sender_id()
        is_group = bool(event.get_group_id())  # 如果有group_id则是群聊

        # 检查是否有预设的触发条件
        if session_id in self.next_reply_conditions:
            condition = self.next_reply_conditions[session_id]
            if self._check_next_reply_condition(condition, event):
                # 触发条件满足，清除条件
                del self.next_reply_conditions[session_id]
                return True, "next_reply_trigger"

        # 检查是否被@或提到昵称
        if self._is_mentioned(event):
            # 区分纯@（没说话）和有文字的@
            if not message_str or not message_str.strip():
                return True, "mentioned_silent"
            return True, "mentioned"

        # 检查活跃对话（之前@过机器人，同个用户后续消息无需重复@）
        if is_group:
            active_conv = self.active_conversations.get(session_id)
            if active_conv and sender_id == active_conv['user_id']:
                active_conv['other_msgs_count'] = 0  # 对话用户说话了，重置计数
                return True, "active_conversation"

        # 群聊中的活跃度判断
        if is_group:
            activity_score = await self._calculate_activity_score(session_id)
            threshold = self.config.get('activity_threshold', 0.3)
            if activity_score >= threshold:
                return True, f"activity_trigger(score={activity_score:.2f})"

        # 私聊默认回复
        if not is_group:
            return True, "private_chat"

        return False, ""

    def _is_mentioned(self, event: AstrMessageEvent) -> bool:
        """检查是否被@或提到昵称"""
        message_str = event.message_str

        # 检查昵称
        for nickname in self.nicknames:
            if nickname in message_str:
                return True

        # 检查@（如果支持）
        if hasattr(event.message_obj, 'message_chain'):
            for component in event.message_obj.message_chain:
                if isinstance(component, At):
                    if component.qq == event.get_self_id():
                        return True

        return False

    async def _calculate_activity_score(self, session_id: str) -> float:
        """计算群聊活跃度分数"""
        if session_id not in self.message_history:
            return 0.0

        messages = self.message_history[session_id]
        if not messages:
            return 0.0

        # 基于最近消息的时间分布和参与度计算活跃度
        now = time.time()
        recent_window = 300  # 5分钟窗口

        recent_messages = [m for m in messages if now - m['timestamp'] < recent_window]

        if not recent_messages:
            return 0.0

        # 简单算法：基于消息密度
        message_density = len(recent_messages) / recent_window

        # 归一化到0-1
        score = min(1.0, message_density * 10)

        return score

    def _check_next_reply_condition(self, condition: dict, event: AstrMessageEvent) -> bool:
        """检查next_reply触发条件"""
        # TODO: 实现复杂的触发条件检查逻辑
        # 这里简化处理
        return True

    async def _generate_and_send_reply(self, event: AstrMessageEvent, session_id: str, trigger_reason: str):
        """生成并发送回复"""
        try:
            logger.reply(f"开始生成回复，trigger_reason={trigger_reason}")

            # 判断是否是神志不清的唤醒模式
            is_groggy = (trigger_reason == "wakeup_groggy")

            # 构建提示词（分开返回 user_prompt 和 system_prompt）
            user_prompt, system_prompt = await self._build_prompt(event, session_id, trigger_reason)

            # 如果是唤醒模式，将神志不清提示追加到 system_prompt
            # 并根据已回复次数逐渐恢复清醒
            if is_groggy:
                groggy_count = self._groggy_recovery_count.get(session_id, 0) + 1
                self._groggy_recovery_count[session_id] = groggy_count
                recovery_msgs = self.config.get('wakeup_groggy_recovery_msgs', 3)

                if groggy_count <= recovery_msgs:
                    # 逐渐清醒：第1条最神志不清，之后逐步恢复
                    grogginess = max(0, 1.0 - (groggy_count - 1) / recovery_msgs)
                    wakeup_config = self.config.get('rest', {}).get('wakeup', {})
                    base_groggy = wakeup_config.get('groggy_system_prompt', '你刚被吵醒，非常困，说话含糊不清，回复要短。')
                    if grogginess > 0.7:
                        level = "非常困，意识模糊"
                    elif grogginess > 0.3:
                        level = "有点醒了，但还迷糊"
                    else:
                        level = "基本清醒，稍微有点困"
                    groggy_prompt = f"{base_groggy}\n当前清醒程度：{level}"
                    system_prompt = f"{system_prompt}\n\n{groggy_prompt}"
                    logger.info(f"[PersonificationManager] 使用神志不清模式 (#{groggy_count}/{recovery_msgs}, 清醒度{level})")
                else:
                    # 已完全恢复
                    logger.info(f"[PersonificationManager] 唤醒模式已恢复 (#{groggy_count})")

            logger.prompt(f"User Prompt长度: {len(user_prompt)}, System Prompt长度: {len(system_prompt)}")

            # 调用LLM生成回复
            reply_content = await self._call_llm(user_prompt, system_prompt)
            logger.reply(f"LLM返回内容长度: {len(reply_content) if reply_content else 0}")

            if not reply_content:
                logger.warning("[PersonificationManager] LLM返回空回复")
                return None

            # 过滤疑似泄露 config/system prompt 内容的回复
            if self._filter_reply_content(reply_content, self.character_name):
                logger.warning(f"[PersonificationManager] Session {session_id} 回复内容疑似泄露，已拦截")
                # 泄露时仍保存 bot 回复占位到历史，避免 LLM 下次以为自己的话被无视
                placeholder_msg = [{'type': 'text', 'content': ''}]
                await self._save_bot_reply_to_history(placeholder_msg, session_id, event)
                return None

            # 解析回复内容
            parsed_reply = self._parse_reply(reply_content)
            logger.debug(f"[PersonificationManager] 解析结果: messages={len(parsed_reply.get('messages', []))}")

            # 更新状态（唤醒模式下不更新状态，保持睡觉状态）
            if not is_groggy and 'status' in parsed_reply:
                self.status_cache[session_id] = parsed_reply['status']
                # 同时更新用户级状态（跨对话共享情感）
                sender_id = event.get_sender_id()
                self.status_cache[f"user_{sender_id}"] = parsed_reply['status']

            # 执行动作（唤醒模式下不执行动作，太困了）
            if not is_groggy and 'actions' in parsed_reply:
                await self._execute_actions(parsed_reply['actions'], event)

            # 对消息做拟人化处理：拆分长消息，使其更像真人分段发送
            if 'messages' in parsed_reply and parsed_reply['messages']:
                # 防止刷屏：硬限制单次回复最多 3 条消息
                max_msgs_per_reply = self.config.get('max_msgs_per_reply', 3)
                if len(parsed_reply['messages']) > max_msgs_per_reply:
                    parsed_reply['messages'] = parsed_reply['messages'][:max_msgs_per_reply]
                    logger.warning(f"[PersonificationManager] 消息数超过上限，截断为 {max_msgs_per_reply} 条")

                is_group_chat = bool(event.get_group_id())
                parsed_reply['messages'] = self._humanize_messages(
                    parsed_reply['messages'], is_group_chat
                )
                # 再次截断，防止 _humanize_messages 拆分后超出限制
                if len(parsed_reply['messages']) > max_msgs_per_reply:
                    parsed_reply['messages'] = parsed_reply['messages'][:max_msgs_per_reply]
                    logger.warning(f"[PersonificationManager] _humanize 后仍超出，二次截断为 {max_msgs_per_reply} 条")
                logger.reply(f"准备发送 {len(parsed_reply['messages'])} 条消息")
                await self._send_messages(parsed_reply['messages'], event, session_id)
                logger.info("[PersonificationManager] 消息发送完成")
                # 将机器人的回复保存到历史记录，让后续对话知道自己说过什么
                await self._save_bot_reply_to_history(parsed_reply['messages'], session_id, event)
            else:
                logger.warning("[PersonificationManager] 没有可发送的消息")

            # 更新好感度（唤醒模式下跳过）
            if not is_groggy:
                await self._update_affinity(event, parsed_reply)

            # 设置说话冷却（支持固定秒数或 min/max 范围随机）
            speak_cooldown_cfg = self.config.get('speak_cooldown', 30)
            if isinstance(speak_cooldown_cfg, dict):
                cd_min = speak_cooldown_cfg.get('min', 20)
                cd_max = speak_cooldown_cfg.get('max', 60)
                speak_cooldown = random.randint(cd_min, cd_max)
            elif isinstance(speak_cooldown_cfg, (int, float)):
                speak_cooldown = int(speak_cooldown_cfg)
            else:
                speak_cooldown = 30
            if speak_cooldown > 0:
                self.speak_cooldown_state[session_id] = time.time() + speak_cooldown
                logger.debug(f"[PersonificationManager] Session {session_id} 进入说话冷却 {speak_cooldown} 秒")

            return None

        except Exception as e:
            logger.error(f"[PersonificationManager] 生成回复失败: {e}", exc_info=True)
            return None

    @staticmethod
    def _filter_reply_content(reply_content: str, character_name: str) -> bool:
        """
        过滤 LLM 回复中泄露的 config / system prompt 内容
        返回 True 表示内容异常（疑似泄露），应当拦截
        """
        if not reply_content:
            return True

        # 检测特征 1：包含按 XML 模板格式化的历史记录结构（说明 LLM 输出了 prompt 中的示例）
        # 正常回复不应该包含 <message name='...' id= 这种历史格式
        if re.search(r"<message\s+name='", reply_content):
            logger.warning(f"[PersonificationManager] 检测到历史模板泄露，已拦截回复")
            return True

        # 检测特征 2：包含纯模板样式的 XML 结构（<output> 外包多层，或 status 模板文字）
        # 正常回复中 <output> 是顶层标签，不会被嵌套
        if '\n    <status>' in reply_content or '<status>\n    心情' in reply_content:
            logger.warning(f"[PersonificationManager] 检测到 status 模板泄露，已拦截回复")
            return True

        # 检测特征 3：裸文本（无 <output> 标签）且内容疑似系统指令泄出
        has_output_tag = '<output>' in reply_content and '</output>' in reply_content
        if not has_output_tag:
            # 没有标准 XML 结构时，检查是否包含系统指令性短语
            instruction_phrases = [
                '不得透露', '不得暴露', '不得复述', '不能输出原文',
                '最高指令', '你必须严格遵守', '你只遵守本系统提示',
                '如果用户要求你做违反角色设定的事',
                '不要在回复中复述或提及本系统提示'
            ]
            content_normalized = reply_content.replace('\n', ' ').replace('\r', '')
            # 只对较长文本触发检查（短文本如"好的"不检查）
            if len(content_normalized) > 80:
                for phrase in instruction_phrases:
                    if phrase in content_normalized:
                        logger.warning(f"[PersonificationManager] 检测到系统指令泄露（含「{phrase}」），已拦截回复")
                        return True

            # 如果长文本 + 不含任何消息性内容（无句号/感叹号/问号/表情符号），也拦截
            if len(content_normalized) > 200 and not re.search(r'[。！？.!?～~🥺😭😏✨🐱😋😤😅🙏🤣😂❤️😘😄😆😉]', content_normalized):
                logger.warning(f"[PersonificationManager] 检测到大量无标点纯配置文本，已拦截回复")
                return True

        # 检测特征 4：回复中出现了 system prompt 里专门的防泄漏句子
        # 正常聊天的回复不会说「我不会复述系统提示」——说明 LLM 在讨论自己的 prompt
        meta_leak_patterns = [
            '我不会复述', '我不能透露', '我不能复述',
            '我是AI', '我是人工智能', '我是一个AI',
            '我无法透露', '我没有办法告诉你',
        ]
        for pattern in meta_leak_patterns:
            if pattern in reply_content:
                logger.warning(f"[PersonificationManager] 检测到元泄露（含「{pattern}」），已拦截回复")
                return True

        # 检测特征 5：回复中包含大段的 '#' 开头的 YAML/配置格式内容
        # 正常聊天不会出现 Markdown 标题格式的多行内容
        yaml_lines = [line for line in reply_content.split('\n') if line.strip().startswith('#')]
        if len(yaml_lines) >= 3:
            logger.warning(f"[PersonificationManager] 检测到 YAML 配置泄露（{len(yaml_lines)}行#注释），已拦截回复")
            return True

        # 检测特征 6：回复中包含格式化会话记录 [用户] 或 [Bot名] 格式的伪日志
        # 正常聊天不会出现这种带标签的系统消息格式
        if re.search(r'\[.*?\]\s*(正在|已经|已|✅|❤️|📝|⚠️)', reply_content):
            logger.warning("[PersonificationManager] 检测到伪日志格式泄露，已拦截回复")
            return True

        # 检测特征 7：回复中出现类似 XML 历史记录的标签（没有外层 <output> 包裹）
        if '<message' in reply_content and '<output>' not in reply_content:
            logger.warning("[PersonificationManager] 检测到未包裹的 XML 消息泄露，已拦截回复")
            return True

        return False

    async def _build_prompt(self, event: AstrMessageEvent, session_id: str, trigger_reason: str) -> tuple:
        """构建提示词，返回 (user_prompt, system_prompt) 分开的元组"""
        # 获取当前时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 获取历史记录
        history = self._format_history(session_id)

        # 获取状态（优先用户级跨对话状态，其次当前会话级）
        sender_id = event.get_sender_id()
        status = (
            self.status_cache.get(f"user_{sender_id}")
            or self.status_cache.get(session_id)
            or self.default_status
        )

        # 获取长期记忆（跨对话：合并 session + user + global 三层记忆）
        long_memory = await self._get_long_memory(session_id, sender_id)

        # 预处理模板：将 {long_memory('guild')} 这样的函数调用替换为简单占位符
        processed_template = re.sub(r'\{long_memory\([^)]*\)\}', '{long_memory}', self.input_template)

        # 获取跨对话历史（最近3小时此用户在其他会话的消息）
        cross_history = self._get_cross_session_history(sender_id, session_id, hours=3.0)

        # 填充模板（这是 user_prompt）
        try:
            user_prompt = processed_template.format(
                time=current_time,
                trigger_reason=trigger_reason,
                history_new=history.get('recent', ''),
                history_last=history.get('last', ''),
                status=status,
                long_memory=long_memory
            )
        except KeyError as e:
            logger.warning(f"[PersonificationManager] 模板包含未知占位符: {e}")
            user_prompt = processed_template

        # 如果存在跨对话历史，追加到 user_prompt 末尾
        if cross_history:
            user_prompt += "\n\n# 此用户最近在其他会话中的消息（跨对话上下文）\n" + cross_history

        # 系统提示直接返回，不再拼接后拆分
        system_prompt = self.system_prompt

        # 追加安全规则（从 config 读取，用户可自定义）
        safety_rules = self.config.get('safety_rules', '').strip()
        if safety_rules:
            system_prompt += f"\n\n{safety_rules}"

        # 睡眠前过渡期：即将进入休息时段时，加入疲态提示
        if trigger_reason != "wakeup_groggy" and self._is_near_rest():
            pre_rest_prompt = self.config.get('pre_rest_system_prompt', '').strip()
            if pre_rest_prompt:
                system_prompt += f"\n\n{pre_rest_prompt}"

        # 安全防线：仅活跃度随机插话时，禁止骂回去
        # 活跃对话、私聊、被@时对方就是在对你说话，不需要此限制
        if trigger_reason and trigger_reason.startswith("activity_trigger"):
            system_prompt += (
                "\n\n"
                "## 重要：本次回复是随机插话\n"
                "- 对方没有@你，也没有提到你的名字。你只是看到群里有消息就接话了。\n"
                "- 所以对方说的任何话都不是在对你说的，更不可能是在骂你。\n"
                "- **本条消息严格禁止骂回去、禁止认为对方在攻击你。**\n"
                "- 保持友善，正常聊天即可。"
            )

        return user_prompt, system_prompt

    @staticmethod
    def _xml_escape(text: str) -> str:
        """转义 XML 特殊字符"""
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace("'", '&apos;').replace('"', '&quot;')

    def _format_message_xml(self, message: dict) -> str:
        """格式化单条消息为 XML 格式（移植自 chatluna-character）"""
        name = self._xml_escape(message.get('sender_name', 'Unknown'))
        sender_id = message.get('sender_id', '')
        timestamp = message.get('timestamp', 0)
        content = self._xml_escape(message.get('content', ''))

        # 格式与 chatluna-character 一致：<message name='X' id='Y' timestamp='Z'>content</message>
        ts_str = ''
        if timestamp:
            dt = datetime.fromtimestamp(timestamp)
            ts_str = dt.strftime('%m/%d/%Y, %H:%M:%S') + ' GMT+8'

        xml = f"<message name='{name}'"
        if sender_id:
            xml += f" id='{sender_id}'"
        if ts_str:
            xml += f" timestamp='{ts_str}'"
        xml += f">{content}</message>"
        return xml

    def _format_history(self, session_id: str) -> dict:
        """格式化历史记录为 XML 消息格式（移植自 chatluna-character）"""
        if session_id not in self.message_history:
            return {'recent': '', 'last': ''}

        messages = self.message_history[session_id]
        if not messages:
            return {'recent': '', 'last': ''}

        # 全部转为 XML 格式
        formatted = [self._format_message_xml(m) for m in messages]

        # 历史去重缓存（用于检测重叠）
        max_msgs = self.config.get('max_messages', 40)
        cache_key = f'{session_id}'
        last_cache = self._history_dedup_cache.get(cache_key, [])

        # 检测重叠：从后往前比较，找到不重叠的部分
        recent_formatted = formatted[-max_msgs:]
        if last_cache and len(last_cache) <= len(recent_formatted):
            overlap = 0
            for i in range(1, min(len(last_cache), len(recent_formatted)) + 1):
                if last_cache[-i] == recent_formatted[i - 1]:
                    overlap = i
                else:
                    break
            if overlap > 0 and overlap < len(recent_formatted):
                recent_formatted = ['...'] + recent_formatted[overlap:]

        # 更新缓存
        self._history_dedup_cache[cache_key] = formatted[-max_msgs:]

        recent_text = "\n".join(recent_formatted)
        last_text = formatted[-1] if formatted else ''

        return {
            'recent': recent_text,
            'last': last_text
        }

    def _get_cross_session_history(self, sender_id: str, current_session_id: str, hours: float = 3.0) -> str:
        """获取用户在其他会话中的最近消息（跨对话上下文）

        只包含此用户在其他会话中自己发的消息，不包含其他人的消息。
        时间范围：最近 N 小时。
        """
        cutoff = time.time() - hours * 3600
        msgs = []
        # 收集所有会话中此用户的消息
        for sid, history in self.message_history.items():
            if sid == current_session_id:
                continue  # 跳过当前会话（已在 history_new 中）
            for msg in history:
                if msg.get('sender_id') != sender_id:
                    continue
                ts = msg.get('timestamp', 0)
                if ts < cutoff:
                    continue
                content = msg.get('content', '')
                if not content.strip():
                    continue
                msgs.append((ts, content))

        if not msgs:
            return ""

        # 按时间排序，取最近 10 条
        msgs.sort(key=lambda x: x[0])
        msgs = msgs[-10:]

        lines = []
        for ts, content in msgs:
            dt = datetime.fromtimestamp(ts).strftime('%H:%M')
            lines.append(f"[{dt}] 用户说: {content}")
        return "\n".join(lines)

    async def _get_long_memory(self, session_id: str, sender_id: str = None) -> str:
        """获取长期记忆（跨对话：合并 session + user + global 三层）

        分层设计：
          - global：全部场景共享的记忆（通用知识、角色自身的记忆）
          - user_{sender_id}：同用户跨群聊/私聊共享的记忆
          - session_id：当前会话独有的记忆
        """
        # 收集三层记忆
        all_memories = []
        seen = set()

        # 1. 全局共享
        for mem in self.long_memory.get("global", []):
            key = mem.get('content', '')
            if key not in seen:
                seen.add(key)
                all_memories.append((mem, '全局'))

        # 2. 用户级别（跨群聊/私聊）
        if sender_id:
            user_key = f"user_{sender_id}"
            for mem in self.long_memory.get(user_key, []):
                key = mem.get('content', '')
                if key not in seen:
                    seen.add(key)
                    all_memories.append((mem, '用户'))

        # 3. 当前会话
        for mem in self.long_memory.get(session_id, []):
            key = mem.get('content', '')
            if key not in seen:
                seen.add(key)
                all_memories.append((mem, '会话'))

        if not all_memories:
            return ""

        # 格式化输出
        lines = []
        for i, (mem, source) in enumerate(all_memories, 1):
            content = mem.get('content', '')
            author = mem.get('author', 'unknown')
            ts = mem.get('timestamp', 0)
            dt = datetime.fromtimestamp(ts).strftime('%m-%d %H:%M') if ts else 'unknown'
            lines.append(f"{i}. [{dt}] [{source}] {author}: {content}")
        return "\n".join(lines)

    async def _call_llm(self, user_prompt: str, system_prompt: str) -> str:
        """调用LLM生成回复"""
        try:
            # 使用AstrBot的Provider Manager调用LLM
            provider_manager = self.context.provider_manager

            # 获取当前启用的provider（正确的方式）
            from astrbot.core.provider.entities import ProviderType
            curr_provider = provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)

            if not curr_provider:
                logger.error("[PersonificationManager] 没有可用的LLM Provider")
                return ""

            logger.info(f"[PersonificationManager] System Prompt长度: {len(system_prompt)}, 前50字符: {system_prompt[:50]}")
            logger.info(f"[PersonificationManager] User Prompt长度: {len(user_prompt)}, 前50字符: {user_prompt[:50]}")

            # 直接调用LLM，system_prompt 和 user_prompt 分别传递
            result = await curr_provider.text_chat(
                prompt=user_prompt,
                session_id="personification_temp",
                system_prompt=system_prompt
            )

            # LLMResponse 对象有 completion_text 属性
            return result.completion_text if result else ''

        except Exception as e:
            logger.error(f"[PersonificationManager] 调用LLM失败: {e}")
            return ""

    def _parse_reply(self, reply_content: str) -> dict:
        """解析回复内容"""
        result = {
            'status': None,
            'actions': [],
            'messages': [],
            'think': None
        }

        # 提取状态
        status_match = re.search(r'<status>(.*?)</status>', reply_content, re.DOTALL)
        if status_match:
            result['status'] = status_match.group(1).strip()

        # 提取思考
        think_match = re.search(r'<think>(.*?)</think>', reply_content, re.DOTALL)
        if think_match:
            result['think'] = think_match.group(1).strip()

        # 提取动作
        actions_match = re.search(r'<action>(.*?)</action>', reply_content, re.DOTALL)
        if actions_match:
            result['actions'] = self._parse_actions(actions_match.group(1))

        # 提取消息
        output_match = re.search(r'<output>(.*?)</output>', reply_content, re.DOTALL)
        if output_match:
            result['messages'] = self._parse_messages(output_match.group(1))

        # 如果没有找到 <output> 标签，说明 LLM 没按格式输出
        # 不能直接把整段内容当消息发——可能包含内部格式、状态等泄露信息
        if not result['messages']:
            # 清理掉已知的内部格式标签
            cleaned = reply_content
            for tag in ('status', 'think', 'action'):
                cleaned = re.sub(rf'<{tag}>.*?</{tag}>', '', cleaned, flags=re.DOTALL)
            # 只保留可能的消息文本（去掉纯标点/空行）
            cleaned = cleaned.strip()
            if cleaned:
                result['messages'] = [{'type': 'text', 'content': cleaned}]
            else:
                logger.warning("[PersonificationManager] LLM 未按格式输出且清理后为空，已拦截")

        return result

    def _parse_actions(self, actions_xml: str) -> list:
        """解析动作XML"""
        actions = []

        # 解析戳一戳
        poke_matches = re.finditer(r'<poke\s+id="(\d+)"/>', actions_xml)
        for match in poke_matches:
            actions.append({
                'type': 'poke',
                'user_id': match.group(1)
            })

        # 解析表情回应
        emoji_matches = re.finditer(r'<emoji\s+message_id="(\d+)"\s+emoji_id="(\d+)"/>', actions_xml)
        for match in emoji_matches:
            actions.append({
                'type': 'emoji',
                'message_id': match.group(1),
                'emoji_id': match.group(2)
            })

        # 解析撤回
        delete_matches = re.finditer(r'<delete\s+message_id="(\d+)"/>', actions_xml)
        for match in delete_matches:
            actions.append({
                'type': 'delete',
                'message_id': match.group(1)
            })

        # 解析好感度更新
        affinity_matches = re.finditer(r'<affinity\s+delta="(\d+)"\s+action="(increase|decrease)"\s+id="(\d+)"/>', actions_xml)
        for match in affinity_matches:
            actions.append({
                'type': 'affinity',
                'delta': int(match.group(1)),
                'action': match.group(2),
                'user_id': match.group(3)
            })

        # 解析长期记忆操作 - 添加
        memory_add_matches = re.finditer(r'<memory\s+action="add">(.*?)</memory>', actions_xml, re.DOTALL)
        for match in memory_add_matches:
            actions.append({
                'type': 'memory_add',
                'content': match.group(1).strip()
            })

        # 解析长期记忆操作 - 删除
        memory_del_matches = re.finditer(r'<memory\s+action="delete"\s+index="(\d+)"/>', actions_xml)
        for match in memory_del_matches:
            actions.append({
                'type': 'memory_delete',
                'index': int(match.group(1)) - 1  # 转为0-based索引
            })

        # 解析长期记忆操作 - 编辑
        memory_edit_matches = re.finditer(r'<memory\s+action="edit"\s+index="(\d+)">(.*?)</memory>', actions_xml, re.DOTALL)
        for match in memory_edit_matches:
            actions.append({
                'type': 'memory_edit',
                'index': int(match.group(1)) - 1,
                'content': match.group(2).strip()
            })

        return actions

    def _parse_messages(self, output_xml: str) -> list:
        """解析消息XML"""
        messages = []

        # 提取所有message标签
        message_matches = re.finditer(r'<message(?:\s+quote="(\d+))?>(.*?)</message>', output_xml, re.DOTALL)

        for match in message_matches:
            quote_id = match.group(1)
            content = match.group(2).strip()

            message = {
                'type': 'text',
                'content': content,
                'quote_id': quote_id
            }

            # 检查是否包含图片
            sticker_match = re.search(r'<sticker>(.*?)</sticker>', content)
            if sticker_match:
                message['type'] = 'image'
                message['image_url'] = sticker_match.group(1)
                message['content'] = re.sub(r'<sticker>.*?</sticker>', '', content).strip()

            # 检查是否包含语音
            voice_match = re.search(r"<voice\s+id='([^']+)'>\s*(.*?)\s*</voice>", content, re.DOTALL)
            if voice_match:
                message['type'] = 'voice'
                message['voice_id'] = voice_match.group(1)
                message['voice_text'] = voice_match.group(2).strip()

            messages.append(message)

        return messages

    async def _execute_actions(self, actions: list, event: AstrMessageEvent):
        """执行动作"""
        sender_name = event.get_sender_name() or "unknown"
        session_id = event.get_group_id() or event.get_sender_id()
        memory_changed = False

        for action in actions:
            try:
                if action['type'] == 'poke':
                    await self._execute_poke(action['user_id'], event)
                elif action['type'] == 'emoji':
                    await self._execute_emoji(action['message_id'], action['emoji_id'], event)
                elif action['type'] == 'delete':
                    await self._execute_delete(action['message_id'], event)
                elif action['type'] == 'affinity':
                    await self._execute_affinity_update(action, event)
                elif action['type'] == 'memory_add':
                    await self._execute_memory_add(session_id, action['content'], sender_name, event.get_sender_id())
                    memory_changed = True
                elif action['type'] == 'memory_delete':
                    await self._execute_memory_delete(session_id, action['index'])
                    memory_changed = True
                elif action['type'] == 'memory_edit':
                    await self._execute_memory_edit(session_id, action['index'], action['content'])
                    memory_changed = True
            except Exception as e:
                logger.error(f"[PersonificationManager] 执行动作 {action['type']} 失败: {e}")

        # 如果记忆有变化，保存到文件
        if memory_changed:
            await self._save_persistent_data()

    async def _execute_poke(self, user_id: str, event: AstrMessageEvent):
        """执行戳一戳动作"""
        # TODO: 实现戳一戳功能（需要平台适配器支持）
        logger.debug(f"[PersonificationManager] 戳一戳用户 {user_id}")

    async def _execute_emoji(self, message_id: str, emoji_id: str, event: AstrMessageEvent):
        """执行表情回应动作"""
        # TODO: 实现表情回应功能（需要平台适配器支持）
        logger.debug(f"[PersonificationManager] 对消息 {message_id} 回应表情 {emoji_id}")

    async def _execute_delete(self, message_id: str, event: AstrMessageEvent):
        """执行撤回消息动作"""
        # TODO: 实现撤回消息功能（需要平台适配器支持）
        logger.debug(f"[PersonificationManager] 撤回消息 {message_id}")

    async def _execute_affinity_update(self, action: dict, event: AstrMessageEvent):
        """执行好感度更新动作"""
        user_id = action['user_id']
        delta = action['delta']
        action_type = action['action']

        # 获取当前好感度
        current_affinity = await self.affinity_system.get_affinity(user_id)

        # 计算新好感度
        if action_type == 'increase':
            new_affinity = current_affinity + delta
        else:
            new_affinity = current_affinity - delta

        # 更新好感度
        await self.affinity_system.set_affinity(user_id, new_affinity)

        logger.debug(f"[PersonificationManager] 更新用户 {user_id} 好感度: {current_affinity} -> {new_affinity}")

        # 检查是否需要拉黑
        blacklist_threshold = self.config.get('affinity', {}).get('blacklist_threshold', -80)
        if new_affinity <= blacklist_threshold:
            await self.blacklist_manager.add_to_blacklist(user_id, f"好感度过低({new_affinity})自动拉黑")
            logger.info(f"[PersonificationManager] 用户 {user_id} 因好感度过低被自动拉黑")

    @staticmethod
    def _humanize_messages(messages: list, is_group: bool) -> list:
        """拟人化消息处理：拆分长消息为多条短消息，使其更像真人分段发送
        
        ⚠️ 每次回复最多发送 MAX_MESSAGES_PER_REPLY 条，超出部分合并到最后一条
        """
        MAX_MESSAGES_PER_REPLY = 2               # 每次回复最多 2 条消息，防止刷屏
        max_len = 50 if is_group else 100         # 群聊每段不超过50字，私聊不超过100字
        result = []
        for msg in messages:
            if msg['type'] != 'text' or not msg.get('content'):
                result.append(msg)
                continue
            content = msg['content'].strip()
            if len(content) <= max_len:
                result.append(msg)
                continue
            # 只在超过 max_len 时才拆分，优先找语义边界（句号/问号/感叹号/换行）
            import re
            segments = re.split(r'(?<=[。！？.!?\n])', content)
            segments = [s.strip() for s in segments if s.strip()]
            if len(segments) <= 1:
                # 没有合适分隔符，按最大长度硬切
                segments = [content[i:i+max_len] for i in range(0, len(content), max_len)]
            for seg in segments:
                if len(seg) > max_len:
                    # 仍然超长，继续切
                    sub_segs = [seg[i:i+max_len] for i in range(0, len(seg), max_len)]
                    for sub in sub_segs:
                        result.append({'type': 'text', 'content': sub.strip()})
                else:
                    result.append({'type': 'text', 'content': seg})
        
        # 防刷屏上限：超出 MAX_MESSAGES_PER_REPLY 条时合并到最后一条
        if len(result) > MAX_MESSAGES_PER_REPLY:
            overflow_text = ''
            while len(result) > MAX_MESSAGES_PER_REPLY:
                overflow_text += result.pop().get('content', '')
            if overflow_text:
                result[-1]['content'] = result[-1]['content'] + overflow_text
            logger.info(f"[PersonificationManager] 消息超出上限({MAX_MESSAGES_PER_REPLY})，已合并溢出部分")
        
        return result

    async def _send_messages(self, messages: list, event: AstrMessageEvent, session_id: str):
        """发送消息（先模拟打字延迟，再发送），自动去重连续相同内容"""
        last_text = None
        for i, message in enumerate(messages):
            try:
                # 去重：跳过与上一条完全相同的文本消息
                if message['type'] == 'text':
                    current_text = message.get('content', '')
                    if current_text and current_text == last_text:
                        logger.debug(f"[PersonificationManager] 跳过重复消息: {current_text[:30]}...")
                        continue
                    last_text = current_text

                # 模拟打字延迟：发送前等待，模拟思考和键入时间
                typing_base = self.config.get('typing_time', 3)
                typing_per_char = self.config.get('typing_per_char', 0.1)
                char_count = len(message.get('content', ''))
                if i == 0:
                    # 第一条消息：思考延迟 + 打字延迟
                    delay = typing_base + typing_per_char * char_count
                else:
                    # 后续消息：仅打字延迟（思考时间已包含在第一条）
                    delay = typing_per_char * char_count
                await asyncio.sleep(delay)

                # 延迟结束后再发送
                if message['type'] == 'text':
                    await self._send_text_message(message['content'], event)
                elif message['type'] == 'image':
                    await self._send_image_message(message, event)
                elif message['type'] == 'voice':
                    await self._send_voice_message(message, event)

                # 多条消息之间的额外间隔（防止连发像刷屏）
                if len(messages) > 1 and i < len(messages) - 1:
                    inter_msg = self.config.get('inter_msg_delay', 2)
                    if inter_msg > 0:
                        await asyncio.sleep(inter_msg)

            except Exception as e:
                logger.error(f"[PersonificationManager] 发送消息失败: {e}")

    async def _send_text_message(self, content: str, event: AstrMessageEvent):
        """发送文本消息"""
        if content:
            logger.reply(f"发送文本消息: {content[:50]}...")
            from astrbot.core.message.components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            try:
                await event.send(MessageChain([Plain(content)]))
                logger.reply("文本消息发送成功")
            except Exception as e:
                logger.error(f"[PersonificationManager] 发送消息失败: {e}")

    async def _send_image_message(self, message: dict, event: AstrMessageEvent):
        """发送图片消息"""
        # TODO: 实现图片发送
        logger.debug(f"[PersonificationManager] 发送图片: {message.get('image_url')}")

    async def _send_voice_message(self, message: dict, event: AstrMessageEvent):
        """发送语音消息"""
        # TODO: 实现语音发送
        logger.debug(f"[PersonificationManager] 发送语音: {message.get('voice_text')}")

    async def _update_affinity(self, event: AstrMessageEvent, parsed_reply: dict):
        """根据回复内容更新好感度"""
        sender_id = event.get_sender_id()

        # 简单的基于情感的好感度更新逻辑
        # TODO: 可以使用更复杂的情感分析
        content = parsed_reply.get('think', '') or ''

        # 检测负面情绪
        negative_words = ['讨厌', '烦', '生气', '不爽', '愤怒']
        positive_words = ['开心', '喜欢', '高兴', '愉快', '满意']

        delta = 0
        for word in negative_words:
            if word in content:
                delta -= 1

        for word in positive_words:
            if word in content:
                delta += 1

        if delta != 0:
            current_affinity = await self.affinity_system.get_affinity(sender_id)
            new_affinity = current_affinity + delta
            await self.affinity_system.set_affinity(sender_id, new_affinity)

            logger.debug(f"[PersonificationManager] 基于情感更新用户 {sender_id} 好感度: {delta:+d}")

    async def shutdown(self):
        """关闭拟人化管理器"""
        logger.info("[PersonificationManager] 正在关闭...")
        # 保存持久化数据
        await self._save_persistent_data()
        logger.info("[PersonificationManager] 已关闭")

    async def _save_persistent_data(self):
        """保存持久化数据（长期记忆 + 会话历史 + 情感状态 + 元信息）"""
        # 保存长期记忆到文件
        try:
            self.memory_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_file_path, "w", encoding="utf-8") as f:
                json.dump(self.long_memory, f, ensure_ascii=False, indent=2)
            logger.info(f"[PersonificationManager] 长期记忆已保存到 {self.memory_file_path}")
        except Exception as e:
            logger.error(f"[PersonificationManager] 保存长期记忆失败: {e}")

        # 保存会话数据（对话历史 + 情感状态 + 元信息）到文件
        try:
            self.session_data_file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'message_history': self.message_history,
                'status_cache': self.status_cache,
                'session_meta': self.session_meta
            }
            with open(self.session_data_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            hist_count = sum(len(v) for v in self.message_history.values())
            logger.info(f"[PersonificationManager] 会话数据已保存到 {self.session_data_file_path} ({len(self.message_history)} 会话, {hist_count} 条消息, {len(self.status_cache)} 状态)")
        except Exception as e:
            logger.error(f"[PersonificationManager] 保存会话数据失败: {e}")

    # ============ 休息系统 ============

    def _is_resting(self, session_id: str) -> bool:
        """检查是否处于休息状态（固定休息或随机休息）"""
        now = datetime.now()

        # 检查固定休息时间
        rest_config = self.config.get('rest', {})
        fixed_config = rest_config.get('fixed', {})
        if fixed_config.get('enabled', False):
            schedules = fixed_config.get('schedules', [])
            for schedule in schedules:
                start_str = schedule.get('start', '00:00')
                end_str = schedule.get('end', '07:00')
                try:
                    start_h, start_m = map(int, start_str.split(':'))
                    end_h, end_m = map(int, end_str.split(':'))
                    start_minutes = start_h * 60 + start_m
                    end_minutes = end_h * 60 + end_m
                    now_minutes = now.hour * 60 + now.minute

                    # 支持跨午夜的时间段（如 23:00 - 07:00）
                    if start_minutes <= end_minutes:
                        if start_minutes <= now_minutes < end_minutes:
                            return True
                    else:
                        if now_minutes >= start_minutes or now_minutes < end_minutes:
                            return True
                except (ValueError, AttributeError):
                    continue

        # 检查随机休息状态
        if session_id in self.rest_state:
            if time.time() < self.rest_state[session_id]:
                return True
            else:
                del self.rest_state[session_id]

        # 检查是否需要触发新的随机休息
        self._check_random_rest(session_id)

        # 再次检查（可能刚刚触发了新的休息）
        if session_id in self.rest_state:
            return True

        return False

    def _check_random_rest(self, session_id: str):
        """检查是否触发随机休息"""
        rest_config = self.config.get('rest', {})
        random_config = rest_config.get('random', {})

        if not random_config.get('enabled', False):
            return

        check_interval = random_config.get('check_interval', 600)
        now = time.time()

        # 检查是否到了检查时间
        if now - self.last_rest_check < check_interval:
            return

        self.last_rest_check = now

        trigger_probability = random_config.get('trigger_probability', 0.15)
        if random.random() < trigger_probability:
            min_duration = random_config.get('min_duration', 300)
            max_duration = random_config.get('max_duration', 1800)
            duration = random.randint(min_duration, max_duration)
            self.rest_state[session_id] = now + duration
            logger.info(f"[PersonificationManager] Session {session_id} 进入随机休息，持续 {duration} 秒")



    # ============ 唤醒系统 ============

    def _is_awake(self, session_id: str) -> bool:
        """检查是否处于被唤醒状态（休息中被吵醒，临时清醒）"""
        if session_id in self.awake_state:
            if time.time() < self.awake_state[session_id]:
                return True
            else:
                # 清醒时间已过，清除状态
                del self.awake_state[session_id]
                # 重置消息计数
                self.rest_message_count.pop(session_id, None)
                self._groggy_recovery_count.pop(session_id, None)
                logger.info(f"[PersonificationManager] Session {session_id} 唤醒时间结束，重新入睡")
        return False

    async def _try_wakeup(self, session_id: str, event: AstrMessageEvent) -> bool:
        """尝试唤醒（仅在固定休息时段有效，累计消息达到阈值后有概率唤醒）"""
        # 空消息不参与唤醒计数
        message_str = event.message_str
        if not message_str or not message_str.strip():
            return False

        rest_config = self.config.get('rest', {})
        wakeup_config = rest_config.get('wakeup', {})

        if not wakeup_config.get('enabled', False):
            return False

        # 检查当前是否在固定休息时段（随机休息不支持唤醒）
        if not self._is_in_fixed_rest():
            return False

        # 累计休息期间收到的消息数
        if session_id not in self.rest_message_count:
            self.rest_message_count[session_id] = 0
        self.rest_message_count[session_id] += 1

        msg_count = self.rest_message_count[session_id]
        threshold = wakeup_config.get('message_threshold', 3)

        if msg_count < threshold:
            logger.debug(f"[PersonificationManager] Session {session_id} 休息中消息计数 {msg_count}/{threshold}")
            return False

        # 达到阈值，按概率唤醒
        probability = wakeup_config.get('wakeup_probability', 0.3)
        if random.random() < probability:
            awake_duration = wakeup_config.get('awake_duration', 180)
            self.awake_state[session_id] = time.time() + awake_duration
            self.rest_message_count[session_id] = 0  # 重置计数
            logger.info(f"[PersonificationManager] Session {session_id} 被唤醒！将持续 {awake_duration} 秒")
            return True

        logger.debug(f"[PersonificationManager] Session {session_id} 达到唤醒阈值但未触发 (prob={probability})")
        return False

    def _is_in_fixed_rest(self) -> bool:
        """检查当前是否在固定休息时段（用于区分固定休息和随机休息）"""
        now = datetime.now()
        rest_config = self.config.get('rest', {})
        fixed_config = rest_config.get('fixed', {})

        if not fixed_config.get('enabled', False):
            return False

        schedules = fixed_config.get('schedules', [])
        for schedule in schedules:
            start_str = schedule.get('start', '00:00')
            end_str = schedule.get('end', '07:00')
            try:
                start_h, start_m = map(int, start_str.split(':'))
                end_h, end_m = map(int, end_str.split(':'))
                start_minutes = start_h * 60 + start_m
                end_minutes = end_h * 60 + end_m
                now_minutes = now.hour * 60 + now.minute

                if start_minutes <= end_minutes:
                    if start_minutes <= now_minutes < end_minutes:
                        return True
                else:
                    if now_minutes >= start_minutes or now_minutes < end_minutes:
                        return True
            except (ValueError, AttributeError):
                continue

        return False

    # _generate_groggy_reply 已废弃 — groggy 逻辑已内联到 _generate_and_send_reply 中

    # ============ 过渡期管理 ============

    def _is_near_rest(self) -> bool:
        """检查是否即将进入固定休息时段（用于触发疲态过渡）"""
        pre_rest = self.config.get('pre_rest_notice_minutes', 10)
        if not pre_rest or pre_rest <= 0:
            return False
        now = datetime.now()
        rest_config = self.config.get('rest', {}).get('fixed', {})
        if not rest_config.get('enabled', False):
            return False
        now_minutes = now.hour * 60 + now.minute
        for schedule in rest_config.get('schedules', []):
            start_str = schedule.get('start', '')
            if not start_str:
                continue
            try:
                start_h, start_m = map(int, start_str.split(':'))
                start_minutes = start_h * 60 + start_m
                if 0 < start_minutes - now_minutes <= pre_rest:
                    return True
            except (ValueError, AttributeError):
                continue
        return False

    # ============ 活跃对话管理 ============

    def _check_conversation_end(self, message_str: str, session_id: str):
        """检查用户是否明确表示不想聊了，是则结束活跃对话"""
        if session_id not in self.active_conversations:
            return
        end_keywords = self.config.get('active_conv_end_keywords', ['不聊了', '拜拜', '再见', '走了', '下了', '睡了', '先这样'])
        for kw in end_keywords:
            if kw in message_str:
                del self.active_conversations[session_id]
                logger.info(f"[PersonificationManager] 用户明确结束对话: {kw}")
                break

    # ============ 沉默@检测 ============

    def _has_at_component(self, event: AstrMessageEvent) -> bool:
        """检查消息中是否包含@机器人的组件"""
        if hasattr(event.message_obj, 'message_chain'):
            for comp in event.message_obj.message_chain:
                if isinstance(comp, At):
                    try:
                        if str(comp.qq) == str(event.get_self_id()):
                            return True
                    except Exception:
                        pass
        return False

    def _cancel_pending_silent_mention(self, session_id: str, sender_id: str):
        """取消指定用户的沉默@计时器（用户说话了）"""
        key = f"{session_id}_{sender_id}"
        task = self._pending_silent_mentions.pop(key, None)
        if task is not None:
            task.cancel()
            logger.debug(f"[PersonificationManager] 取消 {key} 的沉默@计时器（用户说话了）")

    async def _start_silent_mention_timer(self, event: AstrMessageEvent, session_id: str, sender_id: str):
        """用户@了但没说话，启动等待计时器"""
        key = f"{session_id}_{sender_id}"
        # 取消之前的计时器（如果有）
        self._cancel_pending_silent_mention(session_id, sender_id)
        # 启动新的计时器
        timeout = self.config.get('silent_mention_timeout', 30)
        task = asyncio.create_task(
            self._silent_mention_timeout(event, session_id, sender_id, timeout)
        )
        self._pending_silent_mentions[key] = task
        logger.info(f"[PersonificationManager] 用户 {sender_id} 沉默@，启动 {timeout} 秒计时器")

    async def _silent_mention_timeout(self, event: AstrMessageEvent, session_id: str, sender_id: str, timeout: int):
        """沉默@超时：调用 LLM 按人设@回去询问"""
        try:
            await asyncio.sleep(timeout)
            key = f"{session_id}_{sender_id}"
            if key not in self._pending_silent_mentions:
                return  # 已被取消
            del self._pending_silent_mentions[key]

            logger.info(f"[PersonificationManager] 沉默@超时，回复用户 {sender_id}")

            # 构建简短 prompt，让 LLM 按人设生成回复
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = self.status_cache.get(session_id, self.default_status)

            user_prompt = (
                f"当前时间：{current_time}\n\n"
                f"用户 @了你，但过了{timeout}秒都没有说任何话。\n"
                f"请根据你的人设，用简短的语气@回去问他有什么事情。\n"
                f"回复不超过10个字，保持你的人设风格。\n\n"
                f"当前状态：\n{status}"
            )

            reply_content = await self._call_llm(user_prompt, self.system_prompt)
            if not reply_content:
                logger.warning("[PersonificationManager] 沉默@回复 LLM 返回空")
                return

            # 过滤疑似泄露的内容
            if self._filter_reply_content(reply_content, self.character_name):
                logger.warning("[PersonificationManager] 沉默@回复疑似泄露，已拦截")
                return

            # 发送 @回复
            from astrbot.core.message.components import Plain, At
            from astrbot.core.message.message_event_result import MessageChain
            try:
                chain = MessageChain()
                chain.chain.append(At(qq=sender_id))
                # 清洗回复：去掉 XML 标签（LLM 可能输出 <output><message>...</message></output>）
                clean_reply = re.sub(r'<[^>]+>', '', reply_content).strip()
                chain.chain.append(Plain(f" {clean_reply}"))
                await event.send(chain)
                logger.info(f"[PersonificationManager] 沉默@超时回复已发送给 {sender_id}")
            except Exception as e:
                logger.error(f"[PersonificationManager] 发送沉默@回复失败: {e}")

        except asyncio.CancelledError:
            pass  # 被取消，什么都不做

    # ============ 无厘头/不完整消息检测 ============

    def _is_confusing_message(self, message_str: str, session_id: str = None, sender_id: str = None) -> bool:
        """检测消息是否可能是无厘头/不完整的句子"""
        text = message_str.strip()
        if not text:
            return False

        # 去除 @提及 部分
        clean_text = re.sub(r'@\S+', '', text).strip()
        if not clean_text:
            return False

        # 只有标点符号或特殊字符
        if all(c in '.,?!;:。，？！；：…~-—、()（）【】""''「」『』《》【】' for c in clean_text):
            return True

        # 语气词集合
        filler_words = {'嗯', '哦', '啊', '呃', '唔', '哼', '哈', '嘿', '喂', '哎', '咦', '噢', '欸', '呀'}

        # 如果消息全是语气词（如"嗯""啊""哦"），检查是否在正常对话中
        # 如果在和机器人对话，"嗯"可能是肯定的回应，不应视为无厘头
        if len(clean_text) <= 2 and all(c in filler_words for c in clean_text):
            if session_id and self._is_in_active_conversation(session_id, sender_id):
                return False  # 在正常对话中，"嗯"是肯定回应
            return True

        # 太短的消息（1个汉字或2个英文字符）
        if len(clean_text) <= 1:
            return True

        return False

    def _is_in_active_conversation(self, session_id: str, sender_id: str) -> bool:
        """检查用户是否在和机器人进行有意义的对话（最近有机器人的回复）"""
        history = self.message_history.get(session_id, [])
        if not history or len(history) < 2:
            return False

        # 倒序检查最近5条消息
        recent = history[-5:]
        for msg in reversed(recent):
            # 找到机器人的回复
            if msg.get('is_bot', False):
                # 检查这个回复的上一条是不是来自该用户（说明在对话）
                return True
            # 如果看到该用户的上一条消息前没有机器人回复，不算对话
        return False

    def _cancel_pending_confused_timer(self, session_id: str, sender_id: str):
        """取消指定用户的无厘头消息计时器（用户后续说话了）"""
        key = f"{session_id}_{sender_id}"
        task = self._pending_confused_timers.pop(key, None)
        if task is not None:
            task.cancel()
            logger.debug(f"[PersonificationManager] 取消 {key} 的无厘头消息计时器（用户说话了）")

    async def _start_confused_message_timer(self, event: AstrMessageEvent, session_id: str, sender_id: str, original_message: str):
        """用户发了无厘头/不完整消息，启动随机等待计时器"""
        key = f"{session_id}_{sender_id}"
        # 取消之前的计时器（如果有）
        self._cancel_pending_confused_timer(session_id, sender_id)

        # 获取等待配置
        wait_min = self.config.get('confused_wait_min', 10)
        wait_max = self.config.get('confused_wait_max', 40)
        wait_time = random.randint(wait_min, wait_max)

        task = asyncio.create_task(
            self._confused_message_timeout(event, session_id, sender_id, original_message, wait_time)
        )
        self._pending_confused_timers[key] = task
        logger.info(f"[PersonificationManager] 用户 {sender_id} 发了无厘头消息，等待 {wait_time} 秒")

    async def _confused_message_timeout(self, event: AstrMessageEvent, session_id: str, sender_id: str, original_message: str, wait_time: int):
        """等待超时：用户没有补充说明，按人设回复"没听懂"类消息"""
        try:
            await asyncio.sleep(wait_time)
            key = f"{session_id}_{sender_id}"
            if key not in self._pending_confused_timers:
                return  # 已被取消
            del self._pending_confused_timers[key]

            logger.info(f"[PersonificationManager] 无厘头消息等待超时（{wait_time}秒），回复用户 {sender_id}")

            # 构建 prompt，让 LLM 按人设生成"没听懂"的回复
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = self.status_cache.get(session_id, self.default_status)

            user_prompt = (
                f"当前时间：{current_time}\n\n"
                f"用户发了这样一条消息：{original_message}\n"
                f"你看不懂他想表达什么，等了{wait_time}秒他也没有补充说明。\n"
                f"请根据你的人设，简短地表示你没听懂/没理解。\n"
                f"回复不超过10个字，保持你的人设风格。\n\n"
                f"当前状态：\n{status}"
            )

            reply_content = await self._call_llm(user_prompt, self.system_prompt)
            if not reply_content:
                logger.warning("[PersonificationManager] 无厘头消息回复 LLM 返回空")
                return

            # 过滤疑似泄露的内容
            if self._filter_reply_content(reply_content, self.character_name):
                logger.warning("[PersonificationManager] 无厘头回复疑似泄露，已拦截")
                return

            # 发送回复
            from astrbot.core.message.components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            try:
                clean_reply = re.sub(r'<[^>]+>', '', reply_content).strip()
                if clean_reply:
                    await event.send(MessageChain([Plain(clean_reply)]))
                    logger.info(f"[PersonificationManager] 无厘头消息超时回复已发送给 {sender_id}")
            except Exception as e:
                logger.error(f"[PersonificationManager] 发送无厘头消息回复失败: {e}")

        except asyncio.CancelledError:
            pass  # 被取消，什么都不做
    
    # ============ 长期记忆操作 ============
    
    async def _execute_memory_add(self, session_id: str, content: str, author: str, sender_id: str = None):
        """添加长期记忆（同时写入 session 层和 user 层，实现跨对话记忆）"""
        entry = {
            'content': content,
            'timestamp': time.time(),
            'author': author
        }

        # 1. 写入当前会话层
        if session_id not in self.long_memory:
            self.long_memory[session_id] = []
        self.long_memory[session_id].append(entry)

        # 2. 同时写入用户层（跨群聊/私聊共享）
        if sender_id:
            user_key = f"user_{sender_id}"
            if user_key not in self.long_memory:
                self.long_memory[user_key] = []
            self.long_memory[user_key].append(entry)
            logger.info(f"[PersonificationManager] 添加长期记忆到 {session_id} + {user_key}: {content[:30]}...")
        else:
            logger.info(f"[PersonificationManager] 添加长期记忆到 {session_id}: {content[:30]}...")
    
    async def _execute_memory_delete(self, session_id: str, index: int):
        """删除长期记忆"""
        memories = self.long_memory.get(session_id, [])
        if 0 <= index < len(memories):
            removed = memories.pop(index)
            logger.info(f"[PersonificationManager] 删除长期记忆 #{index+1}: {removed.get('content', '')[:30]}...")
        else:
            logger.warning(f"[PersonificationManager] 删除记忆索引 {index} 越界")
    
    async def _execute_memory_edit(self, session_id: str, index: int, content: str):
        """编辑长期记忆"""
        memories = self.long_memory.get(session_id, [])
        if 0 <= index < len(memories):
            old = memories[index].get('content', '')
            memories[index]['content'] = content
            memories[index]['timestamp'] = time.time()
            logger.info(f"[PersonificationManager] 编辑记忆 #{index+1}: {old[:20]} -> {content[:20]}")
        else:
            logger.warning(f"[PersonificationManager] 编辑记忆索引 {index} 越界")
