"""
拟人化插件核心模块
"""
from .personification_manager import PersonificationManager
from .affinity_system import AffinitySystem
from .blacklist_manager import BlacklistManager
from .qzone_system import QZoneSystem
from .qzone_adapter import QZoneAdapter

__all__ = [
    'PersonificationManager',
    'AffinitySystem',
    'BlacklistManager',
    'QZoneSystem',
    'QZoneAdapter'
]
