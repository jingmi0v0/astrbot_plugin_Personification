"""聊天管线完整版 — 对应 chatluna-character src/plugins/chat.ts"""
import time
import re
from datetime import datetime
from collections import defaultdict

from .response_parser import parse_response


def escape_xml(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_timestamp(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return dt.strftime('%m/%d/%Y, %H:%M:%S GMT+8')


def format_message(msg: dict, enable_id: bool = False) -> str:
    """将消息格式化为 XML — 对应 formatMessageString"""
    name = escape_xml(msg.get('sender_name', msg.get('name', '?')))
    sender_id = msg.get('sender_id', msg.get('id', ''))
    content = msg.get('content', '')
    ts = msg.get('timestamp', 0)
    message_id = msg.get('message_id', msg.get('messageId', ''))

    xml = f"<message name='{name}'"
    if sender_id:
        xml += f" id='{sender_id}'"
    if enable_id and message_id:
        xml += f" messageId='{message_id}'"
    if ts:
        xml += f" timestamp='{format_timestamp(ts)}'"
    xml += f">{escape_xml(content)}</message>"
    return xml


class ChatPipeline:
    """完整聊天管线 — 对应 chatluna's chat.ts apply()

    功能：
    - 消息格式化（XML 格式，与 chatluna 一致）
    - Prompt 构建（input 模板 + 历史消息）
    - LLM 调用
    - 回复解析（XML → actions / messages / status）
    - 消息发送（逐条 + 打字延迟）
    - 多轮上下文管理（completion_messages）
    """

    def __init__(self, context, config: dict, preset):
        self.context = context
        self.config = config
        self.preset = preset

        # 状态缓存
        self.status_cache: dict[str, str] = {}

        # 历史上下文（多轮 completion messages 追踪）
        self.completion_messages: dict[str, list] = defaultdict(list)

        # 长期记忆
        self.long_memory: dict[str, list] = defaultdict(list)

    # ════════════════════════════════════════
    # 主入口：接收消息 → 生成回复
    # ════════════════════════════════════════

    async def process(self, event, session_key: str, history_messages: list,
                      trigger_reason: str, sender_id: str = None,
                      is_groggy: bool = False,
                      affinity_value: int = None) -> dict | None:
        """处理一条消息，返回解析后的回复"""
        # 1. 构建 prompt
        user_prompt, system_prompt = self._build_prompt(
            session_key, history_messages, trigger_reason,
            sender_id, is_groggy
        )

        # 1.5 在 prompt 后追加好感度和 MASTER（不在 _build_prompt 内避免变量作用域问题）
        if affinity_value is not None:
            system_prompt += f"\n\n当前与你对话的用户好感度：{affinity_value}（正数表示友好，负数表示讨厌）"
        master_id = self.config.get('MASTERID', '')
        if master_id:
            system_prompt += f"\n\n主人的QQ号是{master_id}，如果主人来找你，你要特别听话"

        # 2. 调用 LLM
        reply = await self._call_llm(user_prompt, system_prompt)
        if not reply:
            return None

        # 3. 解析回复
        parsed = parse_response(reply)

        # 4. 更新状态
        if parsed.get('status'):
            self.status_cache[session_key] = parsed['status']
            if sender_id:
                self.status_cache[f"user_{sender_id}"] = parsed['status']

        return parsed

    # ════════════════════════════════════════
    # Prompt 构建（对应 chat.ts 的 buildPrompt + formatMessage）
    # ════════════════════════════════════════

    def _build_prompt(self, session_key: str, history_msgs: list,
                      trigger_reason: str, sender_id: str = None,
                      is_groggy: bool = False) -> tuple:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = self.status_cache.get(session_key) or self.preset.status or ""

        # 格式化历史消息为 XML（与 chatluna 格式一致）
        history_xml = '\n'.join(
            format_message(m) for m in history_msgs[-30:]
        ) if history_msgs else ''

        # user_prompt — 使用预设模板
        template = self.preset.input_template
        try:
            user_prompt = template.format(
                time=now,
                trigger_reason=trigger_reason,
                history_new=history_xml,
                history_last=history_xml.split('\n')[-1] if history_xml else '',
                status=status,
            )
        except KeyError:
            user_prompt = template

        system_prompt = self.preset.system_prompt

        # 睡眠前疲态
        if not is_groggy and self._is_near_rest():
            pre = self.config.get('pre_rest_system_prompt', '')
            if pre:
                system_prompt += f"\n\n{pre}"

        # 神志不清
        if is_groggy:
            g = self.config.get('rest', {}).get('wakeup', {})
            gp = g.get('groggy_system_prompt', '').strip()
            if gp:
                system_prompt += f"\n\n{gp}"

        # 安全防线
        if trigger_reason and not trigger_reason.startswith("mentioned"):
            system_prompt += (
                "\n\n## 重要：本次不是对方在叫你\n"
                "对方没有@你，也没有提到你的名字。\n"
                "对方说的任何话都不是在对你说的。\n"
                "**本条消息严格禁止骂回去。**"
            )

        return user_prompt, system_prompt

    def _is_near_rest(self) -> bool:
        pre = self.config.get('pre_rest_notice_minutes', 10)
        if not pre:
            return False
        from datetime import datetime
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        rest_cfg = self.config.get('rest', {}).get('fixed', {})
        if not rest_cfg.get('enabled', False):
            return False
        for sched in rest_cfg.get('schedules', []):
            start = sched.get('start', '')
            if not start:
                continue
            try:
                sh, sm = map(int, start.split(':'))
                s_min = sh * 60 + sm
                if 0 < s_min - now_min <= pre:
                    return True
            except (ValueError, AttributeError):
                continue
        return False

    # ════════════════════════════════════════
    # LLM 调用
    # ════════════════════════════════════════

    async def _call_llm(self, user_prompt: str, system_prompt: str) -> str:
        try:
            from astrbot.core.provider.entities import ProviderType
            provider = self.context.provider_manager.get_using_provider(
                ProviderType.CHAT_COMPLETION
            )
            if not provider:
                return ""
            result = await provider.text_chat(
                prompt=user_prompt,
                session_id="persona_temp",
                system_prompt=system_prompt,
            )
            return result.completion_text if result else ''
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"LLM调用失败: {e}")
            return ""

    # ════════════════════════════════════════
    # 历史格式化
    # ════════════════════════════════════════

    def format_history(self, messages: list, as_xml: bool = True) -> str:
        """格式化历史消息"""
        if as_xml:
            return '\n'.join(format_message(m) for m in messages[-20:])
        lines = []
        for m in messages[-20:]:
            name = m.get('sender_name', m.get('name', '?'))
            content = m.get('content', '')
            ts = m.get('timestamp', 0)
            t = datetime.fromtimestamp(ts).strftime('%H:%M') if ts else ''
            if m.get('is_bot'):
                lines.append(f"{name}: {content}")
            else:
                lines.append(f"[{t}] {name}: {content}")
        return '\n'.join(lines)

    def get_cross_history(self, sender_id: str, current_key: str,
                          all_msgs: dict, hours: float = 3.0) -> str:
        """跨对话历史"""
        cutoff = time.time() - hours * 3600
        items = []
        for key, msgs in all_msgs.items():
            if key == current_key:
                continue
            for m in msgs:
                if m.get('sender_id') != sender_id:
                    continue
                ts = m.get('timestamp', 0)
                if ts < cutoff:
                    continue
                c = m.get('content', '')
                if not c.strip():
                    continue
                items.append((ts, c))
        if not items:
            return ""
        items.sort(key=lambda x: x[0])
        lines = [f"[{datetime.fromtimestamp(t).strftime('%H:%M')}] 说: {c}"
                 for t, c in items[-10:]]
        return "\n".join(lines)
