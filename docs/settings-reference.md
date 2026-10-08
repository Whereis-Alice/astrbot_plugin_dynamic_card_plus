# 配置字段表

配置面板中的分组与字段如下。只需填写需要改变的选项，其余保留默认值。

## 通用配置（`common`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | true | 启用插件 |
| `operation_mode` | "auto_update" | 运行模式。auto_update：到间隔后，在机器人下一次回复时更新；tool_reminder：由模型调用工具更新。未选模式的配置不会生效。 可选：`auto_update`、`tool_reminder`。 |
| `max_card_length` | 60 | 群名片最大长度。字符数上限；同时受 UTF-8 字节上限约束，中文和表情会占多个字节。 |
| `retry_count` | 3 | 每轮最多尝试次数（包含首次） |
| `debug_log` | false | 启用调试日志 |
| `blacklist_group_ids` | [] | 群黑名单。填 QQ 群号。命中的群不会自动改名片，也不能用 LLM 工具改。 |
| `blacklist_unified_origins` | [] | 会话黑名单。填完整 unified_msg_origin，可用于更细粒度禁用。 |
| `max_card_bytes` | 60 | 名片 UTF-8 字节上限。默认 60 是保守值，中文通常占 3 字节，表情可能更多。自动截短，不切坏 Unicode 字符；遇到限制请缩短名片。 |
| `api_timeout_seconds` | 10 | 单次接口超时秒数 |
| `retry_delay_seconds` | 2 | 首次重试等待秒数。后续等待逐次翻倍，最长 30 秒；明确的权限或参数错误不重复提交。 |
| `failure_cooldown_seconds` | 300 | 失败后暂停改名片秒数。自动更新和工具共用，避免每条消息都重复触发失败。 |
| `verify_after_write` | true | 修改后回读确认名片。通过 get_group_member_info 检查实际名片。接口报错时也会回读，避免已生效却误报失败。 |

## 通用名片字段（`card_fields`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `bot_name` | "AstrBot" | 机器人基础名字 |
| `static_suffix` | "" | 固定后缀。会进入 {suffixes}，也可以直接用 {static_suffix} 放到完整模板里。 |
| `include_cpu` | true | 生成 CPU 文本。关闭后 {cpu_text} 为空；{cpu} 数值仍可在完整模板中直接使用。 |
| `cpu_template` | "CPU {cpu}%" | CPU 文本模板 |
| `include_memory` | true | 生成内存文本。关闭后 {memory_text} 为空；{memory} 数值仍可在完整模板中直接使用。 |
| `memory_template` | "MEM {memory}%" | 内存文本模板 |
| `include_time` | true | 生成时间文本。关闭后 {time_text} 为空；{time} 仍可在完整模板中直接使用。 |
| `time_template` | "{time}" | 时间文本模板 |

## 模式：自动改群名片（`auto_update_mode`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `update_interval_seconds` | 60 | 自动改名片间隔秒数 |
| `card_template` | "{bot_name} {cpu_text} {memory_text} {time_text} {suffixes}" | 自动模式完整名片模板。仅 common.operation_mode=auto_update 时生效。最终排版只由这个模板决定。 |
| `include_thought_summary` | false | 自动模式包含会话想法摘要 |
| `include_daily_schedule` | false | 自动模式包含当天日程 |
| `include_whim_suffix` | false | 自动模式包含随心后缀 |

## 模式：提醒 bot 主动用工具改名片（`tool_reminder_mode`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `reminder_interval_seconds` | 1800 | 提醒间隔秒数。仅 common.operation_mode=tool_reminder 时生效。到点后，插件会在 LLM 请求中提醒 bot 可主动调用工具。 |
| `card_template` | "{bot_name} {manual_suffix}" | 提醒工具模式完整名片模板。仅 common.operation_mode=tool_reminder 时生效。自然语言要求 bot 改名片、或提醒后 bot 主动调用工具时都会使用这个模板。推荐用 {bot_name} {manual_suffix}，每次工具设置后缀都会替换上一轮工具/动态后缀状态，避免叠加。 |
| `inject_status_hint` | true | 定时注入工具提醒。仅 trigger_mode=llm_request 时用于注入工具提醒。 |
| `trigger_mode` | "llm_request" | 提醒触发方式。llm_request=到间隔后，在下一次 LLM 请求里提醒 bot 调用工具；active_agent_cron=为已记录群注册 AstrBot 主动任务，到点唤醒 bot，让 bot 自己调用 LLM 工具改名片。 可选：`llm_request`、`active_agent_cron`。 |
| `reminder_sources` | ["thought", "schedule", "whim"] | 提醒时可用的后缀来源。可多选。到点提醒时会从已选来源中随机建议一个；想要三选二随机，就只保留其中两项。留空时按三项全选处理。提醒会要求 bot 直接填写 suffix；漏传时才按对应动态来源兜底。thought=当前会话想法；schedule=当天日程；whim=随心所欲。 可选：`thought`、`schedule`、`whim`。 |
| `active_cron_expression` | "" | 主动任务 cron 表达式。仅 trigger_mode=active_agent_cron 时生效。留空时会根据 reminder_interval_seconds 自动换算为分钟级 cron；例如 1800 秒会变成 */30 * * * *。也可以手动填 5 段 cron 表达式。 |

## 动态来源/兜底：当前会话想法摘要（`thought_summary`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `refresh_seconds` | 1800 | 想法摘要刷新间隔秒数 |
| `prefix` | "想法:" | 想法后缀前缀 |
| `prompt` | "请根据最近对话，为自己生成一个适合作为 QQ 群名片后缀的当前想法。只输出后缀本身，不要解释，不要加引号。" | 想法摘要兜底生成提示词 |
| `max_length` | 12 | 想法后缀最大长度 |
| `context_messages` | 8 | 用于摘要的最近消息条数 |
| `context_message_max_chars` | 120 | 每条上下文最多字符数 |

## 动态来源/兜底：当天日程（`daily_schedule`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | "rules" | 当天日程来源。rules=按下面的一周/日期规则匹配；llm=让 bot/LLM 生成当天日程后缀，失败时回落到规则和兜底文本。tool_reminder 提醒会优先要求 bot 直接填写 suffix，漏传时才用这里兜底。 可选：`rules`、`llm`。 |
| `refresh_seconds` | 3600 | 日程后缀刷新间隔秒数 |
| `prefix` | "日程:" | 日程后缀前缀 |
| `prompt` | "请为自己生成一个适合作为今天 QQ 群名片后缀的日程状态。只输出后缀本身，不要解释，不要加引号，尽量短。" | LLM 日程兜底生成提示词。仅 mode=llm 时优先使用。可用变量：{date}、{time}、{weekday}。 |
| `schedule_lines` | ["周一=整理周计划", "周二=推进待办", "周三=补充能量", "周四=检查进度", "周五=准备周末模式", "周六=自由活动", "周日=慢慢充电"] | 日程规则。支持 YYYY-MM-DD=内容、MM-DD=内容、星期一=内容、周一=内容、daily=内容。内容可用 {date}、{time}、{weekday}。 |
| `empty_text` | "自由活动" | 没有命中日程时的兜底文本 |
| `max_length` | 18 | 日程后缀最大长度 |

## 动态来源/兜底：随心后缀（`whim_suffix`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `refresh_seconds` | 900 | 随心后缀刷新间隔秒数 |
| `prefix` | "" | 随心后缀前缀 |
| `mode` | "pool" | 随心后缀兜底来源。tool_reminder 提醒会优先要求 bot 直接填写 suffix；这里主要用于自动模式和漏传 suffix 时兜底。pool 从候选池随机选；llm 调用模型生成，失败时回退到候选池。 可选：`pool`、`llm`。 |
| `pool` | ["今天也在发光", "慢慢加载灵感", "有一点点开心", "正在观察世界"] | 随心后缀候选池 |
| `prompt` | "请随心所欲地生成一个适合作为自己 QQ 群名片后缀的短句。只输出后缀本身，不要解释，不要加引号，长度很短。" | LLM 随心后缀兜底生成提示词 |
| `max_length` | 12 | 随心后缀最大长度 |

## LLM 生成设置（`llm`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `provider_id` | "" | 指定用于生成动态后缀的模型供应商。留空时使用当前会话正在使用的 Chat Provider。 |

## LLM 工具通用配置（`llm_tool`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | true | 启用 LLM 改名片工具 |
| `description` | "修改当前 QQ 群里的群名片。可以设置一个短后缀表达此刻想法、心情、日程状态。source=manual、thought、schedule、whim 时可以直接传 suffix；source=random 或漏传 suffix 时，工具才会按对应来源兜底生成后缀。短后缀会替换上一轮工具后缀，不要把旧后缀拼进新后缀里。在配置允许时也可以直接给出完整名片。" | 工具说明。这段说明会给 LLM 看，用来决定什么时候调用 set_dynamic_group_card。 |
| `min_interval_seconds` | 30 | LLM 工具最小调用间隔秒数 |
| `max_length` | 18 | LLM 工具手动后缀最大长度 |
| `allow_full_card` | false | 允许 LLM 工具直接设置完整名片。关闭时，LLM 只能设置后缀，基础名片仍按当前运行模式的完整名片模板生成。 |
| `manual_ttl_seconds` | 1800 | LLM 工具手动内容保留秒数。0 表示一直保留，直到 clear_manual、插件重载或配置变化。 |
