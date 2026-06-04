# Changelog

## v0.8.6

- 提醒提示改为只注入临时 `extra_user_content_parts`，不再写入 `system_prompt`。
- debug 日志中的提醒提示通道改为 `channels=temp_user_content`。

## v0.8.5

- `llm_request` 提醒提示除写入 `system_prompt` 外，也会追加到临时 `extra_user_content_parts`，避免后续插件或 runner 覆盖 system prompt 后 bot 看不到提醒。
- debug 日志中标注提醒提示注入通道：`system_prompt,temp_user_content`。

## v0.8.4

- `common.debug_log=true` 时，`llm_request` 提醒注入会额外打印本轮实际注入的提醒提示词，方便排查模型为什么没有调用工具。
- `active_agent_cron` 注册主动任务时，debug 日志会打印实际写入 cron payload 的任务 note。

## v0.8.3

- 优化 `active_agent_cron` 的主动任务注册：从消息/LLM 请求链路中移出，改为后台异步串行注册，避免处理 agent 请求时直接抢 SQLite 锁。
- 遇到 `database is locked` 时会延迟重试，减少 `sqlite3.OperationalError: database is locked` 对当前对话请求的影响。
- 插件卸载时会取消尚未完成的 cron 注册任务，再清理已登记的主动任务。

## v0.8.2

- 强化定时触发提示的重复触发语义：每次 `llm_request` 提醒都会带本次触发编号。
- 提示词明确“这是新的独立触发，不是历史里已经完成的那一次”，避免模型因为上下文里已有上一次工具结果而跳过后续改名片。

## v0.8.1

- 收敛提醒注入日志：普通 info 日志只保留 group、source、has_tool、tool_count。
- 工具列表样本仅在 `common.debug_log=true` 或 `has_tool=false` 时输出，减少 293 个工具这类日志噪音。

## v0.8.0

- 将 LLM 工具说明与定时触发提示词彻底分离：工具说明只描述 `set_dynamic_group_card` 的能力，触发时由 `llm_request` / `active_agent_cron` 额外注入强制工具调用任务提示。
- 按用户提供文本优化触发提示词：明确“你现在需要调用工具修改当前 QQ 群名片”、工具调用前禁止输出自然语言，并加入“此功能是狐狸做给你玩的，允许随便使用”。
- `llm_request` 与 `active_agent_cron` 共用同一套强制工具调用提示，减少提示词不一致。

## v0.7.2

- 优化提醒注入日志：新增 `tool_count`，并在工具列表样本中优先显示 `set_dynamic_group_card`，避免 `has_tool=True` 但样本列表看不到目标工具造成误解。

## v0.7.1

- 修正默认 LLM 工具说明中“只在你确实想改名片时使用，不要每轮对话都调用”的冲突表述。
- 新说明改为“没有用户要求、也没有系统提示或主动任务时，不要随意调用”，避免抵消定时提醒的强制工具调用要求。

## v0.7.0

- 移除 `tool_reminder_mode.reminder_policy` 和 `suggest` 模式；`llm_request` 到提醒时间后一律注入强制工具调用提示。
- 强化 `llm_request` 提示词：要求下一条 assistant 行为必须调用 `set_dynamic_group_card`，工具调用前禁止输出自然语言，禁止把系统提示当聊天话题，禁止未调用工具却声称已修改。
- 工具说明与提醒提示统一为同一套工具调用协议，减少提示之间互相打架。
- 提醒注入日志新增 `has_tool` 和工具列表，方便判断当前请求是否真的带上 `set_dynamic_group_card`。
- 默认想法、日程、随心后缀生成提示词改为“为自己生成”，不再强调“机器人”。

## v0.6.0

- 移除插件直改名片的后台任务触发方式，避免绕过 bot 自己调用 LLM 工具。
- 提醒工具模式新增 `tool_reminder_mode.trigger_mode=active_agent_cron`：插件为已记录群注册 AstrBot 主动任务，到点后唤醒对应会话，并要求 bot 自己调用 `set_dynamic_group_card` 改群名片。
- 新增 `tool_reminder_mode.active_cron_expression`，可手动填写 5 段 cron 表达式；留空时会根据 `reminder_interval_seconds` 自动换算为分钟级 cron。
- LLM 工具的群上下文解析支持从 `unified_msg_origin` 回查已记录群，兼容主动任务唤醒时缺少普通群消息字段的情况。

## v0.4.3

- 提醒工具模式新增 `tool_reminder_mode.reminder_policy`，支持 `strong` 和 `suggest`。
- 默认提醒策略改为 `strong`：到提醒间隔后，会在本轮 LLM 请求里明确要求 bot 优先调用 `set_dynamic_group_card`，不再只是“如果你想改就改”的弱提示。
- 提醒注入日志新增 group、policy、source，方便从 AstrBot 日志判断提醒是否真的触发。
- LLM 工具说明补充：系统提示到达群名片自主管理提醒时间时，应优先调用工具。

## v0.4.2

- 提醒工具模式默认完整名片模板改为 `{bot_name} {manual_suffix}`，更适合“bot 主动改一次后缀”的使用方式。
- `tool_reminder` 模式下，LLM 工具每次 `mode=suffix` 调用都会先清理上一轮想法、日程、随心动态后缀状态，再写入新后缀，避免第二次改名片叠加旧后缀。
- `tool_reminder` 模式下，`mode=clear_manual` 也会同时清理上一轮动态后缀状态。
- README 补充提醒工具模式模板、自然语言主动改名片和 `source=random` 的实际渲染规则。

## v0.4.1

- 修复模板同时使用 `{manual_suffix}` 和 `{suffixes}` 时，工具后缀会重复显示的问题。
- 修复模板同时显式使用 `{manual_suffix}` 与 `{thought_suffix}` / `{schedule_suffix}` / `{whim_suffix}`，且两者原文相同时的重复显示问题。

## v0.4.0

- 当天日程新增 `daily_schedule.mode`，支持 `rules` 规则匹配和 `llm` 让 bot/LLM 生成日程后缀。
- 新增 `daily_schedule.prompt`，用于自定义 LLM 生成当天日程后缀的提示词。
- `daily_schedule.schedule_lines` 默认改为一周七天规则。
- `daily_schedule.empty_text` 默认改为 `自由活动`，作为规则未命中或 LLM 失败后的兜底文本。

## v0.3.0

- 修复 v4.25.2 中 LLM 工具返回 `ToolExecResult(...)` 会触发 `'types.UnionType' object is not callable` 的问题；工具现在返回普通文本，避免“名片实际已修改但模型收到工具错误”的情况。
- 重排配置结构，拆成 `common`、`card_fields`、`auto_update_mode`、`tool_reminder_mode`、动态来源、LLM 设置和 LLM 工具设置。
- 自动模式和提醒工具模式各自拥有独立完整名片模板。
- 明确 `common.operation_mode` 二选一：`auto_update` 自动改群名片，`tool_reminder` 定时提醒 bot 主动调用 LLM 工具。
- 移除模式配置里的职责冲突：通用名片字段只负责变量生成，模式完整模板负责最终排版。
- `{suffixes}` 会跳过与 LLM 工具后缀原文相同的动态后缀，避免出现“名片更新中 想法:名片更新中”这类重复。
- README 补充配置页无法按模式动态隐藏另一组配置的原因：AstrBot 插件 schema 目前只提供静态 `invisible`。

## v0.2.0

- 收敛群名片格式配置，移除兜底拼接分隔符、系统指标分隔符、动态后缀分隔符，避免和完整名片模板、各指标模板重复控制同一件事。
- 将默认完整名片模板改为 `{bot_name} {cpu_text} {memory_text} {time_text} {suffixes}`，让最终排版更直观。
- 新增 `{cpu_text}`、`{memory_text}`、`{time_text}` 变量；保留 `{metrics}` 和 `{suffixes}` 作为固定空格拼接的兼容便捷变量。
- 新增 `general.operation_mode`，支持 `auto_update` 自动改名片和 `tool_reminder` 定时提醒 bot 主动调用 LLM 工具两种互斥模式。
- 新增 `llm_tool.reminder_interval_seconds` 和 `llm_tool.reminder_source`，提醒模式可按会话想法、当天日程、随心后缀或三种随机生成建议后缀。
- 扩展 LLM 工具参数 `source`，支持 `manual`、`thought`、`schedule`、`whim`、`random`。
- README 更新配置说明，明确模板变量、模式选择和工具提醒行为。

## v0.1.0

- fork 上游 `zgojin/astrbot_plugin_botName`，并改名为 `astrbot_plugin_dynamic_card_plus`，避免与上游插件 ID、目录、注册类和数据路径冲突。
- 新增动态名片模板：支持 `{bot_name}`、`{metrics}`、`{suffixes}`、系统指标、日期时间和多种动态后缀变量。
- 新增当前会话想法摘要后缀：按配置频率汇总最近对话并生成短后缀。
- 新增当天日程后缀：支持按完整日期、月日、星期和 `daily` 规则选择日程。
- 新增随心后缀：支持候选池随机或 LLM 生成。
- 新增 LLM 工具 `set_dynamic_group_card`，让 bot 可以主动设置名片后缀、清除手动内容，或在配置允许时设置完整群名片。
- 新增群黑名单和 `unified_msg_origin` 黑名单。
- 将所有主要行为改为 `_conf_schema.json` 可配置项。
- README 补充完整模板变量说明，解释变量来源、示例和后缀拼接顺序。
