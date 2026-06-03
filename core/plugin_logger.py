"""
插件日志级别控制
以模块级单例替换 `from astrbot.api import logger`，根据 log_level 过滤输出。

使用方式：
  from .plugin_logger import logger
  logger.info(...)   # 自动根据配置的 log_level 判断是否输出
"""

from astrbot.api import logger as _astrbot_logger

# ---- 级别常量 ----
LV_NONE = 0
LV_DEBUG = 1      # DEBUG：仅错误
LV_WARING = 2     # Waring：仅警告
LV_SIMPLE = 3     # 简单（默认）：回复内容 + 错误
LV_NORMAL = 4     # 普通：简单 + 信息 + 警告
LV_DETAILED = 5   # 详细：普通 + LLM 提示词
LV_ALL = 6        # ALL：全部

_LEVEL_MAP = {
    '无': LV_NONE,
    'DEBUG': LV_DEBUG,
    'Waring': LV_WARING,
    '简单': LV_SIMPLE,
    '普通': LV_NORMAL,
    '详细': LV_DETAILED,
    'ALL': LV_ALL,
}

# 模块级配置提供器（在插件初始化时 set 进来）
_config_provider = None


def set_config_provider(provider):
    """设置配置提供器，provider 是一个可调用对象，接受 (key, default) 返回配置值"""
    global _config_provider
    _config_provider = provider


def _get_level() -> int:
    try:
        if _config_provider is not None:
            level_str = _config_provider('log_level', '简单')
        else:
            level_str = '简单'
        return _LEVEL_MAP.get(level_str, LV_SIMPLE)
    except Exception:
        return LV_SIMPLE


# ========== 代替 from astrbot.api import logger 的兼容接口 ==========

class _PluginLoggerProxy:
    """代理对象，使 `logger.info() / .error() / .debug()` 全部走级别过滤"""

    # ---- 各接口的最小级别 ----
    # error:     LV_DEBUG   (1)
    # exception: LV_DEBUG   (1)
    # warning:   LV_WARING  (2)
    # reply:     LV_SIMPLE  (3)  — 自定义，回复内容
    # info:      LV_NORMAL  (4)
    # prompt:    LV_DETAILED (5) — 自定义，LLM 提示词
    # debug:     LV_ALL     (6)

    def error(self, msg, *args, **kwargs):
        if _get_level() >= LV_DEBUG:
            _astrbot_logger.error(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        if _get_level() >= LV_DEBUG:
            _astrbot_logger.exception(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        if _get_level() >= LV_WARING:
            _astrbot_logger.warning(msg, *args, **kwargs)

    def reply(self, msg, *args, **kwargs):
        """回复内容（简单及以上可见）"""
        if _get_level() >= LV_SIMPLE:
            _astrbot_logger.info(f"[回复] {msg}", *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """普通信息（普通及以上可见）"""
        if _get_level() >= LV_NORMAL:
            _astrbot_logger.info(msg, *args, **kwargs)

    def prompt(self, msg, *args, **kwargs):
        """LLM 提示词（详细及以上可见）"""
        if _get_level() >= LV_DETAILED:
            _astrbot_logger.info(f"[LLM] {msg}", *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        """调试追踪（ALL 可见）"""
        if _get_level() >= LV_ALL:
            _astrbot_logger.debug(msg, *args, **kwargs)


logger = _PluginLoggerProxy()
