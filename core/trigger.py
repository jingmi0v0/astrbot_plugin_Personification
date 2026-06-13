"""触发系统 — next_reply / wake_up_reply 条件管理"""
import re
import time
import asyncio
from datetime import datetime
from typing import Optional


class TriggerStore:
    """管理 next_reply / wake_up_reply 条件

    next_reply: 一次性触发，满足条件后立即回复
    wake_up_reply: 定时触发，指定时间点回复
    """

    def __init__(self):
        self._next_reply_conditions: dict[str, str] = {}       # session_key -> reason表达式
        self._wake_up_tasks: dict[str, asyncio.Task] = {}       # session_key -> asyncio.Task
        self._last_session: dict[str, any] = {}                 # session_key -> 最后发言session

    def set_last_session(self, session_key: str, sender_id: str, message: str):
        self._last_session[session_key] = {
            'sender_id': sender_id,
            'message': message,
            'time': time.time()
        }

    def set_next_reply(self, session_key: str, reason: str):
        """设置下一次主动触发条件

        reason 格式示例:
          - "time_60s" → 60秒无消息触发
          - "id_123456789" → 指定用户发消息触发
          - "time_10s_id_123456789" → 指定用户10秒没发消息触发
          - "id_123&time_30|time_600" → 组合条件（&且 |或）
        """
        self._next_reply_conditions[session_key] = reason

    def check_next_reply(self, session_key: str, sender_id: str, elapsed_since_last: float) -> Optional[str]:
        """检查 next_reply 条件是否满足"""
        reason = self._next_reply_conditions.get(session_key)
        if not reason:
            return None

        # 按 | 分割（OR）
        groups = reason.split('|')
        for group in groups:
            # 按 & 分割（AND）
            conditions = group.split('&')
            if self._evaluate_group(conditions, sender_id, elapsed_since_last):
                del self._next_reply_conditions[session_key]
                return reason

        return None

    def clear_next_reply(self, session_key: str):
        self._next_reply_conditions.pop(session_key, None)

    def _evaluate_group(self, conditions: list[str], sender_id: str, elapsed: float) -> bool:
        for cond in conditions:
            cond = cond.strip()
            if not cond:
                continue
            if cond.startswith('time_'):
                # time_60s → 60秒无消息
                secs = self._parse_time(cond)
                if elapsed >= secs:
                    continue
                return False
            elif cond.startswith('id_'):
                # id_123456789 → 指定用户
                target_id = cond[3:]
                if sender_id == target_id:
                    continue
                return False
            else:
                return False
        return True

    @staticmethod
    def _parse_time(cond: str) -> float:
        """从 'time_60s' 或 'time_10s_id_123' 中提取秒数"""
        m = re.match(r'time_(\d+)s', cond)
        return float(m.group(1)) if m else 0

    async def schedule_wake_up(self, session_key: str, time_str: str, reason: str, callback):
        """安排定时触发

        time_str: "2026/02/20-21:30:00"
        """
        # 取消已有任务
        if session_key in self._wake_up_tasks:
            self._wake_up_tasks[session_key].cancel()

        task = asyncio.create_task(self._wake_up_waiter(session_key, time_str, reason, callback))
        self._wake_up_tasks[session_key] = task

    async def _wake_up_waiter(self, session_key: str, time_str: str, reason: str, callback):
        try:
            target = datetime.strptime(time_str, "%Y/%m/%d-%H:%M:%S").timestamp()
            now = time.time()
            delay = target - now
            if delay > 0:
                await asyncio.sleep(delay)
                await callback(session_key, reason)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._wake_up_tasks.pop(session_key, None)
