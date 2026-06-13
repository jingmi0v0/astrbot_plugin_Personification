"""休息系统 — 固定休息、随机休息、唤醒"""
import random
import time
import re
import asyncio
from datetime import datetime
from typing import Optional


class RestSystem:
    """管理机器人的休息/唤醒状态

    支持：
    - 固定休息时段（如 00:00-07:30）
    - 随机休息（不定期进入休息）
    - 唤醒机制（休息期被连续消息唤醒，神志不清）
    """

    def __init__(self, config: dict):
        self.config = config

        # 随机休息状态
        self.rest_until: dict[str, float] = {}        # session_key -> 休息到何时
        self._last_rest_check: float = 0.0

        # 唤醒状态
        self.awake_until: dict[str, float] = {}        # session_key -> 清醒到何时
        self.rest_msg_count: dict[str, int] = {}       # session_key -> 休息期消息计数
        self.groggy_count: dict[str, int] = {}         # session_key -> 已发神志不清消息数

    # ── 休息检测 ──

    def is_resting(self, session_key: str) -> bool:
        """是否在休息中"""
        if self._in_fixed_rest():
            return True
        if session_key in self.rest_until:
            if time.time() < self.rest_until[session_key]:
                return True
            del self.rest_until[session_key]
        self._check_random_rest(session_key)
        if session_key in self.rest_until:
            return True
        return False

    def is_awake(self, session_key: str) -> bool:
        """是否被唤醒了（临时清醒）"""
        if session_key in self.awake_until:
            if time.time() < self.awake_until[session_key]:
                return True
            del self.awake_until[session_key]
            self.rest_msg_count.pop(session_key, None)
            self.groggy_count.pop(session_key, None)
        return False

    async def try_wakeup(self, session_key: str, event=None) -> bool:
        """尝试唤醒"""
        wakeup_cfg = self.config.get('rest', {}).get('wakeup', {})
        if not wakeup_cfg.get('enabled', False):
            return False
        # 只允许固定休息时段唤醒
        if not self._in_fixed_rest():
            return False

        count = self.rest_msg_count.get(session_key, 0) + 1
        self.rest_msg_count[session_key] = count

        threshold = wakeup_cfg.get('message_threshold', 3)
        prob = wakeup_cfg.get('wakeup_probability', 0.3)
        duration = wakeup_cfg.get('awake_duration', 180)

        if count >= threshold and random.random() < prob:
            self.awake_until[session_key] = time.time() + duration
            self.rest_msg_count[session_key] = 0
            return True
        return False

    def get_groggy_prompt(self, session_key: str) -> Optional[str]:
        """获取神志不清提示（唤醒后逐渐恢复）"""
        wakeup_cfg = self.config.get('rest', {}).get('wakeup', {})
        base = wakeup_cfg.get('groggy_system_prompt', '').strip()
        if not base:
            return None

        count = self.groggy_count.get(session_key, 0) + 1
        self.groggy_count[session_key] = count
        recovery = self.config.get('wakeup_groggy_recovery_msgs', 3)

        if count > recovery:
            return None

        if count / recovery > 0.7:
            level = "非常困，意识模糊"
        elif count / recovery > 0.3:
            level = "有点醒了，但还迷糊"
        else:
            level = "基本清醒，稍微有点困"

        return f"{base}\n当前清醒程度：{level}"

    # ── 内部 ──

    def _in_fixed_rest(self) -> bool:
        now = datetime.now()
        rest_cfg = self.config.get('rest', {}).get('fixed', {})
        if not rest_cfg.get('enabled', False):
            return False
        now_min = now.hour * 60 + now.minute
        for sched in rest_cfg.get('schedules', []):
            start = sched.get('start', '00:00')
            end = sched.get('end', '07:00')
            try:
                sh, sm = map(int, start.split(':'))
                eh, em = map(int, end.split(':'))
                s_min = sh * 60 + sm
                e_min = eh * 60 + em
                if s_min <= e_min:
                    if s_min <= now_min < e_min:
                        return True
                else:
                    if now_min >= s_min or now_min < e_min:
                        return True
            except (ValueError, AttributeError):
                continue
        return False

    def _check_random_rest(self, session_key: str):
        rand_cfg = self.config.get('rest', {}).get('random', {})
        if not rand_cfg.get('enabled', False):
            return
        interval = rand_cfg.get('check_interval', 600)
        now = time.time()
        if now - self._last_rest_check < interval:
            return
        self._last_rest_check = now
        prob = rand_cfg.get('trigger_probability', 0.15)
        if random.random() < prob:
            min_d = rand_cfg.get('min_duration', 300)
            max_d = rand_cfg.get('max_duration', 1800)
            self.rest_until[session_key] = now + random.randint(min_d, max_d)

    def is_near_rest(self) -> bool:
        """是否即将进入固定休息（用于疲态过渡）"""
        pre = self.config.get('pre_rest_notice_minutes', 10)
        if not pre:
            return False
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        rest_cfg = self.config.get('rest', {}).get('fixed', {})
        if not rest_cfg.get('enabled', False):
            return False
        for sched in rest_cfg.get('schedules', []):
            start = sched.get('start', '')
            if not start:
                continue
            try:
                sh, sm = map(int, start.split(':'))
                s_min = sh * 60 + sm
                if 0 < s_min - now_min <= pre:
                    return True
            except (ValueError, AttributeError):
                continue
        return False
