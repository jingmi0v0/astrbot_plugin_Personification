"""内置 QQ 空间数据库（本地 JSON 文件存储）"""
import json
import time
from pathlib import Path
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class Post:
    """动态模型"""
    def __init__(self, tid="", text="", images=None, create_time=None, comments=None, likes=0):
        self.tid = tid or str(int(time.time() * 1000))[-12:]
        self.text = text
        self.images = images or []
        self.create_time = create_time or time.time()
        self.comments = comments or []
        self.likes = likes

    def to_dict(self):
        return {"tid": self.tid, "text": self.text, "images": self.images,
                "create_time": self.create_time, "comments": self.comments, "likes": self.likes}

    @staticmethod
    def from_dict(d):
        return Post(d["tid"], d.get("text", ""), d.get("images", []),
                    d.get("create_time"), d.get("comments", []), d.get("likes", 0))


class PostDB:
    """本地 JSON 文件存储"""

    def __init__(self, config=None):
        self.config = config
        self._data_path = Path(get_astrbot_data_path()) / "plugins" / "personification_qzone.json"
        self._posts: list[dict] = []

    async def initialize(self):
        try:
            if self._data_path.exists():
                with open(self._data_path, "r", encoding="utf-8") as f:
                    self._posts = json.load(f)
                logger.info(f"[QZoneLocal] 加载了 {len(self._posts)} 条本地动态")
            else:
                logger.info("[QZoneLocal] 本地动态文件为空")
        except Exception as e:
            logger.error(f"[QZoneLocal] 加载失败: {e}")

    async def add_post(self, **kwargs) -> Post:
        post = Post(**kwargs)
        self._posts.insert(0, post.to_dict())
        self._trim()
        await self._save()
        return post

    async def get_recent_posts(self, limit=5) -> list[Post]:
        return [Post.from_dict(p) for p in self._posts[:limit]]

    async def get_post_by_tid(self, tid) -> Post | None:
        for p in self._posts:
            if p["tid"] == str(tid):
                return Post.from_dict(p)
        return None

    async def add_comment(self, tid, comment_text):
        for p in self._posts:
            if p["tid"] == str(tid):
                p.setdefault("comments", []).append({"text": comment_text, "time": time.time()})
                await self._save()
                return True
        return False

    async def like_post(self, tid):
        for p in self._posts:
            if p["tid"] == str(tid):
                p["likes"] = p.get("likes", 0) + 1
                await self._save()
                return True
        return False

    async def close(self):
        pass

    def _trim(self):
        if len(self._posts) > 200:
            self._posts = self._posts[:200]

    async def _save(self):
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._posts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[QZoneLocal] 保存失败: {e}")
