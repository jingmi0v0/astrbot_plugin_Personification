"""
拟人化插件核心模块 — 基于 chatluna-character 架构
"""
from .message_collector import MessageCollector
from .chat_pipeline import ChatPipeline
from .filter import ActivitySystem
from .rest import RestSystem
from .trigger import TriggerStore
from .response_parser import parse_response, clean_response_text
from .types import Message, GroupLock, PresetTemplate
from .preset import Preset, PresetLoader
from .affinity import AffinitySystem
from .blacklist import BlacklistManager
