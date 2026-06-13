"""内置 QQ 空间服务层（本地模式）"""
import time
from astrbot.api import logger
from .db import Post


class LLMAction:
    def __init__(self, config):
        self.config = config

    async def generate_comment(self, post):
        return ""

    async def generate_reply(self, post, comment):
        return ""


class Sender:
    def __init__(self, config):
        self.config = config


class PostService:
    """服务层（本地模式 — 数据存本地 JSON 文件，无真实 QQ 发布）"""
    def __init__(self, qzone, session, db, llm):
        self.qzone = qzone
        self.session = session
        self.db = db
        self.llm = llm

    async def query_feeds(self, pos=0, num=5, with_detail=False, **kwargs) -> list[Post]:
        return await self.db.get_recent_posts(limit=num)

    async def publish_post(self, *, post=None, text="", images=None):
        if post:
            await self.db.save(post)
            return post
        p = await self.db.add_post(text=text, images=images or [])
        logger.info(f"[QZoneLocal] 已保存本地动态: {text[:30] if text else '(图片)'}...")
        return p

    async def like_posts(self, post):
        if post.tid:
            await self.db.like_post(post.tid)

    async def comment_posts(self, post):
        if post.tid:
            await self.db.add_comment(post.tid, "💬")

    async def delete_post(self, post):
        pass
