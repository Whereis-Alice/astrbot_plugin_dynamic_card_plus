# astrbot_plugin_dynamic_card_plus

增强版动态群名片插件。它会在 bot 于 QQ 群聊发言时，按配置更新自己的群名片，并允许 LLM 主动调用工具修改名片后缀。

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

- 自动动态群名片：CPU、内存、当前时间。
- 可配置模板：完整名片、系统指标、分隔符、固定后缀都能在配置面板里改。
- 当前想法后缀：按频率汇总当前会话最近消息，让模型生成一个短后缀。
- 当天日程后缀：按日期、星期或 daily 规则选择当天日程。
- 随心后缀：可从候选池随机，也可让模型随心生成。
- LLM 主动改名片：注册 `set_dynamic_group_card` 工具，bot 可以自己设置短后缀、清除手动内容，或在允许时直接设置完整名片。
- 群黑名单：禁止指定群或指定 `unified_msg_origin` 使用本插件。

## 适用范围

当前只支持 `aiocqhttp` 的 QQ 群聊，并通过 OneBot API `set_group_card` 修改 bot 自己的群名片。bot 需要在群内拥有修改自己群名片的权限。

## 依赖

```bash
pip install -r requirements.txt
```

依赖项：

- `psutil`

## 配置概要

### general

- `enabled`：总开关。
- `update_interval_seconds`：自动更新名片的最小间隔。
- `max_card_length`：最终名片最大长度。
- `blacklist_group_ids`：群号黑名单。
- `blacklist_unified_origins`：完整会话 ID 黑名单。

### base_card

默认模板：

```text
{bot_name} {metrics} {suffixes}
```

常用变量：

```text
{bot_name} {metrics} {suffixes}
{cpu} {memory} {time} {date} {weekday}
{manual_suffix} {thought_suffix} {schedule_suffix} {whim_suffix} {static_suffix}
```

例如：

```text
{bot_name} | {metrics} | {suffixes}
```

### thought_summary

启用后，插件会记录当前会话最近几条用户消息和 bot 回复，达到 `refresh_seconds` 后调用 LLM 生成一个很短的“当前想法”后缀。

### daily_schedule

`schedule_lines` 支持这些写法：

```text
2026-06-04=今晚整理插件
06-04=纪念日模式
星期四=周四日程
周五=准备周末模式
daily=自由活动
```

日程内容里可用 `{date}`、`{time}`、`{weekday}`。

### whim_suffix

- `mode=pool`：从候选池随机选择。
- `mode=llm`：让模型生成，失败时回退到候选池。

### llm_tool

工具名：`set_dynamic_group_card`

工具参数：

- `mode=suffix`：设置短后缀。
- `mode=full_card`：设置完整群名片，需要开启 `allow_full_card`。
- `mode=clear_manual`：清除 LLM 工具设置的手动内容。
- `duration_seconds`：手动内容保留时间，`0` 表示一直保留直到清除或插件重载。
- `reason`：可选，说明为什么要修改。

开启 `inject_status_hint` 后，插件会在 LLM 请求中提示 bot 当前群名片和工具可用性。工具调用成功后，工具结果会返回“已把当前群名片改为……”，因此模型会知道这次名片是自己主动修改的。

## 更新记录

### v0.1.0

- fork 上游 `astrbot_plugin_botName` 并完成插件 ID、目录名、元数据与注册标识符改名。
- 新增动态想法、当天日程、随心后缀。
- 新增 LLM 工具 `set_dynamic_group_card`。
- 新增群黑名单和会话黑名单。
- 配置改为分组 schema，所有主要行为可配置。
