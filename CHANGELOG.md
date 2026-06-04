# Changelog

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
