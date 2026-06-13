"""内置 QQ 空间服务层"""
from astrbot.api import logger
from .db import Post


class LLMAction:
    def __init__(self, config):
        self.config = config


class Sender:
    def __init__(self, config):
        self.config = config


class PostService:
    """服务层（本地模式 — 数据存到本地 JSON 文件）"""

    def __init__(self, qzone_api, session, db, llm):
        self.qzone_api = qzone_api
        self.session = session
        self.db = db
        self.llm = llm

    async def publish_post(self, text="", images=None):
        post = await self.db.add_post(text=text, images=images)
        logger.info(f"[QZoneLocal] 已保存本地动态: {text[:30] if text else '(图片)'}...")
        return post

    async def query_feeds(self, pos=0, num=5, with_detail=False):
        return await self.db.get_recent_posts(limit=num)
