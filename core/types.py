"""
拟人化插件 — 类型定义
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    """聊天消息"""
    content: str
    name: str
    id: str
    message_id: Optional[str] = None
    timestamp: Optional[float] = None
    quote: Optional['Message'] = None
    images: list = field(default_factory=list)


@dataclass
class GroupTemp:
    """群组/私聊临时状态"""
    status: Optional[str] = None
    history_cleared_at: Optional[float] = None
    record_loaded: bool = False


@dataclass
class GroupLock:
    """群组锁状态"""
    mute_until: float = 0.0
    response_locked: bool = False


class PresetTemplate:
    """角色预设模板"""
    def __init__(self, name: str, data: dict):
        self.name = name
        self.system_prompt = data.get('system', '')
        self.input_template = data.get('input', '')
        self.status = data.get('status', '')
        self.nicknames = data.get('nick_name', [])
        self.mute_keywords = data.get('mute_keyword', [])
