# Changelog

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
