# astrbot_plugin_dynamic_card_plus

增强版动态群名片插件。它会在 QQ 群聊里更新 bot 自己的群名片，也能注册 LLM 工具让 bot 主动改名片。

## 上游说明

本插件 fork 自 [zgojin/astrbot_plugin_botName](https://github.com/zgojin/astrbot_plugin_botName)。

当前 fork 仓库：[Whereis-Alice/astrbot_plugin_dynamic_card_plus](https://github.com/Whereis-Alice/astrbot_plugin_dynamic_card_plus)。

为了避免和上游插件冲突，本 fork 已改名为：

- 插件目录：`astrbot_plugin_dynamic_card_plus`
- 插件 ID：`astrbot_plugin_dynamic_card_plus`
- 注册类：`DynamicCardPlusPlugin`
- LLM 工具：`set_dynamic_group_card`
- 数据状态：仅使用本插件内存状态，不再写入上游的 `data/plugins/astrbot_plugin_botname/system_info.yml`

## 功能

- 自动模式：插件按配置频率直接更新群名片。
- 提醒工具模式：插件不自动改名片，只定时提醒 bot 可以主动调用 LLM 工具改名片。
- 支持 CPU、内存、时间、固定后缀、会话想法摘要、当天日程、随心后缀。
- 当天日程支持规则匹配或让 bot/LLM 生成。
- 支持自然语言让 bot 调用 `set_dynamic_group_card` 改名片。
- 支持群号黑名单和 `unified_msg_origin` 黑名单。

## 适用范围

当前只支持 `aiocqhttp` 的 QQ 群聊，并通过 OneBot API `set_group_card` 修改 bot 自己的群名片。bot 需要在群内拥有修改自己群名片的权限。

## 依赖

```bash
pip install -r requirements.txt
```

依赖项：

- `psutil`

## 配置结构

配置按职责分组：

- `common`：通用开关、运行模式、长度、重试、黑名单。
- `card_fields`：两个模式共用的基础名片字段和系统指标文本。
- `auto_update_mode`：自动改名片模式专属配置和完整名片模板。
- `tool_reminder_mode`：提醒 bot 主动用工具模式专属配置和完整名片模板。
- `thought_summary`：会话想法摘要来源。
- `daily_schedule`：当天日程来源。
- `whim_suffix`：随心后缀来源。
- `llm`：用于生成动态后缀的模型设置。
- `llm_tool`：LLM 工具通用设置。

AstrBot 当前插件配置 schema 只支持静态 `invisible`，不支持“选择某个模式后动态隐藏另一个模式分组”。因此两个模式的配置都会显示，但只有 `common.operation_mode` 选中的模式会生效。

## 运行模式

### auto_update

`common.operation_mode=auto_update` 时，插件会按 `auto_update_mode.update_interval_seconds` 自动改群名片。

使用的完整模板：

```text
auto_update_mode.card_template
```

动态来源开关：

- `auto_update_mode.include_thought_summary`
- `auto_update_mode.include_daily_schedule`
- `auto_update_mode.include_whim_suffix`

### tool_reminder

`common.operation_mode=tool_reminder` 时，插件不会自动修改群名片。它会按 `tool_reminder_mode.reminder_interval_seconds` 在 LLM 请求里提醒 bot：如果她想改自己的群名片，可以主动调用 `set_dynamic_group_card`。

使用的完整模板：

```text
tool_reminder_mode.card_template
```

提醒建议来源：

- `thought`：当前会话想法摘要。
- `schedule`：当天日程。
- `whim`：随心后缀。
- `random`：三种来源随机。

## 完整名片模板

两个模式各有独立完整模板。最终分隔符只由完整模板决定，不再提供额外的“系统指标分隔符”“动态后缀分隔符”，避免多个配置同时控制同一件事。

默认模板：

```text
{bot_name} {cpu_text} {memory_text} {time_text} {suffixes}
```

### 可用变量

| 变量 | 含义 | 示例 | 备注 |
| --- | --- | --- | --- |
| `{bot_name}` | `card_fields.bot_name` | `AstrBot` | 两个模式共用。 |
| `{cpu_text}` | CPU 文本模板渲染结果 | `CPU 12.3%` | 由 `include_cpu` 和 `cpu_template` 控制。 |
| `{memory_text}` | 内存文本模板渲染结果 | `MEM 45.6%` | 由 `include_memory` 和 `memory_template` 控制。 |
| `{time_text}` | 时间文本模板渲染结果 | `08:30` | 由 `include_time` 和 `time_template` 控制。 |
| `{metrics}` | 三个系统文本用空格拼接 | `CPU 12.3% MEM 45.6% 08:30` | 兼容便捷变量。 |
| `{suffixes}` | 后缀用空格拼接 | `摸鱼中 日程:整理插件` | 会跳过空值、模板里已显式写出的后缀变量，也会避免和工具后缀原文相同的动态后缀重复出现。 |
| `{cpu}` | 当前 CPU 使用率数值 | `12.3` | 不带 `%`。 |
| `{memory}` | 当前内存使用率数值 | `45.6` | 不带 `%`。 |
| `{time}` | 当前本地时间 | `08:30` | 格式为 `HH:MM`。 |
| `{date}` | 当前本地日期 | `2026-07-15` | 格式为 `YYYY-MM-DD`。 |
| `{weekday}` | 当前星期 | `星期三` | 中文星期文本。 |
| `{manual_suffix}` | LLM 工具设置的后缀 | `摸鱼中` | 未设置或过期时为空。 |
| `{thought_suffix}` | 会话想法摘要 | `在整理思路` | 工具 `source=thought` 或自动模式可写入。 |
| `{schedule_suffix}` | 当天日程 | `整理插件` | 工具 `source=schedule` 或自动模式可写入。 |
| `{whim_suffix}` | 随心后缀 | `慢慢加载灵感` | 工具 `source=whim` 或自动模式可写入。 |
| `{static_suffix}` | 固定后缀 | `在线` | 来自 `card_fields.static_suffix`。 |

### 示例

只显示基础名字和工具后缀：

```text
{bot_name} {manual_suffix}
```

自定义系统指标分隔符：

```text
{bot_name} | {cpu_text} | {memory_text} | {time_text} | {suffixes}
```

完全手写指标格式：

```text
{bot_name} CPU:{cpu}% MEM:{memory}% {time} {manual_suffix}
```

## LLM 工具

工具名：

```text
set_dynamic_group_card
```

你可以自然语言要求 bot 改名片，例如：

```text
把你的群名片后缀改成“摸鱼中”
根据我们刚才聊天的内容，给你的群名片加个想法后缀
把群名片改成今天的日程状态
随便给自己换个可爱的群名片后缀
```

工具参数：

- `mode=suffix`：设置短后缀。
- `mode=full_card`：设置完整群名片，需要开启 `llm_tool.allow_full_card`。
- `mode=clear_manual`：清除 LLM 工具设置的手动内容。
- `source=manual`：使用传入的 `suffix`。
- `source=thought`：根据当前会话生成想法后缀。
- `source=schedule`：使用当天日程。
- `source=whim`：生成随心后缀。
- `source=random`：在 `thought`、`schedule`、`whim` 中随机。

工具调用成功后会返回“已把当前群名片改为……”，因此 bot 会知道这次名片是自己主动改的。

## 动态来源

### thought_summary

根据当前会话最近消息生成一个短后缀。自动模式中需要开启 `auto_update_mode.include_thought_summary`；工具模式中可以通过 `source=thought` 使用。

### daily_schedule

`daily_schedule.mode` 支持：

- `rules`：按 `schedule_lines` 规则匹配当天日程。
- `llm`：让 bot/LLM 生成当天日程后缀，失败时回落到规则和兜底文本。

默认规则包含一周七天：

```text
周一=整理周计划
周二=推进待办
周三=补充能量
周四=检查进度
周五=准备周末模式
周六=自由活动
周日=慢慢充电
```

你也可以加日期规则：

```text
2026-07-15=今晚整理插件
07-15=纪念日模式
星期四=周四日程
周五=准备周末模式
daily=自由活动
```

日程内容里可用 `{date}`、`{time}`、`{weekday}`。

如果没有命中任何规则，会使用 `daily_schedule.empty_text`，默认是 `自由活动`。

### whim_suffix

- `mode=pool`：从候选池随机选择。
- `mode=llm`：让模型生成，失败时回退到候选池。

## 更新记录

见 [CHANGELOG.md](CHANGELOG.md)。
