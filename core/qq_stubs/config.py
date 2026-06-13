"""内置配置模块"""
from astrbot.core.config.astrbot_config import AstrBotConfig


class PluginConfig:
    """配置包装器"""
    def __init__(self, config: AstrBotConfig, context=None):
        self.config = config
        self.context = context

    def get(self, key, default=None):
        return self.config.get(key, default) if self.config else default

    def __getitem__(self, key):
        return self.config.get(key) if self.config else None
