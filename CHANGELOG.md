# Changelog

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
