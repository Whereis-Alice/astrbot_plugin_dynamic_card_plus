# 平台兼容性

插件使用 **OneBot v11**，从 AstrBot 的 **aiocqhttp** 连接取得客户端。Windows 和 Linux 使用同一套插件代码；不需要设置 QQ 客户端安装路径。

| 客户端 | 接入方式与核对结果 |
| --- | --- |
| LLOneBot / llbot（LuckyLilliaBot） | 使用 OneBot v11；已核对改名片和成员信息接口源码 |
| SnowLuma | 使用其 OneBot v11 接口；已核对改名片和错误返回源码 |
| 其他 OneBot v11 实现 | 按标准接口调用，需要自行确认改名片与成员回读功能 |
| QQ 官方机器人 API、其他平台适配器 | 不适用 |

核对不等于真实 QQ 账号实测。当前验证包含本地 AstrBot SDK、接口返回模拟与回归测试，没有连接用户的 QQ 账号或 Linux 云服务器验证权限和平台限制。

## 使用的接口

- `set_group_card`：`group_id`、机器人自己的 `user_id`、`card`。
- `get_group_member_info`：同一群、同一机器人，附带 `no_cache=true`，用于回读和故障诊断。
- `get_version_info`：仅 `/名片检查` 使用。

调用通过 `client.call_action`，兼容提供 `client.api.call_action` 的封装。传入 `self_id` 供 aiocqhttp 路由到正确的机器人连接。QQ 号和群号转为正整数；不会把 QQ 号替换成客户端内部 UID，也不会调用不通用的私有接口。

支持 aiocqhttp 已解包的数据或完整 OneBot `status/retcode/data` 响应。空的成功数据 `None` 不会被误判为失败；完整失败响应不会被误判为成功。

## `1200` 的含义

llbot 的 WebSocket 分发层在接口执行抛出异常时返回 `retcode=1200`。它不是“某一种权限问题”的唯一编号。修改成员名片时，内部会先查 QQ 号对应的 UID，再调用 QQ 群名片接口；任何阶段的异常都可能进入这个返回分支。

LLOneBot/llbot 明确接受数字或字符串群号和 QQ 号，所以不能仅用“把字符串改成整数”解释这类报错。插件会回读实际名片、在必要时退避重试，并提供错误上下文；客户端内部故障、QQ 风控或内容限制仍需在对应客户端解决。

## 源码依据

核对日期：2026-10-08。链接固定到核对时的提交，便于后续复查。

- llbot：[SetGroupCard](https://github.com/LLOneBot/LuckyLilliaBot/blob/c7031f74ad160d4b56552f3872da2cad286de003/src/onebot11/action/group/SetGroupCard.ts)、[GetGroupMemberInfo](https://github.com/LLOneBot/LuckyLilliaBot/blob/c7031f74ad160d4b56552f3872da2cad286de003/src/onebot11/action/group/GetGroupMemberInfo.ts)、[BaseAction](https://github.com/LLOneBot/LuckyLilliaBot/blob/c7031f74ad160d4b56552f3872da2cad286de003/src/onebot11/action/BaseAction.ts)。
- SnowLuma：[群管理接口](https://github.com/SnowLuma/SnowLuma/blob/87527cb7641a5a42f8f0efb73cb066102e004dee/packages/onebot/src/actions/group-admin.ts)、[API 分发与错误返回](https://github.com/SnowLuma/SnowLuma/blob/87527cb7641a5a42f8f0efb73cb066102e004dee/packages/onebot/src/api-handler.ts)。
- AstrBot：[aiocqhttp 事件](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py)、[插件 Hook 注册](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/register/star_handler.py)。
