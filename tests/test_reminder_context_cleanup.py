import asyncio
import time
import unittest
from collections import defaultdict
from types import SimpleNamespace

from astrbot.api import FunctionTool, ToolSet
from astrbot.core.agent.message import Message, TextPart

from main import (
    CARD_HINT_MARKER,
    CARD_TOOL_NAME,
    DynamicCardPlusPlugin,
    GroupCardState,
    ReminderBinding,
)


class ReminderContextCleanupTests(unittest.TestCase):
    def make_plugin(self) -> DynamicCardPlusPlugin:
        plugin = object.__new__(DynamicCardPlusPlugin)
        plugin._states = defaultdict(GroupCardState)
        plugin._reminder_bindings = {}
        return plugin

    def make_binding(
        self,
        event: SimpleNamespace,
        group_id: str,
        hint: str,
    ) -> ReminderBinding:
        request_parts = [TextPart(text="other temporary context"), TextPart(text=hint)]
        request = SimpleNamespace(extra_user_content_parts=request_parts)
        run_context = SimpleNamespace(
            messages=[
                Message(
                    role="user",
                    content=[
                        TextPart(text="用户原始问题"),
                        TextPart(text=hint).mark_as_temp(),
                        TextPart(text="另一个插件内容"),
                    ],
                )
            ]
        )
        return ReminderBinding(
            event_id=id(event),
            event=event,
            unified_msg_origin=event.unified_msg_origin,
            group_id=group_id,
            trigger_id=f"{group_id}-trigger",
            injected_at=time.time() - 1,
            request=request,
            hint_part=request_parts[1],
            request_parts=request_parts,
            hint_text=hint,
            request_id=f"request-{group_id}",
            run_context=run_context,
            run_context_id=f"context-{group_id}",
        )

    def test_consuming_one_reminder_keeps_other_content_and_other_group(self) -> None:
        plugin = self.make_plugin()
        event_a = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        event_b = SimpleNamespace(unified_msg_origin="default:GroupMessage:10002")
        binding_a = self.make_binding(event_a, "10001", f"{CARD_HINT_MARKER} A")
        binding_b = self.make_binding(event_b, "10002", f"{CARD_HINT_MARKER} B")
        plugin._reminder_bindings[id(event_a)] = binding_a
        plugin._reminder_bindings[id(event_b)] = binding_b

        state = plugin._states["10001"]
        self.assertTrue(
            plugin._consume_pending_reminder(event_a, state, "10001", time.time())
        )
        self.assertTrue(binding_a.consumed)
        self.assertFalse(binding_b.consumed)
        self.assertIn(id(event_b), plugin._reminder_bindings)
        self.assertEqual(
            [part.text for part in binding_a.run_context.messages[0].content],
            ["用户原始问题", "另一个插件内容"],
        )
        self.assertEqual(
            [part.text for part in binding_a.request.extra_user_content_parts],
            ["other temporary context"],
        )
        self.assertTrue(
            any(
                CARD_HINT_MARKER in plugin._part_text(part)
                for part in binding_b.run_context.messages[0].content
            )
        )

    def test_agent_done_releases_consumed_binding(self) -> None:
        plugin = self.make_plugin()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        binding = self.make_binding(event, "10001", f"{CARD_HINT_MARKER} once")
        plugin._reminder_bindings[id(event)] = binding

        state = plugin._states["10001"]
        self.assertTrue(
            plugin._consume_pending_reminder(event, state, "10001", time.time())
        )
        asyncio.run(
            plugin.release_group_card_reminder(
                event,
                binding.run_context,
                SimpleNamespace(),
            )
        )
        self.assertNotIn(id(event), plugin._reminder_bindings)

    def test_initial_gate_restores_all_followup_tools(self) -> None:
        plugin = self.make_plugin()
        card_tool = FunctionTool(
            name=CARD_TOOL_NAME,
            description="card",
            parameters={"type": "object", "properties": {}},
        )
        song_tool = FunctionTool(
            name="play_song_by_name",
            description="song",
            parameters={"type": "object", "properties": {}},
        )
        image_tool = FunctionTool(
            name="send_image",
            description="image",
            parameters={"type": "object", "properties": {}},
        )
        original_tools = ToolSet(tools=[card_tool, song_tool, image_tool])
        request = SimpleNamespace(func_tool=original_tools)

        original, gated, initial_count = plugin._gate_initial_reminder_tools(
            request,
            [CARD_TOOL_NAME, "play_song_by_name", "send_image"],
        )

        self.assertIs(original, original_tools)
        self.assertTrue(gated)
        self.assertEqual(initial_count, 1)
        self.assertEqual(request.func_tool.names(), [CARD_TOOL_NAME])

        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        binding = self.make_binding(event, "10001", f"{CARD_HINT_MARKER} gate")
        binding.request = request
        binding.original_func_tool = original
        binding.initial_tool_gate_applied = True
        plugin._restore_reminder_tools(binding, "test")

        self.assertEqual(
            request.func_tool.names(),
            [CARD_TOOL_NAME, "play_song_by_name", "send_image"],
        )

    def test_skills_like_mode_keeps_the_original_tool_set(self) -> None:
        plugin = self.make_plugin()
        plugin.context = SimpleNamespace(
            get_config=lambda: {"provider_settings": {"tool_schema_mode": "skills_like"}}
        )
        card_tool = FunctionTool(
            name=CARD_TOOL_NAME,
            description="card",
            parameters={"type": "object", "properties": {}},
        )
        other_tool = FunctionTool(
            name="play_song_by_name",
            description="song",
            parameters={"type": "object", "properties": {}},
        )
        original_tools = ToolSet(tools=[card_tool, other_tool])
        request = SimpleNamespace(func_tool=original_tools)

        original, gated, initial_count = plugin._gate_initial_reminder_tools(
            request,
            [CARD_TOOL_NAME, "play_song_by_name"],
        )

        self.assertIsNone(original)
        self.assertFalse(gated)
        self.assertEqual(initial_count, 2)
        self.assertIs(request.func_tool, original_tools)


if __name__ == "__main__":
    unittest.main()
