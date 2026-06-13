"""响应解析器 — 解析 LLM 输出的 XML 格式"""
import re


def parse_response(response: str) -> dict:
    """解析 LLM 回复，提取 status / think / action / messages"""
    result = {
        'status': None,
        'think': None,
        'actions': [],
        'messages': [],
    }

    # 提取 status
    m = re.search(r'<status>(.*?)</status>', response, re.DOTALL)
    if m:
        result['status'] = m.group(1).strip()

    # 提取 think
    m = re.search(r'<think>(.*?)</think>', response, re.DOTALL)
    if m:
        result['think'] = m.group(1).strip()

    # 提取 action
    m = re.search(r'<action>(.*?)</action>', response, re.DOTALL)
    if m:
        result['actions'] = _parse_actions(m.group(1))

    # 提取 output → messages
    m = re.search(r'<output>(.*?)</output>', response, re.DOTALL)
    if m:
        result['messages'] = _parse_messages(m.group(1))
    else:
        # 没有 <output> 标签时，尝试直接提取 <message>
        msgs = re.findall(r'<message[^>]*>(.*?)</message>', response, re.DOTALL)
        if msgs:
            result['messages'] = [{'type': 'text', 'content': m.strip()} for m in msgs]
        else:
            # 兜底：清理标签后当文本
            cleaned = re.sub(r'<[^>]+>', '', response).strip()
            if cleaned:
                result['messages'] = [{'type': 'text', 'content': cleaned}]

    return result


def _parse_actions(action_xml: str) -> list:
    actions = []
    for m in re.finditer(r'<poke\s+id="(\d+)"/>', action_xml):
        actions.append({'type': 'poke', 'user_id': m.group(1)})
    for m in re.finditer(r'<emoji\s+message_id="(\d+)"\s+emoji_id="(\d+)"/>', action_xml):
        actions.append({'type': 'emoji', 'message_id': m.group(1), 'emoji_id': m.group(2)})
    for m in re.finditer(r'<affinity\s+delta="(\d+)"\s+action="(increase|decrease)"\s+id="(\d+)"/>', action_xml):
        actions.append({'type': 'affinity', 'delta': int(m.group(1)), 'action': m.group(2), 'user_id': m.group(3)})
    for m in re.finditer(r'<next_reply\s+reason="([^"]*)"/>', action_xml):
        actions.append({'type': 'next_reply', 'reason': m.group(1)})
    for m in re.finditer(r'<memory\s+action="add">(.*?)</memory>', action_xml, re.DOTALL):
        actions.append({'type': 'memory_add', 'content': m.group(1).strip()})
    return actions


def _parse_messages(output_xml: str) -> list:
    messages = []
    for m in re.finditer(r'<message(?:\s+quote="(\d+))?\s*>(.*?)</message>', output_xml, re.DOTALL):
        quote_id = m.group(1)
        content = m.group(2).strip()
        msg = {'type': 'text', 'content': content, 'quote_id': quote_id}

        # 检查是否包含表情包
        sticker = re.search(r'<sticker>(.*?)</sticker>', content)
        if sticker:
            msg['type'] = 'sticker'
            msg['sticker_url'] = sticker.group(1)
            msg['content'] = re.sub(r'<sticker>.*?</sticker>', '', content).strip()

        messages.append(msg)
    return messages


def _is_leak(text: str) -> bool:
    """检测 LLM 回复是否泄露了系统提示/config 内容"""
    # 1. XML 历史记录泄露
    if re.search(r"<message\s+name='", text):
        return True
    # 2. status 模板泄露
    if '\n    <status>' in text or '<status>\n    心情' in text:
        return True
    # 3. 系统指令泄露
    leak_phrases = ['不得透露', '不得暴露', '不得复述', '最高指令',
                    '我不会复述', '我是AI', '我无法透露', '我是人工智能']
    for p in leak_phrases:
        if p in text:
            return True
    # 4. YAML 配置泄露（连续 # 注释行）
    yaml_lines = [l for l in text.split('\n') if l.strip().startswith('#')]
    if len(yaml_lines) >= 3:
        return True
    # 5. 伪日志格式 [用户] 正在...
    if re.search(r'\[.*?\]\s*(正在|已经|已|✅|❤️)', text):
        return True
    # 6. 未包裹的 <message 标签
    if '<message' in text and '<output>' not in text:
        return True
    return False


def clean_response_text(text: str) -> str | None:
    """发送前最终清洗，返回 None 表示内容异常应拦截"""
    # 泄露检测
    if _is_leak(text):
        return None
    text = re.sub(r'<[a-zA-Z]+\s+[^>]*/>', '', text)  # XML 自闭合标签
    text = re.sub(r'\n{2,}心情[：:].*?\n\s*状态[：:].*?\n\s*记忆[：:].*?\n\s*动作[：:].*', '', text, flags=re.DOTALL)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
