#!/usr/bin/env python3
"""
拟人化插件测试脚本
用于快速测试插件功能
"""
import asyncio
import sys
from pathlib import Path

# 添加插件目录到路径
plugin_dir = Path(__file__).parent
sys.path.insert(0, str(plugin_dir))


async def test_database():
    """测试数据库初始化"""
    print("测试数据库初始化...")
    from core.database import init_database
    
    success = await init_database()
    if success:
        print("✓ 数据库初始化成功")
    else:
        print("✗ 数据库初始化失败")
    
    return success


async def test_affinity_system():
    """测试好感度系统"""
    print("\n测试好感度系统...")
    from core.affinity_system import AffinitySystem
    
    # 创建模拟context
    class MockContext:
        def get_config(self):
            return {
                'affinity': {
                    'default_value': 0,
                    'min_value': -100,
                    'max_value': 100
                }
            }
    
    affinity_system = AffinitySystem(MockContext())
    await affinity_system.initialize()
    
    # 测试设置和获取好感度
    test_user = "test_user_123"
    
    await affinity_system.set_affinity(test_user, 50)
    value = await affinity_system.get_affinity(test_user)
    
    if value == 50:
        print(f"✓ 好感度设置成功: {value}")
    else:
        print(f"✗ 好感度设置失败: 期望50, 实际{value}")
    
    # 测试更新好感度
    new_value = await affinity_system.update_affinity(test_user, 10)
    if new_value == 60:
        print(f"✓ 好感度更新成功: {new_value}")
    else:
        print(f"✗ 好感度更新失败: 期望60, 实际{new_value}")
    
    return True


async def test_blacklist_manager():
    """测试黑名单管理器"""
    print("\n测试黑名单管理器...")
    from core.blacklist_manager import BlacklistManager
    
    # 创建模拟context
    class MockContext:
        pass
    
    blacklist_manager = BlacklistManager(MockContext())
    await blacklist_manager.initialize()
    
    # 测试添加黑名单
    test_user = "test_user_456"
    await blacklist_manager.add_to_blacklist(test_user, "测试原因", "user")
    
    is_blacklisted = await blacklist_manager.is_in_blacklist(test_user)
    if is_blacklisted:
        print(f"✓ 黑名单添加成功")
    else:
        print(f"✗ 黑名单添加失败")
    
    # 测试移除黑名单
    removed = await blacklist_manager.remove_from_blacklist(test_user)
    if removed:
        print(f"✓ 黑名单移除成功")
    else:
        print(f"✗ 黑名单移除失败")
    
    return True


async def main():
    """主测试函数"""
    print("=" * 60)
    print("ARSTBOT 拟人化插件测试")
    print("=" * 60)
    
    try:
        # 运行所有测试
        await test_database()
        await test_affinity_system()
        await test_blacklist_manager()
        
        print("\n" + "=" * 60)
        print("所有测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
