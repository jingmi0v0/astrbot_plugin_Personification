"""黑名单系统"""
import json
from pathlib import Path
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class BlacklistManager:
    def __init__(self, config: dict):
        self.config = config
        self._data_path = Path(get_astrbot_data_path()) / "plugins" / "personification_blacklist.json"
        self._data: dict[str, dict] = {}  # user_id -> {reason, timestamp}

    async def initialize(self):
        try:
            if self._data_path.exists():
                with open(self._data_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"[Blacklist] 加载了 {len(self._data)} 条黑名单记录")
        except Exception as e:
            logger.error(f"[Blacklist] 加载失败: {e}")

    async def is_blacklisted(self, user_id: str) -> bool:
        return user_id in self._data

    async def add(self, user_id: str, reason: str = "", target_type: str = "私聊"):
        self._data[user_id] = {"reason": reason, "timestamp": __import__('time').time(), "type": target_type}
        await self._save()

    async def remove(self, user_id: str) -> bool:
        if user_id in self._data:
            del self._data[user_id]
            await self._save()
            return True
        return False

    async def list_all(self) -> list[dict]:
        return [
            {"id": uid, "reason": info.get("reason", ""), "timestamp": info.get("timestamp", 0), "type": info.get("type", "私聊")}
            for uid, info in self._data.items()
        ]

    async def _save(self):
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Blacklist] 保存失败: {e}")
