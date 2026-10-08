# 配置与模板

## 安装与更新

插件 ID 为 `astrbot_plugin_dynamic_card_plus`，工具名为 `set_dynamic_group_card`。安装地址见 [README](../README.md)。

1. 已安装的用户先备份配置，在 AstrBot 插件管理中更新本插件，无需更换仓库。
2. 检查机器人基础名字、运行模式、模板与更新间隔。在人格设置中确认名片工具已启用。
3. 如果还安装了其他会修改机器人群名片的插件，请关闭重复功能，避免互相覆盖。
4. 在目标群发送一次消息，然后执行 `/名片预览`、`/名片检查`。

如启用了主动任务，检查 AstrBot 定时任务列表，清理已停用插件遗留的群名片任务，避免重复唤醒。正常卸载时插件会清理自己登记的任务。

## 两种运行方式

### 自动更新

`common.operation_mode=auto_update`。更新间隔是最短间隔，并不是后台计时器：需要机器人回复群消息才会触发。名片与上一次提交相同时跳过写入。

可组合系统指标、固定后缀、聊天摘要、日程和随机心情。聊天摘要默认关闭；开启后会把限定条数和长度的最近聊天内容交给当前配置的模型生成短句。

### 提醒模型改名片

`common.operation_mode=tool_reminder`。到提醒时间后，下一次群消息进入模型请求时，插件提醒模型调用 `set_dynamic_group_card`。没有可用名片工具的请求不会注入提醒。

`reminder_sources` 可多选 `thought`（最近聊天）、`schedule`（日程）、`whim`（随心心情）。模型优先直接给出后缀；没有传后缀时使用规则或候选池，不在提醒工具内部再调用一次模型。日程提醒本身不读取外部日历。

一次工具调用只维护当前群里当前机器人自己的名片。`llm_tool.min_interval_seconds` 限制成功后的更新频率；失败则使用通用配置中的失败冷却。

### 无人聊天时定时更新

提醒模式下把 `tool_reminder_mode.trigger_mode` 设为 `active_agent_cron`。需要 AstrBot 支持主动任务、配置可用模型，并先在目标群产生一次消息。插件重载后需要再次有群事件来登记目标。

`active_cron_expression` 使用五段 cron：

| 表达式 | 含义 |
| --- | --- |
| `*/30 * * * *` | 每半小时 |
| `0 */2 * * *` | 每两小时的整点 |
| `0 8 * * *` | 每天 08:00 |

留空时，插件从提醒间隔换算 cron；秒数向上取整到分钟。只接受能用 cron 表达的等间隔，例如 10、15、30 分钟、2 小时、24 小时。90 分钟等间隔不能直接这样换算，请自行填写明确的 cron；插件不会悄悄改成每半小时。

主动任务会请求模型，可能产生模型费用或群回复。时间以 AstrBot 调度器的时区设置为准。多个 QQ 账号共用同一平台连接且会话来源相同时，主动任务无法唯一判断目标，会拒绝修改；请为账号配置独立连接。

## 名片模板

两种模式分别使用 `auto_update_mode.card_template` 和 `tool_reminder_mode.card_template`。基础名字来自 `card_fields.bot_name`。

```text
{bot_name} {manual_suffix}
{bot_name} | {time} | {schedule_suffix}
{bot_name} CPU:{cpu}% MEM:{memory}%
```

| 变量 | 内容 |
| --- | --- |
| `{bot_name}` | 基础名字 |
| `{manual_suffix}` | 工具设置的短后缀 |
| `{static_suffix}` | 配置中的固定后缀 |
| `{thought_suffix}` | 最近聊天的想法摘要 |
| `{schedule_suffix}` | 日程短句 |
| `{whim_suffix}` | 随心短句 |
| `{suffixes}` | 用空格连接可用后缀，跳过已单独放进模板的项和与工具后缀相同的项 |
| `{cpu_text}`、`{memory_text}`、`{time_text}` | 按各自字段模板生成的文本，受对应开关控制 |
| `{metrics}` | 用空格连接以上三种文本 |
| `{cpu}`、`{memory}` | 不带百分号的数值 |
| `{time}`、`{date}`、`{weekday}` | 系统本地时间、日期、中文星期 |

提醒模式每次设置后缀会清理上一轮动态后缀，不会越叠越长。自动模式是否加入动态来源，由 `include_thought_summary` 等开关控制。直接写 `{cpu}`、`{memory}`、`{time}` 不受文本字段开关控制。

最终名片同时受字符数上限和 UTF-8 字节上限约束，采用先到达的上限。默认字节预算为 60，这只是保守设置，不代表所有 QQ 客户端都保证接受；调整后仍应通过回读和客户端日志确认。

## 日程规则与随心短句

`daily_schedule.mode=rules` 时，在 `schedule_lines` 每行填写一条规则：

```text
2026-10-10=整理项目
10-10=纪念日
周一=整理周计划
周五=准备周末
daily=自由活动
```

支持具体日期、每年日期、星期和每天。相同类型按列表先后匹配；具体日期优先于每年日期，再到星期和每天。内容可包含 `{date}`、`{time}`、`{weekday}`。没有命中时用 `empty_text`。

`daily_schedule.mode=llm` 会让模型生成短句，失败后使用规则。`whim_suffix.mode=pool` 从候选池选择；`llm` 则优先由模型生成，失败后回退到候选池。`llm.provider_id` 留空使用当前会话的模型。

## 工具参数

普通用户直接用自然语言提出要求即可。以下是给配置人格或调试工具的用户看的参数：

| 参数 | 用途 |
| --- | --- |
| `mode=suffix` | 按当前模式的模板设置短后缀 |
| `mode=full_card` | 直接设置完整名片，需要开启 `llm_tool.allow_full_card` |
| `mode=clear_manual` | 清理手动内容并重新按模板更新；不是恢复安装前的名片 |
| `suffix` / `full_card` | 对应模式的文本 |
| `source` | `manual`、`thought`、`schedule`、`whim` 或 `random` |
| `duration_seconds` | 手动内容的有效期；省略使用默认值，0 表示直到被替换、清除或插件重载 |
| `reason` | 可选的修改理由 |

有效期只决定下次渲染时是否继续使用手动内容，不会在到期瞬间自动唤醒机器人改名。失败或参数不合法时，不提交新的手动内容和有效期。

## 参数查阅

所有选项都能在 AstrBot 插件配置中编辑。完整字段、默认值和说明见[配置字段表](settings-reference.md)。日常使用通常只需修改基础名字、运行模式、名片模板和更新间隔。
