"""
拟人化管理器 - 核心聊天逻辑
"""
import asyncio
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Plain, Image, At


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
    
    async def initialize(self):
        """初始化拟人化管理器"""
        logger.info("[PersonificationManager] 正在初始化...")
        # 可以从数据库加载历史数据
        await self._load_persistent_data()
        logger.info("[PersonificationManager] 初始化完成")
    
    async def _load_persistent_data(self):
        """加载持久化数据"""
        # TODO: 从数据库加载历史消息、状态等
        pass
    
    async def handle_message(self, event: AstrMessageEvent):
        """处理收到的消息"""
        try:
            sender_id = event.get_sender_id()
            is_group = bool(event.get_group_id())  # 如果有group_id则是群聊
            session_id = event.get_group_id() if is_group else sender_id
            
            # 检查是否处于闭嘴状态
            if self._is_muted(session_id):
                logger.debug(f"[PersonificationManager] Session {session_id} 处于闭嘴状态")
                return
            
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
            
            # 阻止AstrBot的默认LLM回复
            event.stop_event()
            # 设置标志，确保后续阶段也不会处理
            event.set_extra("astrbot_personification_handled", True)
            
            # 生成回复
            await self._generate_and_send_reply(event, session_id, trigger_reason)
            
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
    
    async def _save_message_to_history(self, event: AstrMessageEvent, session_id: str):
        """保存消息到历史记录"""
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
        
        # 限制历史记录数量
        max_messages = self.config.get('max_messages', 40)
        if len(self.message_history[session_id]) > max_messages:
            self.message_history[session_id] = self.message_history[session_id][-max_messages:]
    
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
            return True, "mentioned"
        
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
            logger.info(f"[PersonificationManager] 开始生成回复，trigger_reason={trigger_reason}")
            
            # 构建提示词
            prompt = await self._build_prompt(event, session_id, trigger_reason)
            logger.debug(f"[PersonificationManager] 提示词长度: {len(prompt)}")
            
            # 调用LLM生成回复
            reply_content = await self._call_llm(prompt)
            logger.info(f"[PersonificationManager] LLM返回内容长度: {len(reply_content) if reply_content else 0}")
            
            if not reply_content:
                logger.warning("[PersonificationManager] LLM返回空回复")
                return None
            
            # 解析回复内容
            parsed_reply = self._parse_reply(reply_content)
            logger.debug(f"[PersonificationManager] 解析结果: messages={len(parsed_reply.get('messages', []))}")
            
            # 更新状态
            if 'status' in parsed_reply:
                self.status_cache[session_id] = parsed_reply['status']
            
            # 执行动作
            if 'actions' in parsed_reply:
                await self._execute_actions(parsed_reply['actions'], event)
            
            # 发送消息
            if 'messages' in parsed_reply and parsed_reply['messages']:
                logger.info(f"[PersonificationManager] 准备发送 {len(parsed_reply['messages'])} 条消息")
                await self._send_messages(parsed_reply['messages'], event, session_id)
                logger.info("[PersonificationManager] 消息发送完成")
            else:
                logger.warning("[PersonificationManager] 没有可发送的消息")
            
            # 更新好感度
            await self._update_affinity(event, parsed_reply)
            
            return None
            
        except Exception as e:
            logger.error(f"[PersonificationManager] 生成回复失败: {e}", exc_info=True)
            return None
    
    async def _build_prompt(self, event: AstrMessageEvent, session_id: str, trigger_reason: str) -> str:
        """构建提示词"""
        # 获取当前时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取历史记录
        history = self._format_history(session_id)
        
        # 获取状态
        status = self.status_cache.get(session_id, self.default_status)
        
        # 获取长期记忆（如果有）
        long_memory = await self._get_long_memory(session_id)
        
        # 填充模板
        prompt = self.input_template.format(
            time=current_time,
            trigger_reason=trigger_reason,
            history_new=history.get('recent', ''),
            history_last=history.get('last', ''),
            status=status,
            long_memory=long_memory
        )
        
        # 添加系统提示
        full_prompt = f"{self.system_prompt}\n\n{prompt}"
        
        return full_prompt
    
    def _format_history(self, session_id: str) -> dict:
        """格式化历史记录"""
        if session_id not in self.message_history:
            return {'recent': '', 'last': ''}
        
        messages = self.message_history[session_id]
        
        if not messages:
            return {'recent': '', 'last': ''}
        
        # 最近的消息
        recent_messages = messages[-10:] if len(messages) > 10 else messages
        recent_text = "\n".join([
            f"{m['sender_name']}: {m['content']}" 
            for m in recent_messages
        ])
        
        # 最后一条消息
        last_message = messages[-1]
        last_text = f"{last_message['sender_name']}: {last_message['content']}"
        
        return {
            'recent': recent_text,
            'last': last_text
        }
    
    async def _get_long_memory(self, session_id: str) -> str:
        """获取长期记忆"""
        # TODO: 从数据库或向量存储中获取长期记忆
        return ""
    
    async def _call_llm(self, prompt: str) -> str:
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
            
            # 分离 system_prompt 和 user_prompt
            # prompt 格式为: "{system_prompt}\n\n{user_prompt}"
            parts = prompt.split("\n\n", 1)
            system_prompt = parts[0] if len(parts) > 1 else ""
            user_prompt = parts[1] if len(parts) > 1 else prompt
            
            logger.info(f"[PersonificationManager] System Prompt长度: {len(system_prompt)}, 前50字符: {system_prompt[:50]}")
            logger.info(f"[PersonificationManager] User Prompt长度: {len(user_prompt)}, 前50字符: {user_prompt[:50]}")
            
            # 调用LLM，传递 system_prompt
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
        
        # 如果没有找到标签，将整个内容作为消息
        if not result['messages']:
            result['messages'] = [{'type': 'text', 'content': reply_content.strip()}]
        
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
            except Exception as e:
                logger.error(f"[PersonificationManager] 执行动作 {action['type']} 失败: {e}")
    
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
    
    async def _send_messages(self, messages: list, event: AstrMessageEvent, session_id: str):
        """发送消息"""
        for message in messages:
            try:
                if message['type'] == 'text':
                    await self._send_text_message(message['content'], event)
                elif message['type'] == 'image':
                    await self._send_image_message(message, event)
                elif message['type'] == 'voice':
                    await self._send_voice_message(message, event)
                
                # 模拟打字延迟
                typing_time = self.config.get('typing_time', 1)
                await asyncio.sleep(typing_time)
                
            except Exception as e:
                logger.error(f"[PersonificationManager] 发送消息失败: {e}")
    
    async def _send_text_message(self, content: str, event: AstrMessageEvent):
        """发送文本消息"""
        if content:
            logger.info(f"[PersonificationManager] 发送文本消息: {content[:50]}...")
            from astrbot.core.message.components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            try:
                await event.send(MessageChain([Plain(content)]))
                logger.info("[PersonificationManager] 文本消息发送成功")
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
        """保存持久化数据"""
        # TODO: 保存状态、历史记录等到数据库
        pass
