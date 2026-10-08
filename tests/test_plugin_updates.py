import asyncio
import copy
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiocqhttp.exceptions import ActionFailed
from astrbot.api import FunctionTool, ToolSet
from astrbot.api.message_components import Image, Plain

from dynamic_card_plugin.main import (
    CARD_CONTENT_FIELDS,
    DynamicCardPlusPlugin,
    _compact_json_schema,
)
from dynamic_card_plugin.onebot import CardUpdateResult


class FakeEvent:
    def __init__(self, *, self_id="456", platform="qq", origin=""):
        self.message_obj = SimpleNamespace(group_id="123", self_id=self_id)
        self.bot = SimpleNamespace(call_action=AsyncMock())
        self.unified_msg_origin = origin or f"{platform}:GroupMessage:123"
        self.platform = platform
        self.extra = {}
        self.message_str = "hello"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return self.platform

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def plain_result(self, text):
        return text

    def get_result(self):
        return None


def make_plugin(config=None):
    plugin = DynamicCardPlusPlugin(
        SimpleNamespace(add_llm_tools=lambda *args: None),
        config or {"common": {"operation_mode": "tool_reminder"}},
    )
    plugin._set_group_card = AsyncMock(return_value=CardUpdateResult(True, verified=True))
    return plugin


def state_for(plugin, event):
    return plugin._states[plugin._state_key(event, "123", event.message_obj.self_id)]


def content(state):
    return {key: getattr(state, key) for key in CARD_CONTENT_FIELDS}


@pytest.mark.parametrize("kwargs", [
    {"mode": "full_card", "full_card": "new"},
    {"mode": "suffix", "suffix": ""},
    {"mode": "unknown", "suffix": "new"},
    {"mode": "suffix", "suffix": "new", "source": "unknown"},
])
def test_rejected_tool_preserves_content_and_ttl(kwargs):
    plugin, event = make_plugin(), FakeEvent()
    state = state_for(plugin, event)
    state.manual_suffix, state.manual_until, state.thought_suffix = "old", time.time() + 500, "idea"
    before = content(state)
    reply = asyncio.run(plugin.handle_tool_call(event, kwargs))
    assert reply.startswith("失败") and content(state) == before
    plugin._set_group_card.assert_not_called()


def test_failed_tool_preserves_content_and_shares_failure_cooldown():
    plugin, event = make_plugin(), FakeEvent()
    state = state_for(plugin, event)
    state.manual_suffix, state.manual_until, state.whim_suffix = "old", 9999999999, "old thought"
    before = content(state)
    plugin._set_group_card.return_value = CardUpdateResult(False, attempts=3, detail="retcode=1200")
    reply = asyncio.run(plugin.handle_tool_call(event, {"mode": "suffix", "source": "whim", "suffix": "拒绝看怪东西", "duration_seconds": 0}))
    assert "1200" in reply and content(state) == before
    assert state.retry_after > time.time()
    asyncio.run(plugin.handle_tool_call(event, {"mode": "suffix", "suffix": "retry"}))
    assert plugin._set_group_card.await_count == 1
    plugin.config["common"]["operation_mode"] = "auto_update"
    asyncio.run(plugin.modify_card_before_send(event))
    assert plugin._set_group_card.await_count == 1


def test_cancelled_tool_preserves_content_and_releases_lock():
    plugin, event = make_plugin(), FakeEvent()
    state = state_for(plugin, event)
    state.manual_suffix = "old"
    before = content(state)
    plugin._set_group_card.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin.handle_tool_call(event, {"mode": "suffix", "suffix": "new"}))
    assert content(state) == before and not state.update_lock.locked()


def test_concurrent_calls_share_lock_and_success_cooldown():
    plugin, first, second = make_plugin(), FakeEvent(), FakeEvent()

    async def delayed_write(**kwargs):
        await asyncio.sleep(0.02)
        return CardUpdateResult(True, verified=True)

    plugin._set_group_card.side_effect = delayed_write

    async def concurrent():
        return await asyncio.gather(
            plugin.handle_tool_call(first, {"mode": "suffix", "suffix": "one"}),
            plugin.handle_tool_call(second, {"mode": "suffix", "suffix": "two"}),
        )

    replies = asyncio.run(concurrent())
    assert sum(reply.startswith("已把") for reply in replies) == 1
    assert plugin._set_group_card.await_count == 1
    assert state_for(plugin, first).manual_suffix == "one"


def test_platforms_and_accounts_have_independent_cards():
    plugin = make_plugin()
    events = [FakeEvent(), FakeEvent(self_id="789"), FakeEvent(platform="other")]
    for i, event in enumerate(events):
        reply = asyncio.run(plugin.handle_tool_call(event, {"mode": "suffix", "suffix": str(i)}))
        assert reply.startswith("已把")
    assert len(plugin._states) == 3
    assert [state_for(plugin, event).manual_suffix for event in events] == ["0", "1", "2"]
    assert plugin._known_group_context_from_event(events[0]) is None  # shared origin is ambiguous


def test_readonly_commands_never_trigger_auto_update():
    plugin = make_plugin({"common": {"operation_mode": "auto_update"}})
    event = FakeEvent()
    event.bot.call_action.side_effect = [{"app_name": "SnowLuma", "app_version": "test"}, {"card": "current"}]

    async def collect():
        preview = [text async for text in plugin.preview_card(event, "午睡中")]
        await plugin.modify_card_before_send(event)
        diagnostic = [text async for text in plugin.diagnose_card(event)]
        await plugin.modify_card_before_send(event)
        return preview, diagnostic

    preview, diagnostic = asyncio.run(collect())
    assert "未修改" in preview[0] and "SnowLuma" in diagnostic[0]
    plugin._set_group_card.assert_not_called()
    assert all(call.args[0] != "set_group_card" for call in event.bot.call_action.call_args_list)


def test_missing_tool_does_not_inject_an_impossible_reminder():
    plugin, event = make_plugin(), FakeEvent()
    request = SimpleNamespace(extra_user_content_parts=[], func_tool=ToolSet(tools=[]))
    asyncio.run(plugin.inject_group_card_tool_hint(event, request))
    assert not request.extra_user_content_parts and not plugin._reminder_bindings


def test_compaction_preserves_business_parameter_names():
    schema = {"type": "object", "description": "long", "properties": {
        "description": {"type": "string", "description": "help"},
        "default": {"type": "string"}, "title": {"type": "object", "properties": {
            "examples": {"type": "string"},
        }},
    }}
    before = copy.deepcopy(schema)
    compact = _compact_json_schema(schema)
    assert set(compact["properties"]) == {"description", "default", "title"}
    assert "examples" in compact["properties"]["title"]["properties"]
    assert "description" not in compact and schema == before


def test_draft_cleanup_scoped_to_event_and_keeps_images():
    plugin, own, other = make_plugin(), FakeEvent(), FakeEvent()
    own.set_extra("dynamic_card_completed_at", time.time())
    draft = "思考过程：hidden\n回复草稿：午安。\n字数校验：OK"
    image = Image.fromURL("https://example.com/test.png")
    response = SimpleNamespace(completion_text=draft, result_chain=SimpleNamespace(chain=[Plain(draft), image]))
    asyncio.run(plugin.sanitize_group_card_tool_followup(other, response))
    assert response.completion_text == draft
    asyncio.run(plugin.sanitize_group_card_tool_followup(own, response))
    assert response.completion_text == "午安。" and image in response.result_chain.chain


def test_cron_daily_and_unrepresentable_interval():
    plugin = make_plugin({"tool_reminder_mode": {"reminder_interval_seconds": 86400}})
    assert plugin._active_cron_expression(plugin._settings()) == "0 0 * * *"
    plugin.config["tool_reminder_mode"]["reminder_interval_seconds"] = 5400
    with pytest.raises(ValueError, match="cron"):
        plugin._active_cron_expression(plugin._settings())


def test_only_one_class_tool_registered():
    captured = []
    DynamicCardPlusPlugin(SimpleNamespace(add_llm_tools=lambda *tools: captured.extend(tools)))
    assert len(captured) == 1 and isinstance(captured[0], FunctionTool)
    assert captured[0].name == "set_dynamic_group_card"


@pytest.mark.parametrize("lines,expected", [
    (["周四=每周", "10-08=每年", "2026-10-08=当天"], "当天"),
    (["周四=每周", "10-08=每年"], "每年"),
    (["daily=每天", "周四=每周"], "每周"),
    (["daily=每天", "周五=每周"], "每天"),
])
def test_schedule_specific_dates_beat_weekly_rules(lines, expected):
    assert make_plugin()._select_schedule_line(lines, datetime(2026, 10, 8)) == expected


def test_invalid_template_does_not_write_literal_placeholder():
    plugin = make_plugin({"tool_reminder_mode": {"card_template": "{typo}"}, "common": {"operation_mode": "tool_reminder"}})
    reply = asyncio.run(plugin.handle_tool_call(FakeEvent(), {"mode": "suffix", "suffix": "hello"}))
    assert reply.startswith("失败")
    plugin._set_group_card.assert_not_called()


def test_reported_log_scenario_through_real_transport():
    plugin, event = make_plugin(), FakeEvent()
    plugin._set_group_card = DynamicCardPlusPlugin._set_group_card.__get__(plugin)
    event.bot.call_action.side_effect = [
        ActionFailed({"status": "failed", "retcode": 1200, "message": "", "wording": ""}),
        {"card": "AstrBot 拒绝看怪东西", "user_id": 456, "group_id": 123},
    ]
    reply = asyncio.run(plugin.handle_tool_call(event, {
        "duration_seconds": 0, "mode": "suffix", "reason": "群名片自主管理提醒",
        "source": "whim", "suffix": "拒绝看怪东西",
    }))
    assert reply.startswith("已把当前群名片改为：AstrBot 拒绝看怪东西")
    state = state_for(plugin, event)
    assert state.last_verified and state.manual_until == 0
    assert state.manual_suffix == "拒绝看怪东西"


def test_real_transport_failure_keeps_old_card(monkeypatch):
    monkeypatch.setattr("dynamic_card_plugin.onebot.asyncio.sleep", AsyncMock())
    plugin, event = make_plugin(), FakeEvent()
    plugin._set_group_card = DynamicCardPlusPlugin._set_group_card.__get__(plugin)
    state = state_for(plugin, event)
    state.manual_suffix, state.last_card = "原后缀", "AstrBot 原后缀"
    event.bot.call_action.side_effect = [
        ActionFailed({"retcode": 1200, "message": ""}), {"card": "AstrBot 原后缀"},
    ] * 3
    reply = asyncio.run(plugin.handle_tool_call(event, {"mode": "suffix", "suffix": "新后缀"}))
    assert reply.startswith("失败") and "1200" in reply
    assert state.manual_suffix == "原后缀" and state.last_card == "AstrBot 原后缀"
    assert state.last_tool_update_at == 0
