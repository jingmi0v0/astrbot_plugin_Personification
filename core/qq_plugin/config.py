"""内置配置模块（接收普通 dict，不依赖 AstrBotConfig 路径）"""


class PluginConfig:
    """配置包装器（桩模式 — 直接接受 dict）"""
    def __init__(self, config, context=None):
        self._data = config if isinstance(config, dict) else {}
        self.context = context
        self.data_dir = None
        self.db_path = None
        self.style_dir = None
        self.cache_dir = None
        self.client = None
        self.timeout = self._data.get('timeout', 30)

    def get(self, key, default=None):
        return self._data.get(key, default) if self._data else default

    def __getitem__(self, key):
        return self._data.get(key) if self._data else None
