import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiocqhttp.exceptions import ActionFailed

from dynamic_card_plugin.onebot import OneBotCardClient, clean_card


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_wait(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("dynamic_card_plugin.onebot.asyncio.sleep", sleep)
    return sleep


def test_error_1200_but_card_already_applied_does_not_repeat_write(no_wait):
    api = AsyncMock(side_effect=[
        ActionFailed({"status": "failed", "retcode": 1200, "message": "", "wording": ""}),
        {"card": "AstrBot 拒绝看怪东西", "user_id": 456, "group_id": 123},
    ])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update(
        "123", "AstrBot 拒绝看怪东西",
    ))
    assert result.ok and result.verified and result.attempts == 1
    assert [call.args[0] for call in api.call_args_list] == ["set_group_card", "get_group_member_info"]
    assert all(call.kwargs["self_id"] == "456" for call in api.call_args_list)
    assert api.call_args_list[0].kwargs["user_id"] == 456
    assert api.call_args_list[1].kwargs["no_cache"] is True
    no_wait.assert_not_called()


def test_1200_readback_mismatch_retries_with_backoff(no_wait):
    api = AsyncMock(side_effect=[
        ActionFailed({"retcode": 1200, "message": ""}), {"card": "old"},
        {"status": "failed", "retcode": 1200, "wording": ""}, {"card": "old"},
        {"status": "ok", "retcode": 0, "data": None}, {"card": "new"},
    ])
    result = run(OneBotCardClient(SimpleNamespace(api=SimpleNamespace(call_action=api)), "456").update("123", "new"))
    assert result.ok and result.verified and result.attempts == 3
    assert [call.args[0] for call in no_wait.call_args_list] == [2, 4]


def test_repeated_1200_is_not_reported_as_success(no_wait):
    api = AsyncMock(side_effect=[ActionFailed({"retcode": 1200}), {"card": "old"}] * 3)
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new"))
    assert not result and result.attempts == 3
    assert "1200" in result.detail and "客户端未提供原因" in result.detail


@pytest.mark.parametrize("retcode,message", [(1400, "bad parameter"), (1200, "没有权限")])
def test_permanent_error_stops_after_readback(retcode, message, no_wait):
    api = AsyncMock(side_effect=[{"status": "failed", "retcode": retcode, "message": message}, {"card": "old"}])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new"))
    assert not result and result.attempts == 1
    no_wait.assert_not_called()


def test_success_ack_but_readback_mismatch_is_failure(no_wait):
    api = AsyncMock(side_effect=[None, {"card": "old"}, {"card": "old"}])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new", attempts=1))
    assert not result and "仍不一致" in result.detail


def test_eventually_consistent_success(no_wait):
    api = AsyncMock(side_effect=[None, {"card": "old"}, {"card": "new"}])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new"))
    assert result.ok and result.verified and result.attempts == 1


def test_missing_read_api_is_acknowledged_but_unverified(no_wait):
    api = AsyncMock(side_effect=[None, ActionFailed({"retcode": 1404})])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new"))
    assert result.ok and not result.verified and "尚未确认" in result.detail


def test_timeout_is_bounded_and_cancellation_propagates():
    async def slow(*args, **kwargs):
        await asyncio.sleep(0.5)

    result = run(OneBotCardClient(SimpleNamespace(call_action=slow), "456", timeout=0.01).update("123", "new", attempts=1))
    assert not result and "超时" in result.detail

    api = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new"))


@pytest.mark.parametrize("group,self_id", [("", "456"), ("123", "bad"), ("-1", "456"), ("123", "0")])
def test_invalid_identifiers_never_reach_api(group, self_id):
    api = AsyncMock()
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), self_id).update(group, "new"))
    assert not result
    api.assert_not_called()


def test_wrong_account_readback_never_verifies(no_wait):
    api = AsyncMock(side_effect=[ActionFailed({"retcode": 1200}), {"card": "new", "user_id": 999}])
    result = run(OneBotCardClient(SimpleNamespace(call_action=api), "456").update("123", "new", attempts=1))
    assert not result


def test_chinese_emoji_and_controls_respect_bytes():
    card = clean_card("AstrBot\x00 拒绝看怪东西 🌈🌈🌈" * 3, 60, 60)
    assert len(card.encode("utf-8")) <= 60
    assert "\x00" not in card and "\ufffd" not in card
    assert clean_card("A\ud800\u202eB", 60, 60) == "AB"
    assert clean_card("👩‍💻", 60, 60) == "👩‍💻"
