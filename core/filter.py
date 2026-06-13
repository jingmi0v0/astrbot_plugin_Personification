"""活跃度系统 — 对应 chatluna-character filter.ts"""
import time
import math
import random

WINDOW_SIZE = 300        # 活跃度计算窗口（秒）
COOLDOWN_PENALTY = 0.15  # 每次回复后活跃度下降量
THRESHOLD_RESET_TIME = 600  # 阈值重置时间（秒）
STALE_GROUP_INFO_TTL = 3600


class ActivitySystem:
    """活跃度评分、空闲触发、固定间隔触发管理

    对应 chatluna-character:
    - src/plugins/filter.ts
    - src/utils/activity.ts
    """

    def __init__(self, config: dict):
        self.config = config
        self._groups: dict[str, dict] = {}

    def _tcfg(self, key: str, default=None):
        """从 trigger 节读取配置"""
        tc = self.config.get('trigger', {})
        return tc.get(key, self.config.get(key, default))

    def _idle_cfg(self, key: str, default=None):
        tc = self.config.get('trigger', {})
        idle = tc.get('idleTrigger', {})
        return idle.get(key, default)

    # ─── 消息处理 ───

    def on_message(self, session_key: str, sender_id: str):
        info = self._get(session_key)
        info['messageCount'] = info.get('messageCount', 0) + 1
        info['messageWait'] = True
        now = time.time()
        info.setdefault('messageTimestamps', []).append(now)
        info['lastUserMessageTime'] = now
        info['lastMessageUserId'] = sender_id

        # 裁剪旧时间戳
        cutoff = now - WINDOW_SIZE
        info['messageTimestamps'] = [t for t in info['messageTimestamps'] if t > cutoff]

    def on_reply(self, session_key: str, is_group: bool = True):
        """回复后调用"""
        info = self._get(session_key)
        info['messageCount'] = 0
        info['messageWait'] = False
        now = time.time()

        # 动态阈值调整
        score = self._calc_score(session_key)
        info['lastActivityScore'] = max(0, score - COOLDOWN_PENALTY)
        info['lastResponseTime'] = now

        if is_group:
            lower = self._tcfg('messageActivityScoreLowerLimit', 0.3)
            upper = self._tcfg('messageActivityScoreUpperLimit', 0.85)
            current = info.get('currentActivityThreshold', lower)
            step = (upper - lower) * 0.1
            info['currentActivityThreshold'] = max(
                min(current + step, max(lower, upper)), min(lower, upper)
            )

    # ─── 触发判断 ───

    def check_activity_trigger(self, session_key: str, is_group: bool) -> str | None:
        """检查是否应触发回复"""
        if not is_group:
            return None

        info = self._get(session_key)
        now = time.time()

        # 空闲触发
        idle_reason = self._check_idle_trigger(session_key, info)
        if idle_reason:
            return idle_reason

        # 活跃度触发
        if self._tcfg('enableActivityScoreTrigger', True):
            score = self._calc_score(session_key)
            threshold = info.get('currentActivityThreshold', 0.3)
            if score >= threshold:
                return f"activity(score={score:.2f})"

        # 固定间隔触发
        if self._tcfg('enableFixedIntervalTrigger', True):
            interval = self._tcfg('messageInterval', 20)
            if interval > 0 and info.get('messageCount', 0) >= interval:
                return f"interval(count={info['messageCount']})"

        return None

    def check_message_wait(self, session_key: str) -> bool:
        """检查是否需要等待更多消息"""
        info = self._get(session_key)
        wait_time = self._tcfg('messageWaitTime', 10)
        if wait_time <= 0:
            return False
        last = info.get('lastUserMessageTime', 0)
        return time.time() - last < wait_time

    def mark_triggered(self, session_key: str, is_group: bool = True):
        self.on_reply(session_key, is_group)

    # ─── 空闲触发 ───

    def _check_idle_trigger(self, session_key: str, info: dict) -> str | None:
        if not self._idle_cfg('enableLongWaitTrigger', False):
            return None
        now = time.time()
        last_msg = info.get('lastUserMessageTime', 0)
        if last_msg == 0:
            return None

        base_interval = self._idle_cfg('idleTriggerIntervalMinutes', 180) * 60
        retry_style = self._idle_cfg('idleTriggerRetryStyle', 'exponential')
        max_interval = self._idle_cfg('idleTriggerMaxIntervalMinutes', 1440) * 60
        max_retries = self._idle_cfg('idleTriggerFixedMaxRetries', 3)

        wait = base_interval
        if retry_style == 'exponential':
            retries = info.get('passiveRetryCount', 0)
            wait = min(base_interval * (2 ** retries), max_interval)
        else:
            retries = info.get('passiveRetryCount', 0)
            if retries >= max_retries:
                return None

        # 抖动
        if self._idle_cfg('enableIdleTriggerJitter', True):
            jitter = wait * random.uniform(-0.1, 0.1)
            wait += jitter

        if now - last_msg >= wait:
            info['passiveRetryCount'] = (info.get('passiveRetryCount', 0) + 1)
            return f"idle(wait={wait:.0f}s)"

        return None

    def on_user_message(self, session_key: str):
        """用户发消息后重置空闲计数"""
        info = self._get(session_key)
        info['passiveRetryCount'] = 0

    # ─── 内部 ───

    def _calc_score(self, session_key: str) -> float:
        info = self._get(session_key)
        timestamps = info.get('messageTimestamps', [])
        if not timestamps:
            return 0.0
        density = len(timestamps) / WINDOW_SIZE
        return min(1.0, density * 10)

    def _get(self, session_key: str) -> dict:
        if session_key not in self._groups:
            self._groups[session_key] = {
                'messageCount': 0,
                'messageTimestamps': [],
                'lastActivityScore': 0,
                'lastScoreUpdate': 0,
                'lastResponseTime': 0,
                'currentActivityThreshold': self._tcfg('messageActivityScoreLowerLimit', 0.3),
                'lastUserMessageTime': 0,
                'passiveRetryCount': 0,
            }
        return self._groups[session_key]
