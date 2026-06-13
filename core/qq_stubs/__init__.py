"""
QQ 插件桩模块 — 内置本地 QQ 空间实现
不依赖外部 QQ 插件，使用本地 JSON 文件存储动态
"""
from .config import PluginConfig
from .qzone import QzoneSession, QzoneAPI
from .db import PostDB
from .service import LLMAction, Sender, Post, PostService
