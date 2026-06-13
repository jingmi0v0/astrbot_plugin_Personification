"""好感度系统"""
import json
from pathlib import Path
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class AffinitySystem:
    def __init__(self, config: dict):
        self.config = config
        self._data_path = Path(get_astrbot_data_path()) / "plugins" / "personification_affinity.json"
        self._data: dict[str, int] = {}

    async def initialize(self):
        try:
            if self._data_path.exists():
                with open(self._data_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"[Affinity] 加载了 {len(self._data)} 条好感度记录")
            else:
                logger.info("[Affinity] 好感度文件不存在，从零开始")
        except Exception as e:
            logger.error(f"[Affinity] 加载失败: {e}")

    async def get(self, user_id: str) -> int:
        return self._data.get(user_id, 0)

    async def set(self, user_id: str, value: int):
        self._data[user_id] = value
        await self._save()

    async def add(self, user_id: str, delta: int):
        current = await self.get(user_id)
        new_val = max(-100, min(100, current + delta))
        if new_val != current:
            self._data[user_id] = new_val
            await self._save()
        return new_val

    async def clear(self, user_id: str):
        self._data.pop(user_id, None)
        await self._save()

    async def all_blacklisted(self, threshold: int = -80) -> list[str]:
        return [uid for uid, val in self._data.items() if val <= threshold]

    async def _save(self):
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Affinity] 保存失败: {e}")
