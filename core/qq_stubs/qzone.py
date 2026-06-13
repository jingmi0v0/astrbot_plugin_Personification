"""内置 QQ 空间会话和 API（本地模式）"""
from astrbot.api import logger


class QzoneSession:
    """本地模式不需要真实会话"""
    def __init__(self, config):
        self.config = config
        logger.info("[QZoneLocal] 本地 QQ 空间模式已启用")

    async def login(self):
        return True

    async def close(self):
        pass


class QzoneAPI:
    """QQ 空间 API（本地模式，操作本地数据）"""
    def __init__(self, session, config):
        self.session = session
        self.config = config

    async def publish_post(self, text="", images=None):
        return {"success": True, "tid": str(int(time.time() * 1000))[-12:]}

    async def get_recent_feeds(self, num=5):
        return []

    async def like_post(self, tid):
        return {"success": True}

    async def comment_post(self, tid, text):
        return {"success": True}
