# 开发与验证

## 结构

- `main.py`：AstrBot 配置、模板、命令、LLM 工具与提醒生命周期。
- `onebot.py`：接口封装、字节限制、超时、回读与退避重试。
- `_conf_schema.json`：插件配置面板。
- `tests/`：使用 SDK 与模拟客户端验证行为，不连接 QQ。
- `assets/logo.svg`：可编辑 Logo；根目录 `logo.png` 用于插件列表和 README。

## 设计约束

只修改事件中机器人自己的群名片。状态和锁按平台连接 ID、机器人 QQ 号、群号组合隔离；同一目标的构建、写入与状态提交串行执行。

工具先在候选状态上构建名片，验证和写入完成后才提交后缀、有效期与生成内容；失败、拒绝或取消不会覆盖原内容。失败冷却单独记录，不伪造成功时间。

使用 class-based `FunctionTool` 单次注册。提醒绑定具体请求：首轮只向模型提供名片工具，后续恢复可执行的工具集并精简冗余 schema，Agent 结束后恢复原始工具。`skills_like` 模式跳过门控。清理只作用于本插件的临时提醒，卸载时也会还原请求工具集。

schema 精简保留业务参数名，避免把名为 `description`、`default` 等字段误删。名片完成后的输出清理绑定具体事件，保留图片等非文本组件。

群记录、近期消息与手动后缀仅放在内存，不在插件代码目录写运行状态。当前不持久化临时后缀，也不承诺重启后无人消息时自动恢复目标。

## 运行检查

```bash
python -m pip install astrbot pytest ruff -r requirements.txt
python -m pytest -q
python -m ruff check .
python -m compileall -q main.py onebot.py
```

测试覆盖 `1200` 但实际已生效、持续失败、HTTP/WS 包装差异、权限与参数拒绝、超时、取消、延迟可见、回读不可用、中文/表情字节限制、失败状态回滚、并发更新、账号隔离、只读命令、提醒清理与 schema 参数保留。

本地验证使用 AstrBot SDK 4.25.0；目标日志中的 4.28.2 已对照其 Hook、aiocqhttp 与主动任务接口源码。CI 在 Linux 上运行 SDK 回归。模拟测试不覆盖真实账号的 QQ 权限、内容审核、风控或客户端内部 UID 缓存行为。

## Logo

深蓝底色、青绿机器人名片、环形更新箭头与金色星光，表达“机器人的状态随聊天更新”。不含平台商标。PNG 为 SVG 的 512 × 512 渲染结果。
