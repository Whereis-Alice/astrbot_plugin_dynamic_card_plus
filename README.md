<div align="center">
  <img src="logo.png" width="128" alt="灵动名片 Logo">
  <h1>灵动名片</h1>
  <p>让机器人的 QQ 群名片，显示此刻的心情、日程或运行状态。</p>
</div>

比如 `爱乃 整理日程中`，也可以显示时间、CPU 和内存。插件只修改**机器人自己的群名片**，不会修改群名称或其他成员的名片。

## 安装

1. 在 AstrBot 中通过 **OneBot v11 / aiocqhttp** 接入 QQ，并确认机器人可以正常收发群消息。支持使用这套接口的 LLOneBot、llbot 和 SnowLuma；[兼容性说明](docs/compatibility.md)列出了核对范围。
2. 打开 AstrBot 的插件管理，选择从链接安装，填入：

   ```text
   https://github.com/Whereis-Alice/astrbot_plugin_dynamic_card_plus
   ```

3. 打开插件配置，在「通用名片字段」中填写机器人的基础名字，再选择下面的一种运行方式。

## 选择运行方式

| 想要的效果 | 「通用配置 → 运行模式」选择 | 何时更新 |
| --- | --- | --- |
| 自动显示时间、运行状态或日程 | `auto_update` | 到达设置的间隔后，在机器人下一次回复群消息时更新 |
| 让机器人根据聊天自己想一个后缀 | `tool_reminder` | 到达提醒间隔后，在下一次模型请求中提醒机器人使用工具 |

**想要“名字 + 心情”**：选择 `tool_reminder`，保留默认名片模板 `{bot_name} {manual_suffix}`，并在当前人格的工具设置中启用 `set_dynamic_group_card`。模型需要支持工具调用。

然后在群里对机器人说：

> 把你的群名片后缀改成“整理日程中”。

也可以说“根据刚才聊的内容，给自己换个名片后缀”。每次设置都会替换上一次工具后缀。

如果希望**群里没人说话时也能定时更新**，在提醒模式里选择 `active_agent_cron`，并先在该群聊一次，让插件记录目标。[设置方法](docs/configuration.md#无人聊天时定时更新)

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `/名片预览` | 查看当前模板的效果，不执行改名 |
| `/名片预览 午睡中` | 试放一个后缀，查看长度和排版 |
| `/名片检查` | AstrBot 管理员查看客户端、当前名片和最近的错误，不执行改名 |

如果你设置了其他命令前缀，请把 `/` 换成自己的前缀。自动生成的内容在预览中使用已有缓存，不额外请求模型。

## 使用时留意

- 名片太长会被截短；中文和表情占用的字节更多，建议保持简短。
- 修改失败后会暂缓重试，避免连续请求。出现 `1200` 时先用 `/名片检查`，再参考[排障说明](docs/troubleshooting.md)。
- 时间和日程使用 AstrBot 所在系统或容器的时区。临时后缀、冷却状态和群记录在插件重载后清空。

## 更多说明

[配置与模板](docs/configuration.md) · [兼容性](docs/compatibility.md) · [常见问题](docs/troubleshooting.md) · [开发说明](docs/development.md) · [更新日志](changelog.md)

基于 [botName](https://github.com/zgojin/astrbot_plugin_botName)；来源与许可说明见 [NOTICE.md](NOTICE.md)。
