"""
拟人化插件核心模块
"""
from .personification_manager import PersonificationManager
from .affinity_system import AffinitySystem
from .blacklist_manager import BlacklistManager
from .qzone_system import QZoneSystem

# QZoneAdapter 不在此处导入（依赖外部 QQ 插件，非必需）
# 在 qzone_system.py 中按需惰性导入

__all__ = [
    'PersonificationManager',
    'AffinitySystem',
    'BlacklistManager',
    'QZoneSystem',
]
