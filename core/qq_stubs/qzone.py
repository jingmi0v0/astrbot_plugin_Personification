"""内置 QQ 空间会话和 API（本地模式）"""
from astrbot.api import logger


class QzoneSession:
    """会话（本地模式 — 无真实 QQ 登录）"""
    def __init__(self, config):
        self.cfg = config
        logger.info("[QZoneLocal] 本地 QQ 空间模式（无真实 QQ 登录）")

    async def get_ctx(self):
        return None

    async def get_uin(self):
        return 0

    async def get_nickname(self):
        return "本地模式"

    async def invalidate(self):
        pass

    async def login(self, cookies_str=None):
        return None

    async def close(self):
        pass


class QzoneAPI:
    """API（本地模式）"""
    def __init__(self, session, config):
        self.session = session
        self.cfg = config

    async def close(self):
        pass
