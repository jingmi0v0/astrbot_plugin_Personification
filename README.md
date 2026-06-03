# AstrBot 拟人化插件 (Personification)

让 AstrBot 拥有**真正拟人化**的聊天能力。不是模板回复——每一次对话都由 LLM 根据角色设定实时生成，支持好感度、休息唤醒、沉默检测、活跃对话追踪等完整交互体验。

## 注意事项

- 作者邮箱：
```text
mcap163@163.com
```

- ⚠️ 插件不支持在 AstrBot Web 面板编辑配置，请直接修改 `config.yml`
- ⚠️ 需要一定的动手能力熟悉 YAML 配置
- ⚠️ 当前版本测试功能较多，如遇问题请提交 Issue或联系作者
- ⚠️ 不兼容以下插件（会有冲突）：
  - [QQ Space Plugin](https://github.com/Zhalslar/astrbot_plugin_qzone)
  - [LivelyState](https://github.com/KonmaKanSinPack/astrbot_plugin_LivelyState)
  - 任何修改 LLM 消息的插件
  - 其他拟人化角色插件


## 功能一览

| 功能 | 说明 |
|------|------|
| 🎭 **拟人化聊天** | LLM 实时生成，完全遵循角色设定 |
| 💖 **好感度系统** | 动态增减、阈值拉黑、持久化存储 |
| 😴 **休息系统** | 固定时段休息 + 随机休息 + 唤醒后神志不清 |
| 🔇 **闭嘴检测** | 检测关键词后自动闭嘴一段时间 |
| ❓ **沉默@检测** | @了不说话？等 30 秒后@回去询问 |
| 🤔 **无厘头消息检测** | 看不懂的消息等 10~40 秒，不补充再问 |
| 💬 **活跃对话** | @一次后保持回复，无需重复@ |
| 🚫 **黑名单管理** | 用户/群聊双重黑名单 |
| 📱 **QQ 空间集成** | 自动发说说、看动态 |
| 🛡️ **安全防线** | 非明确@时禁止"骂回去" |

## 快速开始

### 首次配置

编辑 `config.yml`，至少修改以下字段：

```yaml
name: 你的机器人名字
nick_name:
  - 名字
  - '@名字'
system: |
  # 角色设定（自行配置）
  ...
```

### 常用命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/查看好感度 [用户ID]` | 查看好感度 | 所有人 |
| `/设置好感度 <用户ID> <数值>` | 设置好感度 | 管理员 |
| `/清除好感度 <用户ID>` | 重置好感度 | 管理员 |
| `/查看黑名单` | 查看黑名单列表 | 管理员 |
| `/移除黑名单 <用户ID>` | 移除黑名单 | 管理员 |
| `/发说说 [内容]` | 手动发 QQ 空间动态 | 管理员 |
| `/看说说 [数量]` | 查看最近动态 | 所有人 |
| `/重载配置` | 热加载 config.yml | 管理员 |

## 核心功能详解

### 🎭 拟人化聊天

每次对话由 LLM 实时生成，完全基于 `config.yml` 中的 `system` 角色设定。支持：
- **输入模板** (`input`) — 控制 LLM 看到的上下文格式
- **状态系统** (`status`) — 机器人记住当前心情、状态
- **长期记忆** — 自动保存和回忆重要信息
- **多种触发方式** — @、昵称、活跃度、私聊

#### 触发优先级

1. **被 @** — `mentioned`（最高优先级）
2. **提到昵称** — `mentioned`
3. **被 @ 但没说话** — `mentioned_silent` → 启动 30s 等待计时器
4. **活跃对话中** — `active_conversation`（无需重复@）
5. **群聊活跃度达到阈值** — `activity_trigger`
6. **私聊** — `private_chat`

### 💬 活跃对话系统

被 @ 一次后，同个用户后续消息**无需重复 @**，机器人会持续回复。对话在以下情况结束：

| 条件 | 说明 | 配置项 |
|------|------|--------|
| 其他人刷屏 | 对话中其他人发了 N 条消息 | `active_conv_max_other_msgs: 3` |
| 用户说再见 | 包含结束关键词 | `active_conv_end_keywords` |
| 长时间无互动 | 自然冷却后恢复正常 | — |

### 😴 休息系统

#### 固定休息
在指定时间段内（如凌晨 00:00~07:30）不回复消息。

```yaml
rest:
  fixed:
    enabled: true
    schedules:
      - start: "00:00"
        end: "07:30"
      - start: "12:00"
        end: "13:00"    # 午休
```

#### 随机休息
不定期随机进入休息状态，模拟真人作息。

```yaml
rest:
  random:
    enabled: true
    min_duration: 300       # 最少休息 5 分钟
    max_duration: 1800      # 最多休息 30 分钟
    trigger_probability: 0.15  # 15% 概率触发
```

#### 唤醒机制（固定休息时段有效）
休息期间有人一直发消息，有概率被唤醒，但说话**神志不清**。

```yaml
rest:
  wakeup:
    enabled: true
    message_threshold: 3     # 累计 3 条消息后开始尝试唤醒
    wakeup_probability: 0.3  # 每次 30% 概率唤醒
    awake_duration: 180      # 清醒 3 分钟后重新入睡
    groggy_system_prompt: |  # 神志不清时的额外提示
      你刚刚被吵醒，现在非常困，意识模糊...
```

### ❓ 沉默@检测

当用户 @ 了机器人但没说话，等待 `silent_mention_timeout` 秒。如果用户没有后续消息，@ 回去询问。

```yaml
silent_mention_timeout: 30  # 等 30 秒
```

超时后由 LLM 按人设生成回复，如 `"喵？有事吗~"` `"？"` 等。

### 🤔 无厘头消息检测

当用户发了看不懂的消息（纯标点、单个语气词、无意义短句），等待随机 10~40 秒。如果用户没有补充说明，回复"没听懂"类消息。

```yaml
confused_wait_min: 10
confused_wait_max: 40
```

**上下文感知**：如果正在和机器人对话，"嗯"会被识别为肯定回应，不会误触发。

### 🛡️ 安全防线

**双重防护**确保不会因随机接话而误"骂回去"：

1. **Prompt 层** — system prompt 中加入了上下文判断规则
2. **代码层** — 非 @/提及 触发的回复，强制在 prompt 追加"对方不是在对你说"，禁止骂回去

### 💖 好感度系统

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `default_value` | 0 | 初始好感度 |
| `min_value` | -100 | 最小值 |
| `max_value` | 100 | 最大值 |
| `blacklist_threshold` | -80 | 低于此值自动拉黑 |
| `step` | 2 | 每次互动基础变化量 |

### 🔇 闭嘴关键词

当消息包含以下词语时，机器人闭嘴一段时间：

```yaml
mute_keyword:
  - 闭嘴
  - 弱智
  - 傻逼
mute_time: 60  # 闭嘴时长（秒）
```

## 完整配置参考

所有配置均在 `config.yml` 中，无需修改代码：

| 配置路径 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `name` | string | — | 角色名称 |
| `nick_name` | string[] | — | 唤醒昵称列表 |
| `system` | string | — | **角色设定（system prompt）** |
| `input` | string | — | LLM 输入模板 |
| `status` | string | — | 初始状态 |
| `max_messages` | int | 40 | 最大历史消息数 |
| `activity_threshold` | float | 0.3 | 群聊活跃度触发阈值 |
| `mute_time` | int | 60 | 闭嘴时长（秒） |
| `mute_keyword` | string[] | — | 闭嘴关键词 |
| `ignore_keywords` | string[] | — | 忽略关键词（不回复） |
| `typing_time` | int | 3 | 打字基础延迟（秒） |
| `typing_per_char` | float | 0.1 | 每字额外延迟（秒） |
| `speak_cooldown` | int/dict | 30 | 回复后冷却时间 |
| `session_persistence.status_expire_days` | int | 5 | 情感状态过期天数 |
| `silent_mention_timeout` | int | 30 | 沉默@等待秒数 |
| `confused_wait_min` | int | 10 | 无厘头消息最短等待 |
| `confused_wait_max` | int | 40 | 无厘头消息最长等待 |
| `active_conv_max_other_msgs` | int | 3 | 活跃对话中他人消息阈值 |
| `active_conv_end_keywords` | string[] | — | 结束对话关键词 |
| `affinity.*` | — | — | 好感度系统参数 |
| `qzone.*` | — | — | QQ 空间系统参数 |
| `rest.fixed.schedules` | — | — | 固定休息时段 |
| `rest.random.*` | — | — | 随机休息参数 |
| `rest.wakeup.*` | — | — | 唤醒机制参数 |

> 改配置后运行 `/重载配置` 即可生效，无需重启 AstrBot。

## 架构说明

```
消息流入
  │
  ├─ 过滤阶段（handle_message）
  │   ├─ 自身消息过滤
  │   ├─ 空消息过滤（保留纯@）
  │   ├─ 活跃对话：他人消息计数
  │   ├─ 闭嘴检测
  │   ├─ 忽略关键词
  │   ├─ 会话过期检查
  │   └─ 休息/唤醒检测
  │
  ├─ 决策阶段（_should_reply）
  │   ├─ 预设触发条件 (next_reply)
  │   ├─ @ 或昵称匹配
  │   ├─ 活跃对话检查
  │   └─ 群聊活跃度
  │
  ├─ 特殊处理
  │   ├─ 沉默@ → 启动计时器
  │   ├─ 无厘头消息 → 启动计时器
  │   └─ 正常回复 → _generate_and_send_reply
  │
  └─ 回复生成（_generate_and_send_reply）
      ├─ 构建 prompt（system + user）
      ├─ 追加安全规则
      ├─ 调用 LLM
      ├─ 解析回复（消息/动作/状态）
      ├─ 执行动作（戳一戳/表情/记忆操作）
      ├─ 发送消息
      ├─ 更新好感度
      └─ 建立活跃对话
```



## 文件结构

```
astrbot_plugin_Personification/
├── main.py                          # 插件入口
├── metadata.yaml                    # 插件元信息
├── config.yml                       # 角色配置（所有修改在这里）
├── README.md                        # 本文件
├── requirements.txt
├── core/
│   ├── __init__.py
│   ├── personification_manager.py   # 核心聊天逻辑
│   ├── affinity_system.py           # 好感度系统
│   ├── blacklist_manager.py         # 黑名单管理
│   ├── qzone_system.py             # QQ 空间系统
│   └── database.py                 # 数据库
└── presets/                         # 角色预设（不要在这里改！！！！！）
```

## 鸣谢

感谢以下项目：

- **[chatluna-character](https://github.com/ChatLunaLab/chatluna-character)** - 拟人化插件的设计灵感
- **[QQ Space Plugin](https://github.com/Zhalslar/astrbot_plugin_qzone)** - QQ空间功能的完整实现

## 开源协议

**AGPL-3.0** — 详见 [LICENSE](LICENSE)。
