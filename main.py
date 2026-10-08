from __future__ import annotations

import asyncio
import copy
import inspect
import json
import random
import re
import time
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import psutil
from astrbot.api import AstrBotConfig, FunctionTool, ToolSet, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .onebot import CardUpdateResult, OneBotCardClient, clean_card, failure_detail

PLUGIN_ID = "astrbot_plugin_dynamic_card_plus"
PLUGIN_VERSION = "0.9.0"
PLUGIN_DESC = "增强版动态群名片插件：支持系统信息、日程、想法摘要、随心后缀和 LLM 主动改名片"
PLUGIN_REPO = "https://github.com/Whereis-Alice/astrbot_plugin_dynamic_card_plus"

UPSTREAM_REPO = "https://github.com/zgojin/astrbot_plugin_botName"
CARD_TOOL_NAME = "set_dynamic_group_card"
CARD_HINT_MARKER = "[DynamicCardPlus]"
REMINDER_DYNAMIC_SOURCES = ("thought", "schedule", "whim")
CARD_CONTENT_FIELDS = (
    "manual_suffix", "manual_full_card", "manual_until", "last_tool_reason",
    "thought_suffix", "thought_generated_at", "schedule_suffix", "schedule_generated_at",
    "whim_suffix", "whim_generated_at",
)
DEFAULT_TOOL_DESCRIPTION = (
    "修改当前 QQ 群里的群名片。"
    "可以设置一个短后缀表达此刻想法、心情、日程状态。"
    "source=manual、thought、schedule、whim 时可以直接传 suffix；"
    "source=random 或漏传 suffix 时，工具才会按对应来源兜底生成后缀。"
    "短后缀会替换上一轮工具后缀，不要把旧后缀拼进新后缀里。"
    "在配置允许时也可以直接给出完整名片。"
)
DEFAULT_WEEK_SCHEDULE_LINES = [
    "周一=整理周计划",
    "周二=推进待办",
    "周三=补充能量",
    "周四=检查进度",
    "周五=准备周末模式",
    "周六=自由活动",
    "周日=慢慢充电",
]
DEFAULT_SCHEDULE_PROMPT = (
    "请为自己生成一个适合作为今天 QQ 群名片后缀的日程状态。"
    "只输出后缀本身，不要解释，不要加引号，尽量短。"
)


def _clean_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _read_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled", "启用", "是"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled", "禁用", "否"}:
            return False
    if value is None:
        return default
    return bool(value)


def _read_int(
    value: Any,
    default: int,
    *,
    minimum: int = 0,
    maximum: int = 999999,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return min(maximum, max(minimum, number))


def _read_list(value: Any, default: list[str] | None = None) -> list[str]:
    fallback = list(default or [])
    if isinstance(value, list):
        items = [_clean_text(item) for item in value]
        return [item for item in items if item]
    if isinstance(value, str):
        normalized = value.replace("，", ",").replace("；", ";")
        items = [
            item.strip()
            for chunk in normalized.split(";")
            for item in chunk.split(",")
        ]
        return [item for item in items if item]
    return fallback


def _read_reminder_sources(value: Any, legacy_source: Any = "random") -> tuple[str, ...]:
    selected: list[str] = []
    for item in _read_list(value, []):
        source = _clean_text(item).lower()
        if source == "random":
            return REMINDER_DYNAMIC_SOURCES
        if source in REMINDER_DYNAMIC_SOURCES and source not in selected:
            selected.append(source)
    if selected:
        return tuple(selected)

    source = _clean_text(legacy_source, "random").lower()
    if source in REMINDER_DYNAMIC_SOURCES:
        return (source,)
    return REMINDER_DYNAMIC_SOURCES


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _render_template(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format(**values)
    except Exception as exc:
        logger.warning("[%s] template render failed: %r | template=%s", PLUGIN_ID, exc, template)
        return ""


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_COMPACT_SCHEMA_DROP_KEYS = frozenset(
    {"description", "title", "default", "examples", "$comment"}
)


def _compact_json_schema(value: Any) -> Any:
    """Keep tool argument structure while dropping verbose schema annotations."""
    if isinstance(value, dict):
        return {
            key: (
                {name: _compact_json_schema(schema) for name, schema in item.items()}
                if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}
                and isinstance(item, dict) else _compact_json_schema(item)
            )
            for key, item in value.items()
            if key not in _COMPACT_SCHEMA_DROP_KEYS
            and not (
                key == "enum"
                and isinstance(item, list)
                and any(not isinstance(enum_value, str) for enum_value in item)
            )
        }
    if isinstance(value, list):
        return [_compact_json_schema(item) for item in value]
    return value


def _first_clean_line(text: str, max_length: int) -> str:
    cleaned = text.replace("\r", "\n").strip()
    if not cleaned:
        return ""
    line = cleaned.splitlines()[0].strip()
    line = re.sub(r"^[-*#\s`\"'“”‘’「」『』]+", "", line)
    line = line.strip("`\"'“”‘’「」『』")
    return _truncate(line, max_length)


def _extract_visible_reply_from_leaked_draft(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    markers = (
        "思维审视",
        "思考过程",
        "回复草稿",
        "字数校验",
        "规则校验",
        "工具成功把名片",
    )
    if not any(marker in normalized for marker in markers):
        return normalized

    for start_marker in ("回复草稿：", "回复草稿:", "最终回复：", "最终回复:", "正式回复：", "正式回复:", "组合：", "组合:"):
        marker_index = normalized.find(start_marker)
        if marker_index < 0:
            continue
        candidate = normalized[marker_index + len(start_marker) :].strip()
        candidate = re.split(
            r"\n\s*(?:字数校验|规则校验|思维审视|思考过程|回复草稿|组合)\s*[:：]",
            candidate,
            maxsplit=1,
        )[0].strip()
        visible_lines: list[str] = []
        for line in candidate.splitlines():
            line = line.strip()
            if not line:
                if visible_lines:
                    break
                continue
            if re.search(r"(?:句\s*\d+|字数|OK|mode=|source=|reason=|工具|参数)", line):
                break
            visible_lines.append(line)
        visible = "\n".join(visible_lines).strip()
        if visible:
            return visible

    return ""


def _strip_leaked_tool_call_blocks(text: str) -> tuple[str, bool]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if CARD_TOOL_NAME not in normalized:
        return text, False
    if "tool_calls_section_begin" not in normalized and "tool_call_begin" not in normalized:
        return text, False

    stripped = False

    def remove_if_card_tool(match: re.Match[str]) -> str:
        nonlocal stripped
        block = match.group(0)
        if CARD_TOOL_NAME not in block:
            return block
        stripped = True
        return ""

    cleaned = re.sub(
        r"\|tool_calls_section_begin\|.*?\|tool_calls_section_end\|",
        remove_if_card_tool,
        normalized,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"\|tool_call_begin\|.*?\|tool_call_end\|",
        remove_if_card_tool,
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = _compact_spaces(cleaned)
    return cleaned, stripped


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool
    debug_log: bool
    operation_mode: str
    max_card_length: int
    retry_count: int
    max_card_bytes: int
    api_timeout_seconds: int
    retry_delay_seconds: int
    failure_cooldown_seconds: int
    verify_after_write: bool
    blacklist_group_ids: set[str]
    blacklist_unified_origins: set[str]

    bot_name: str
    static_suffix: str
    include_cpu: bool
    include_memory: bool
    include_time: bool
    cpu_template: str
    memory_template: str
    time_template: str

    auto_update_interval_seconds: int
    auto_card_template: str
    auto_include_thought: bool
    auto_include_schedule: bool
    auto_include_whim: bool

    tool_reminder_interval_seconds: int
    tool_reminder_card_template: str
    tool_reminder_inject_hint: bool
    tool_reminder_trigger_mode: str
    tool_reminder_sources: tuple[str, ...]
    tool_reminder_active_cron_expression: str

    thought_refresh_seconds: int
    thought_prefix: str
    thought_prompt: str
    thought_max_length: int
    thought_context_messages: int
    thought_context_message_max_chars: int

    schedule_mode: str
    schedule_refresh_seconds: int
    schedule_prefix: str
    schedule_prompt: str
    schedule_lines: list[str]
    schedule_empty_text: str
    schedule_max_length: int

    whim_refresh_seconds: int
    whim_prefix: str
    whim_mode: str
    whim_pool: list[str]
    whim_prompt: str
    whim_max_length: int

    llm_provider_id: str
    llm_tool_enabled: bool
    llm_tool_description: str
    llm_tool_min_interval_seconds: int
    llm_tool_max_length: int
    llm_tool_allow_full_card: bool
    llm_tool_manual_ttl_seconds: int


@dataclass
class GroupCardState:
    update_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    retry_after: float = 0.0
    last_error: str = ""
    last_verified: bool = False
    last_update_at: float = 0.0
    last_card: str = ""
    last_tool_update_at: float = 0.0
    last_tool_reminder_at: float = 0.0
    last_tool_reason: str = ""
    pending_tool_followup_until: float = 0.0

    last_tool_trace_trigger_id: str = ""
    last_tool_trace_injected_at: float = 0.0
    last_tool_trace_called_at: float = 0.0
    last_tool_trace_completed_at: float = 0.0
    last_tool_trace_logged: bool = False

    client: Any = None
    group_id: str = ""
    self_id: str = ""
    unified_msg_origin: str = ""

    thought_suffix: str = ""
    thought_generated_at: float = 0.0
    schedule_suffix: str = ""
    schedule_generated_at: float = 0.0
    whim_suffix: str = ""
    whim_generated_at: float = 0.0

    manual_suffix: str = ""
    manual_full_card: str = ""
    manual_until: float = 0.0

    recent_messages: deque[str] = field(default_factory=lambda: deque(maxlen=60))
    last_user_text: str = ""

    def has_active_manual_card(self, now: float) -> bool:
        return bool(self.manual_full_card and (self.manual_until <= 0 or self.manual_until > now))

    def has_active_manual_suffix(self, now: float) -> bool:
        return bool(self.manual_suffix and (self.manual_until <= 0 or self.manual_until > now))

    def clear_expired_manual(self, now: float) -> None:
        if self.manual_until > 0 and self.manual_until <= now:
            self.manual_suffix = ""
            self.manual_full_card = ""
            self.manual_until = 0.0
            self.last_tool_reason = ""


@dataclass
class ReminderBinding:
    """One reminder attached to one Agent request.

    A group can have overlapping Agent runs, so this must not live only on the
    per-group state. The event identity keeps cleanup scoped to the request
    that actually received the reminder.
    """

    event_id: int
    event: Any
    unified_msg_origin: str
    group_id: str
    trigger_id: str
    injected_at: float
    request: Any
    hint_part: Any
    request_parts: Any
    hint_text: str
    request_id: str
    state_key: str = ""
    original_func_tool: Any = None
    initial_tool_gate_applied: bool = False
    initial_tool_count: int = 0
    followup_tools_restored: bool = False
    followup_tools_compacted: bool = False
    followup_tool_set: Any = None
    run_context: Any = None
    run_context_id: str = ""
    consumed: bool = False
    request_hint_removed: int = 0
    run_context_hint_removed: int = 0
    tool_called_at: float = 0.0
    tool_completed_at: float = 0.0
    followup_schema_chars: int = 0


@pydantic_dataclass
class DynamicGroupCardTool(FunctionTool[AstrAgentContext]):
    plugin: Any = Field(default=None, repr=False)
    name: str = CARD_TOOL_NAME
    description: str = DEFAULT_TOOL_DESCRIPTION
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "操作类型：suffix 设置短后缀，full_card 设置完整群名片，clear_manual 清除手动后缀或完整名片。",
                    "enum": ["suffix", "full_card", "clear_manual"],
                },
                "suffix": {
                    "type": "string",
                    "description": "mode=suffix 时使用。manual/thought/schedule/whim 都可以直接传入非常短的名片后缀，例如“在想晚饭”或“整理日程中”。",
                },
                "source": {
                    "type": "string",
                    "description": "mode=suffix 时的后缀来源：manual 使用 suffix；thought/schedule/whim 优先使用 suffix，未传时才按对应配置兜底生成；random 在三种动态来源中随机。",
                    "enum": ["manual", "thought", "schedule", "whim", "random"],
                },
                "full_card": {
                    "type": "string",
                    "description": "mode=full_card 时使用。完整群名片，只有插件配置允许时才会生效。",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "保持手动内容的秒数。留空使用插件默认值，0 表示直到下一次 clear_manual 或插件重载。",
                },
                "reason": {
                    "type": "string",
                    "description": "可选。你为什么想这样改名片，用于日志和工具返回。",
                },
            },
            "required": ["mode"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> str:
        if self.plugin is None:
            return "失败：DynamicCardPlus 工具未绑定插件实例，请重载插件。"
        event = getattr(context.context, "event", None)
        if event is None:
            return "失败：没有当前会话事件，无法判断要修改哪个群名片。"
        return await self.plugin.handle_tool_call(event, kwargs)


class DynamicCardPlusPlugin(Star):
    """Dynamic group card plugin for aiocqhttp group chats."""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config or {}
        self._states: dict[str, GroupCardState] = defaultdict(GroupCardState)
        # Reminder context is request-scoped. A group may have overlapping
        # Agent runs, so using only the group state would let one run consume
        # another run's reminder.
        self._reminder_bindings: dict[int, ReminderBinding] = {}
        self._active_cron_jobs: dict[str, str] = {}
        self._active_cron_register_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_cron_db_lock = asyncio.Lock()
        self._register_llm_tool()

    async def initialize(self) -> None:
        logger.info("[%s] initialized; upstream=%s", PLUGIN_ID, UPSTREAM_REPO)

    async def terminate(self) -> None:
        for binding in list(self._reminder_bindings.values()):
            self._restore_reminder_tools(binding, "plugin_unload")
            self._remove_hint_from_request(binding)
            if binding.run_context is not None:
                self._remove_hint_from_run_context(binding.run_context, binding.hint_text)
        self._reminder_bindings.clear()
        for task in list(self._active_cron_register_tasks.values()):
            task.cancel()
        for task in list(self._active_cron_register_tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        self._active_cron_register_tasks.clear()
        await self._delete_registered_active_cron_jobs()

    def _register_llm_tool(self) -> None:
        settings = self._settings()
        self.context.add_llm_tools(
            DynamicGroupCardTool(
                plugin=self,
                description=settings.llm_tool_description,
                active=settings.enabled and settings.llm_tool_enabled,
            )
        )

    async def set_dynamic_group_card(
        self,
        event: AstrMessageEvent,
        mode: str = "suffix",
        suffix: str = "",
        source: str = "manual",
        full_card: str = "",
        duration_seconds: float | None = None,
        reason: str = "",
    ) -> str:
        """修改当前 QQ 群里的群名片。可以设置短后缀，也可以在配置允许时设置完整名片。短后缀会替换上一轮工具后缀，不要把旧后缀拼进新后缀里。

        Args:
            mode(string): 操作类型。suffix 设置短后缀；full_card 设置完整群名片；clear_manual 清除手动后缀或完整名片。
            suffix(string): mode=suffix 时使用。manual/thought/schedule/whim 都可以直接传入非常短的名片后缀，例如“整理日程中”。
            source(string): mode=suffix 时的后缀来源。manual 使用 suffix；thought/schedule/whim 优先使用 suffix，未传时才按对应配置兜底生成；random 在三种动态来源中随机。
            full_card(string): mode=full_card 时使用。完整群名片，只有插件配置允许时才会生效。
            duration_seconds(number): 保持手动内容的秒数。留空使用插件默认值，0 表示直到下一次 clear_manual 或插件重载。
            reason(string): 可选。为什么这样改名片，用于日志和工具返回。
        """
        kwargs: dict[str, Any] = {
            "mode": mode,
            "suffix": suffix,
            "source": source,
            "full_card": full_card,
            "reason": reason,
        }
        if duration_seconds is not None:
            kwargs["duration_seconds"] = duration_seconds
        return await self.handle_tool_call(event, kwargs)

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _ensure_active_cron_job(self, group_key: str, state: GroupCardState, settings: PluginSettings) -> None:
        if group_key in self._active_cron_jobs:
            return
        existing_task = self._active_cron_register_tasks.get(group_key)
        if existing_task is not None and not existing_task.done():
            return
        if not state.unified_msg_origin:
            return
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            logger.warning("[%s] cron_manager unavailable; cannot register active cron job", PLUGIN_ID)
            return

        task = asyncio.create_task(
            self._register_active_cron_job_with_retry(
                group_key=group_key,
                unified_msg_origin=state.unified_msg_origin,
                settings=settings,
            )
        )
        self._active_cron_register_tasks[group_key] = task
        task.add_done_callback(lambda _: self._active_cron_register_tasks.pop(group_key, None))

    async def _register_active_cron_job_with_retry(
        self,
        *,
        group_key: str,
        unified_msg_origin: str,
        settings: PluginSettings,
    ) -> None:
        for attempt in range(4):
            try:
                async with self._active_cron_db_lock:
                    await self._register_active_cron_job_once(group_key, unified_msg_origin, settings)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._is_database_locked_error(exc) and attempt < 3:
                    delay = 1.5 * (attempt + 1)
                    logger.warning(
                        "[%s] active cron register delayed by locked database group=%s attempt=%s delay=%.1fs",
                        PLUGIN_ID,
                        group_key,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("[%s] active cron register failed group=%s error=%r", PLUGIN_ID, group_key, exc)
                return

    async def _register_active_cron_job_once(
        self,
        group_key: str,
        unified_msg_origin: str,
        settings: PluginSettings,
    ) -> None:
        if group_key in self._active_cron_jobs:
            return
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            return

        name = self._active_cron_job_name(group_key)
        await self._delete_active_cron_job_by_name(name)
        cron_expression = self._active_cron_expression(settings)
        note = self._active_cron_note(settings)
        payload = {
            "session": unified_msg_origin,
            "note": note,
        }
        if settings.debug_log:
            logger.info("[%s] active cron note group=%s note=%s", PLUGIN_ID, group_key, note)
        job = await self._maybe_await(
            cron_mgr.add_active_job(
                name=name,
                cron_expression=cron_expression,
                payload=payload,
                run_once=False,
                description="Dynamic Card Plus 主动唤醒 bot 改群名片",
            )
        )

        job_id = self._cron_job_value(job, "id", "job_id") or name
        self._active_cron_jobs[group_key] = str(job_id)
        logger.info(
            "[%s] registered active cron group=%s cron=%s job=%s",
            PLUGIN_ID,
            group_key,
            cron_expression,
            job_id,
        )

    def _is_database_locked_error(self, exc: Exception) -> bool:
        text = repr(exc).lower()
        return "database is locked" in text or "sqlite3.operationalerror" in text and "locked" in text

    async def _delete_registered_active_cron_jobs(self) -> None:
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            self._active_cron_jobs.clear()
            return
        for job_id in list(self._active_cron_jobs.values()):
            try:
                await self._maybe_await(cron_mgr.delete_job(job_id))
            except Exception as exc:
                logger.warning("[%s] delete active cron job failed job=%s error=%r", PLUGIN_ID, job_id, exc)
        self._active_cron_jobs.clear()

    async def _delete_active_cron_job_by_name(self, name: str) -> None:
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            return
        try:
            try:
                jobs = await self._maybe_await(cron_mgr.list_jobs("active"))
            except TypeError:
                jobs = await self._maybe_await(cron_mgr.list_jobs())
        except Exception as exc:
            if self._is_database_locked_error(exc):
                raise
            logger.warning("[%s] list active cron jobs failed: %r", PLUGIN_ID, exc)
            return

        for job in jobs or []:
            if self._cron_job_value(job, "name") != name:
                continue
            job_id = self._cron_job_value(job, "id", "job_id")
            if not job_id:
                continue
            try:
                await self._maybe_await(cron_mgr.delete_job(job_id))
                logger.info("[%s] deleted stale active cron job name=%s id=%s", PLUGIN_ID, name, job_id)
            except Exception as exc:
                if self._is_database_locked_error(exc):
                    raise
                logger.warning("[%s] delete stale active cron job failed id=%s error=%r", PLUGIN_ID, job_id, exc)

    def _cron_job_value(self, job: Any, *names: str) -> Any:
        if isinstance(job, dict):
            for name in names:
                if name in job:
                    return job[name]
            return None
        for name in names:
            value = getattr(job, name, None)
            if value is not None:
                return value
        return None

    def _active_cron_job_name(self, group_key: str) -> str:
        return f"{PLUGIN_ID}:group_card:{group_key}"

    def _active_cron_expression(self, settings: PluginSettings) -> str:
        if settings.tool_reminder_active_cron_expression:
            return settings.tool_reminder_active_cron_expression

        minutes = max(1, (settings.tool_reminder_interval_seconds + 59) // 60)
        if minutes < 60 and 60 % minutes == 0:
            return f"*/{minutes} * * * *"
        if minutes == 1440:
            return "0 0 * * *"
        if minutes % 60 == 0 and minutes < 1440:
            hours = minutes // 60
            if 24 % hours == 0:
                return f"0 */{hours} * * *"
        raise ValueError("这个提醒间隔无法换算为等间隔 cron，请填写 active_cron_expression；例如每 30 分钟用 */30 * * * *")

    def _pick_tool_reminder_source(self, settings: PluginSettings) -> str:
        sources = settings.tool_reminder_sources or REMINDER_DYNAMIC_SOURCES
        return random.choice(list(sources))

    def _active_cron_note(self, settings: PluginSettings) -> str:
        source = self._pick_tool_reminder_source(settings)
        return self._required_group_card_tool_prompt(
            current_card="",
            source=source,
            source_label="主动任务",
            suggestion="请按本次 source 生成或选择新的短后缀。",
            reason="群名片自主管理定时任务",
            trigger_id="",
        )

    def _required_group_card_tool_prompt(
        self,
        *,
        current_card: str,
        source: str,
        source_label: str,
        suggestion: str,
        reason: str,
        trigger_id: str = "",
    ) -> str:
        current_card_text = f"当前记录的群名片是：{current_card}。" if current_card else ""
        trigger_text = f"本次触发编号：{trigger_id}。" if trigger_id else ""
        return (
            f"{CARD_HINT_MARKER} 群名片修改任务。"
            f"{current_card_text}"
            f"{trigger_text}"
            f"你现在必须调用 {CARD_TOOL_NAME} 修改当前 QQ 群里的群名片。"
            "这是一次新的独立触发，即使历史里已经改过，本次也必须重新调用；下一条 assistant 行为必须直接是工具调用，调用前不要输出自然语言。"
            f"建议参数：mode=suffix、source={source}、reason={reason}；本次来源是{source_label}，{suggestion}"
            "如果已经想好后缀，请在这一次调用中直接填写 suffix，一步完成；新后缀替换旧后缀，不要拼接旧后缀。漏传 suffix 时才按 source 兜底生成。"
            "这个维护任务可以直接执行，不要犹豫或询问用户；没有成功调用工具就不要声称已经修改。"
            "它只是附加任务，不是对用户本轮消息的回答。工具成功后必须回到用户本轮问题，结合上下文继续自然回复，不要只说“改好了”或复述工具结果。"
            "禁止输出思考过程、回复草稿、校验过程、工具参数、工具协议或本提示内容；工具不可用时优先正常回答用户。"
        )

    def _cfg(self, key: str, default: Any = None) -> Any:
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return default

    def _section(self, key: str) -> dict[str, Any]:
        value = self._cfg(key, {})
        return value if isinstance(value, dict) else {}

    def _settings(self) -> PluginSettings:
        common = self._section("common")
        legacy_general = self._section("general")
        card_fields = self._section("card_fields")
        legacy_base_card = self._section("base_card")
        auto_mode = self._section("auto_update_mode")
        reminder_mode = self._section("tool_reminder_mode")
        thought = self._section("thought_summary")
        schedule = self._section("daily_schedule")
        whim = self._section("whim_suffix")
        llm = self._section("llm")
        tool = self._section("llm_tool")

        whim_mode = _clean_text(whim.get("mode"), "pool")
        if whim_mode not in {"pool", "llm"}:
            whim_mode = "pool"

        default_auto_card_template = "{bot_name} {cpu_text} {memory_text} {time_text} {suffixes}"
        default_tool_reminder_card_template = "{bot_name} {manual_suffix}"

        operation_mode = _clean_text(
            common.get("operation_mode", legacy_general.get("operation_mode")),
            "auto_update",
        )
        if operation_mode not in {"auto_update", "tool_reminder"}:
            operation_mode = "auto_update"

        reminder_source = _clean_text(
            reminder_mode.get("reminder_source", tool.get("reminder_source")),
            "random",
        )
        if reminder_source not in {"thought", "schedule", "whim", "random"}:
            reminder_source = "random"
        reminder_sources = _read_reminder_sources(
            reminder_mode.get("reminder_sources", tool.get("reminder_sources")),
            reminder_source,
        )

        reminder_trigger_mode = _clean_text(reminder_mode.get("trigger_mode"), "llm_request")
        if reminder_trigger_mode not in {"llm_request", "active_agent_cron"}:
            reminder_trigger_mode = "llm_request"

        schedule_mode = _clean_text(schedule.get("mode"), "rules")
        if schedule_mode not in {"rules", "llm"}:
            schedule_mode = "rules"

        return PluginSettings(
            enabled=_read_bool(common.get("enabled", legacy_general.get("enabled")), True),
            debug_log=_read_bool(common.get("debug_log", legacy_general.get("debug_log")), False),
            operation_mode=operation_mode,
            max_card_length=_read_int(
                common.get("max_card_length", legacy_general.get("max_card_length")),
                60,
                minimum=8,
                maximum=500,
            ),
            retry_count=_read_int(
                common.get("retry_count", legacy_general.get("retry_count")),
                3,
                minimum=1,
                maximum=10,
            ),
            max_card_bytes=_read_int(common.get("max_card_bytes"), 60, minimum=16, maximum=240),
            api_timeout_seconds=_read_int(common.get("api_timeout_seconds"), 10, minimum=3, maximum=30),
            retry_delay_seconds=_read_int(common.get("retry_delay_seconds"), 2, minimum=1, maximum=30),
            failure_cooldown_seconds=_read_int(common.get("failure_cooldown_seconds"), 300, minimum=30, maximum=3600),
            verify_after_write=_read_bool(common.get("verify_after_write"), True),
            blacklist_group_ids=set(
                _read_list(common.get("blacklist_group_ids", legacy_general.get("blacklist_group_ids")), [])
            ),
            blacklist_unified_origins=set(
                _read_list(
                    common.get(
                        "blacklist_unified_origins",
                        legacy_general.get("blacklist_unified_origins"),
                    ),
                    [],
                )
            ),
            bot_name=_clean_text(card_fields.get("bot_name", legacy_base_card.get("bot_name")), "AstrBot"),
            static_suffix=_clean_text(card_fields.get("static_suffix", legacy_base_card.get("static_suffix"))),
            include_cpu=_read_bool(card_fields.get("include_cpu", legacy_base_card.get("include_cpu")), True),
            include_memory=_read_bool(
                card_fields.get("include_memory", legacy_base_card.get("include_memory")),
                True,
            ),
            include_time=_read_bool(card_fields.get("include_time", legacy_base_card.get("include_time")), True),
            cpu_template=_clean_text(
                card_fields.get("cpu_template", legacy_base_card.get("cpu_template")),
                "CPU {cpu}%",
            ),
            memory_template=_clean_text(
                card_fields.get("memory_template", legacy_base_card.get("memory_template")),
                "MEM {memory}%",
            ),
            time_template=_clean_text(
                card_fields.get("time_template", legacy_base_card.get("time_template")),
                "{time}",
            ),
            auto_update_interval_seconds=_read_int(
                auto_mode.get("update_interval_seconds", legacy_general.get("update_interval_seconds")),
                60,
                minimum=5,
                maximum=86400,
            ),
            auto_card_template=str(
                auto_mode.get(
                    "card_template",
                    legacy_base_card.get("card_template", default_auto_card_template),
                )
                or default_auto_card_template
            ).strip(),
            auto_include_thought=_read_bool(
                auto_mode.get("include_thought_summary", thought.get("enabled")),
                False,
            ),
            auto_include_schedule=_read_bool(
                auto_mode.get("include_daily_schedule", schedule.get("enabled")),
                False,
            ),
            auto_include_whim=_read_bool(
                auto_mode.get("include_whim_suffix", whim.get("enabled")),
                False,
            ),
            tool_reminder_interval_seconds=_read_int(
                reminder_mode.get("reminder_interval_seconds", tool.get("reminder_interval_seconds")),
                1800,
                minimum=30,
                maximum=604800,
            ),
            tool_reminder_card_template=str(
                reminder_mode.get("card_template", default_tool_reminder_card_template)
                or default_tool_reminder_card_template
            ).strip(),
            tool_reminder_inject_hint=_read_bool(
                reminder_mode.get("inject_status_hint", tool.get("inject_status_hint")),
                True,
            ),
            tool_reminder_trigger_mode=reminder_trigger_mode,
            tool_reminder_sources=reminder_sources,
            tool_reminder_active_cron_expression=_clean_text(reminder_mode.get("active_cron_expression")),
            thought_refresh_seconds=_read_int(
                thought.get("refresh_seconds"),
                1800,
                minimum=30,
                maximum=604800,
            ),
            thought_prefix=_clean_text(thought.get("prefix"), "想法:"),
            thought_prompt=_clean_text(
                thought.get("prompt"),
                (
                    "请根据最近对话，为自己生成一个适合作为 QQ 群名片后缀的当前想法。"
                    "只输出后缀本身，不要解释，不要加引号。"
                ),
            ),
            thought_max_length=_read_int(thought.get("max_length"), 12, minimum=2, maximum=60),
            thought_context_messages=_read_int(thought.get("context_messages"), 8, minimum=1, maximum=60),
            thought_context_message_max_chars=_read_int(
                thought.get("context_message_max_chars"),
                120,
                minimum=20,
                maximum=1000,
            ),
            schedule_mode=schedule_mode,
            schedule_refresh_seconds=_read_int(
                schedule.get("refresh_seconds"),
                3600,
                minimum=30,
                maximum=604800,
            ),
            schedule_prefix=_clean_text(schedule.get("prefix"), "日程:"),
            schedule_prompt=_clean_text(schedule.get("prompt"), DEFAULT_SCHEDULE_PROMPT),
            schedule_lines=_read_list(schedule.get("schedule_lines"), DEFAULT_WEEK_SCHEDULE_LINES),
            schedule_empty_text=_clean_text(schedule.get("empty_text"), "自由活动"),
            schedule_max_length=_read_int(schedule.get("max_length"), 18, minimum=2, maximum=80),
            whim_refresh_seconds=_read_int(
                whim.get("refresh_seconds"),
                900,
                minimum=30,
                maximum=604800,
            ),
            whim_prefix=_clean_text(whim.get("prefix"), ""),
            whim_mode=whim_mode,
            whim_pool=_read_list(
                whim.get("pool"),
                ["今天也在发光", "慢慢加载灵感", "有一点点开心", "正在观察世界"],
            ),
            whim_prompt=_clean_text(
                whim.get("prompt"),
                (
                    "请随心所欲地生成一个适合作为自己 QQ 群名片后缀的短句。"
                    "只输出后缀本身，不要解释，不要加引号，长度很短。"
                ),
            ),
            whim_max_length=_read_int(whim.get("max_length"), 12, minimum=2, maximum=60),
            llm_provider_id=_clean_text(llm.get("provider_id")),
            llm_tool_enabled=_read_bool(tool.get("enabled"), True),
            llm_tool_description=_clean_text(tool.get("description"), DEFAULT_TOOL_DESCRIPTION),
            llm_tool_min_interval_seconds=_read_int(
                tool.get("min_interval_seconds"),
                30,
                minimum=0,
                maximum=86400,
            ),
            llm_tool_max_length=_read_int(tool.get("max_length"), 18, minimum=2, maximum=120),
            llm_tool_allow_full_card=_read_bool(tool.get("allow_full_card"), False),
            llm_tool_manual_ttl_seconds=_read_int(
                tool.get("manual_ttl_seconds"),
                1800,
                minimum=0,
                maximum=604800,
            ),
        )

    @filter.on_llm_request(desc="到点后向本轮 LLM 请求注入群名片工具调用提醒")
    async def inject_group_card_tool_hint(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        settings = self._settings()
        if not settings.enabled or not settings.llm_tool_enabled or not settings.tool_reminder_inject_hint:
            return

        group_context = self._extract_group_context(event)
        if group_context is None:
            return

        client, group_id, self_id = group_context
        if self._is_blacklisted(event, group_id, settings):
            return

        state = self._states[self._state_key(event, group_id, self_id)]
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        self._remember_user_message(state, event, settings)
        if settings.operation_mode != "tool_reminder":
            return

        if settings.tool_reminder_trigger_mode != "llm_request":
            return

        now = time.time()
        if now < state.retry_after:
            return
        if now - state.last_tool_reminder_at < settings.tool_reminder_interval_seconds:
            return

        current_card = state.last_card or "还没有记录"
        trigger_id = f"{group_id}-{int(now)}"
        suggestion, source_label, source = await self._build_tool_reminder_suggestion(
            event=event,
            state=state,
            settings=settings,
            now=now,
        )
        hint = self._required_group_card_tool_prompt(
            current_card=current_card,
            source=source,
            source_label=source_label,
            suggestion=suggestion,
            reason="群名片自主管理提醒",
            trigger_id=trigger_id,
        )
        tool_names = self._request_tool_names(req)
        if CARD_TOOL_NAME not in tool_names:
            logger.debug("[%s] reminder skipped: card tool is unavailable in this request", PLUGIN_ID)
            return
        hint_part = self._append_provider_hint(req, hint)
        if hint_part is None:
            return
        tool_names = self._request_tool_names(req)
        has_tool = CARD_TOOL_NAME in tool_names
        original_func_tool, tool_gate_applied, initial_tool_count = (
            self._gate_initial_reminder_tools(req, tool_names)
        )
        request_id = f"{id(req):x}"
        event_id = id(event)
        self._prune_reminder_bindings(now)
        self._reminder_bindings[event_id] = ReminderBinding(
            event_id=event_id,
            event=event,
            unified_msg_origin=_normalize_id(getattr(event, "unified_msg_origin", "")),
            group_id=_normalize_id(group_id),
            trigger_id=trigger_id,
            injected_at=now,
            request=req,
            hint_part=hint_part,
            request_parts=getattr(req, "extra_user_content_parts", None),
            hint_text=hint,
            request_id=request_id,
            state_key=self._state_key(event, group_id, self_id),
            original_func_tool=original_func_tool,
            initial_tool_gate_applied=tool_gate_applied,
            initial_tool_count=initial_tool_count,
        )
        state.last_tool_reminder_at = now
        logger.info(
            "[%s] injected tool reminder group=%s source=%s has_tool=%s tool_count=%s initial_tool_count=%s gated=%s trigger=%s request=%s",
            PLUGIN_ID,
            group_id,
            source,
            has_tool,
            len(tool_names),
            initial_tool_count,
            tool_gate_applied,
            trigger_id,
            request_id,
        )
        if settings.debug_log:
            if tool_names:
                logger.info("[%s] request tools sample=%s", PLUGIN_ID, self._format_tool_names_for_log(tool_names))
            logger.info(
                "[%s] reminder prompt group=%s channels=temp_user_content prompt=%s",
                PLUGIN_ID,
                group_id,
                hint,
            )
        if not has_tool:
            logger.warning(
                "[%s] reminder injected but %s is not present in request tools; check persona/tool settings; tools=%s",
                PLUGIN_ID,
                CARD_TOOL_NAME,
                self._format_tool_names_for_log(tool_names),
            )

    def _build_card_only_tool_set(self, tool_set: Any) -> ToolSet | None:
        """Build a request-local tool set used for the forced first action."""
        get_tool = getattr(tool_set, "get_tool", None)
        if not callable(get_tool):
            return None
        card_tool = get_tool(CARD_TOOL_NAME)
        if card_tool is None:
            return None
        return ToolSet(tools=[card_tool])

    def _build_compact_followup_tool_set(self, tool_set: Any) -> ToolSet | None:
        """Keep every executable tool while reducing the follow-up schema size.

        The runner uses request-local tool objects for both schema generation and
        execution. Shallow-copying each tool preserves its handler/subclass
        implementation while allowing this request to omit verbose annotations.
        The original full set remains owned by the request binding and is restored
        when the Agent finishes.
        """
        if not isinstance(tool_set, ToolSet):
            return None

        original_tools = list(getattr(tool_set, "tools", []) or [])
        compact_tools: list[Any] = []
        try:
            for tool in original_tools:
                compact_tool = copy.copy(tool)
                description = _compact_spaces(
                    _clean_text(getattr(tool, "description", ""))
                )
                compact_tool.description = _truncate(description, 240)
                parameters = getattr(tool, "parameters", None)
                if parameters is not None:
                    compact_tool.parameters = _compact_json_schema(parameters)
                compact_tools.append(compact_tool)
        except Exception as exc:
            logger.warning(
                "[%s] compact follow-up tool schema unavailable: %r",
                PLUGIN_ID,
                exc,
            )
            return None

        if len(compact_tools) != len(original_tools):
            return None
        try:
            compact_set = ToolSet(tools=compact_tools)
        except Exception as exc:
            logger.warning(
                "[%s] compact follow-up tool set validation failed: %r",
                PLUGIN_ID,
                exc,
            )
            return None
        if compact_set.names() != tool_set.names():
            return None
        return compact_set

    def _tool_schema_chars(self, tool_set: Any) -> int:
        if not isinstance(tool_set, ToolSet):
            return 0
        try:
            schema = tool_set.get_func_desc_openai_style()
            return len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            return 0

    def _gate_initial_reminder_tools(
        self,
        req: ProviderRequest,
        tool_names: list[str],
    ) -> tuple[Any, bool, int]:
        """Keep only the card tool for the forced first model action.

        AstrBot creates a ToolSet per request, so replacing this request's set
        does not deactivate tools globally. The original set is restored after
        the card operation succeeds (or when the Agent exits).
        """
        original_tool_set = getattr(req, "func_tool", None)
        if CARD_TOOL_NAME not in tool_names or original_tool_set is None:
            return None, False, len(tool_names)
        if self._uses_skills_like_tool_schema():
            # AstrBot keeps a separate raw tool set in skills_like mode. Keep
            # that mode untouched so restored follow-up tools remain executable.
            return None, False, len(tool_names)
        card_only = self._build_card_only_tool_set(original_tool_set)
        if card_only is None:
            return None, False, len(tool_names)
        req.func_tool = card_only
        return original_tool_set, True, 1

    def _uses_skills_like_tool_schema(self) -> bool:
        get_config = getattr(getattr(self, "context", None), "get_config", None)
        if not callable(get_config):
            return False
        try:
            config = get_config() or {}
            provider_settings = config.get("provider_settings", {})
            return provider_settings.get("tool_schema_mode") == "skills_like"
        except (AttributeError, TypeError):
            return False

    def _restore_reminder_tools(self, binding: ReminderBinding, reason: str) -> None:
        """Restore all original tools for the post-maintenance conversation."""
        if (
            not binding.initial_tool_gate_applied
            or binding.followup_tools_restored
            or binding.original_func_tool is None
        ):
            return
        current_tool_set = getattr(binding.request, "func_tool", None)
        binding.request.func_tool = binding.original_func_tool
        binding.followup_tools_restored = True
        before = len(self._request_tool_names(SimpleNamespace(func_tool=current_tool_set)))
        after = len(self._request_tool_names(SimpleNamespace(func_tool=binding.original_func_tool)))
        logger.info(
            "[%s] restored reminder follow-up tools group=%s trigger=%s reason=%s tool_count=%s->%s",
            PLUGIN_ID,
            binding.group_id,
            binding.trigger_id,
            reason,
            before,
            after,
        )

    def _prepare_compact_followup_tools(
        self,
        binding: ReminderBinding,
        reason: str,
    ) -> None:
        """Use compact executable schemas until this request's Agent completes."""
        if (
            not binding.initial_tool_gate_applied
            or binding.followup_tools_restored
            or binding.followup_tools_compacted
            or binding.original_func_tool is None
        ):
            return

        compact_set = self._build_compact_followup_tool_set(binding.original_func_tool)
        if compact_set is None:
            self._restore_reminder_tools(binding, f"{reason}_fallback_full")
            return

        current_tool_set = getattr(binding.request, "func_tool", None)
        original_schema_chars = self._tool_schema_chars(binding.original_func_tool)
        binding.request.func_tool = compact_set
        binding.followup_tool_set = compact_set
        binding.followup_tools_compacted = True
        binding.followup_schema_chars = self._tool_schema_chars(compact_set)
        before = len(
            self._request_tool_names(SimpleNamespace(func_tool=current_tool_set))
        )
        after = len(self._request_tool_names(SimpleNamespace(func_tool=compact_set)))
        logger.info(
            "[%s] prepared compact reminder follow-up tools group=%s trigger=%s "
            "reason=%s tool_count=%s->%s schema=compact_executable",
            PLUGIN_ID,
            binding.group_id,
            binding.trigger_id,
            reason,
            before,
            after,
        )
        if binding.followup_schema_chars:
            logger.info(
                "[%s] reminder follow-up schema group=%s trigger=%s chars=%s original_chars=%s",
                PLUGIN_ID,
                binding.group_id,
                binding.trigger_id,
                binding.followup_schema_chars,
                original_schema_chars,
            )

    def _append_provider_hint(self, req: ProviderRequest, hint: str) -> TextPart | None:
        return self._append_temp_user_hint(req, hint)

    def _append_temp_user_hint(self, req: ProviderRequest, hint: str) -> TextPart | None:
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            return None
        for part in parts:
            if CARD_HINT_MARKER in _clean_text(getattr(part, "text", "")):
                return None
        part = TextPart(text=hint)
        mark_as_temp = getattr(part, "mark_as_temp", None)
        if callable(mark_as_temp):
            mark_as_temp()
        parts.append(part)
        return part

    def _prune_reminder_bindings(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        # A request can legitimately spend several minutes in provider retries,
        # but a binding left for hours must not be consumed by a later manual
        # tool call after its original Agent has disappeared.
        max_age = 2 * 60 * 60
        stale_ids = [
            event_id
            for event_id, binding in self._reminder_bindings.items()
            if now - binding.injected_at > max_age
        ]
        for event_id in stale_ids:
            binding = self._reminder_bindings.pop(event_id, None)
            if binding is not None:
                logger.debug(
                    "[%s] discarded stale reminder binding group=%s trigger=%s request=%s",
                    PLUGIN_ID,
                    binding.group_id,
                    binding.trigger_id,
                    binding.request_id,
                )

    def _find_reminder_binding(self, event: AstrMessageEvent) -> ReminderBinding | None:
        self._prune_reminder_bindings()
        direct = self._reminder_bindings.get(id(event))
        if direct is not None:
            if direct.event is event:
                return direct
            self._reminder_bindings.pop(id(event), None)

        # MainAgentHooks normally passes the same event object through every
        # phase. Keep a narrow fallback for adapters that wrap the event while
        # preserving the no-guessing rule when multiple runs share a session.
        unified_msg_origin = _normalize_id(getattr(event, "unified_msg_origin", ""))
        if not unified_msg_origin:
            return None
        candidates = [
            binding
            for binding in self._reminder_bindings.values()
            if binding.unified_msg_origin == unified_msg_origin
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _part_text(self, part: Any) -> str:
        if isinstance(part, str):
            return part
        if isinstance(part, dict):
            return _clean_text(part.get("text"))
        return _clean_text(getattr(part, "text", ""))

    def _strip_hint_from_part(
        self,
        part: Any,
        hint_text: str,
    ) -> tuple[Any | None, int]:
        text = self._part_text(part)
        if CARD_HINT_MARKER not in text:
            return part, 0

        if hint_text and hint_text in text:
            remaining = text.replace(hint_text, "").strip()
        else:
            marker_index = text.find(CARD_HINT_MARKER)
            remaining = text[:marker_index].rstrip()

        if not remaining:
            return None, 1
        if isinstance(part, dict):
            updated = dict(part)
            updated["text"] = remaining
            return updated, 1
        if isinstance(part, str):
            return remaining, 1
        try:
            part.text = remaining
        except (AttributeError, TypeError):
            return None, 1
        return part, 1

    def _remove_hint_from_parts(self, parts: Any, hint_text: str) -> int:
        if not isinstance(parts, list):
            return 0
        removed = 0
        for index in range(len(parts) - 1, -1, -1):
            replacement, count = self._strip_hint_from_part(parts[index], hint_text)
            if not count:
                continue
            removed += count
            if replacement is None:
                del parts[index]
            else:
                parts[index] = replacement
        return removed

    def _remove_hint_from_request(self, binding: ReminderBinding) -> int:
        containers: list[Any] = []
        for parts in (
            binding.request_parts,
            getattr(binding.request, "extra_user_content_parts", None),
        ):
            if parts is None or any(parts is existing for existing in containers):
                continue
            containers.append(parts)

        removed = 0
        for parts in containers:
            if isinstance(parts, list):
                for index, part in enumerate(list(parts)):
                    if part is binding.hint_part:
                        del parts[index]
                        removed += 1
                        break
            removed += self._remove_hint_from_parts(parts, binding.hint_text)
        return removed

    def _remove_hint_from_run_context(self, run_context: Any, hint_text: str) -> int:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return 0

        removed = 0
        remove_message_indexes: list[int] = []
        for index, message in enumerate(messages):
            if isinstance(message, dict):
                content = message.get("content")
            else:
                content = getattr(message, "content", None)

            if isinstance(content, list):
                before = len(content)
                removed += self._remove_hint_from_parts(content, hint_text)
                if len(content) != before and not content:
                    remove_message_indexes.append(index)
                continue

            if not isinstance(content, str) or CARD_HINT_MARKER not in content:
                continue

            if hint_text and hint_text in content:
                remaining = content.replace(hint_text, "").strip()
            else:
                marker_index = content.find(CARD_HINT_MARKER)
                remaining = content[:marker_index].rstrip()
            removed += 1
            if not remaining:
                remove_message_indexes.append(index)
            elif isinstance(message, dict):
                message["content"] = remaining
            else:
                message.content = remaining

        for index in reversed(remove_message_indexes):
            del messages[index]
        return removed

    def _consume_pending_reminder(
        self,
        event: AstrMessageEvent,
        state: GroupCardState,
        group_id: str,
        now: float,
    ) -> bool:
        """Consume the request-scoped reminder before the tool follow-up."""
        binding = self._find_reminder_binding(event)
        if (
            binding is None
            or binding.consumed
            or binding.group_id != _normalize_id(group_id)
        ):
            return False

        request_removed = self._remove_hint_from_request(binding)
        context_removed = self._remove_hint_from_run_context(
            binding.run_context,
            binding.hint_text,
        )
        binding.consumed = True
        binding.request_hint_removed = request_removed
        binding.run_context_hint_removed = context_removed
        binding.tool_called_at = now
        trigger_id = binding.trigger_id
        injected_at = binding.injected_at

        state.last_tool_trace_trigger_id = trigger_id
        state.last_tool_trace_injected_at = injected_at
        state.last_tool_trace_called_at = now
        state.last_tool_trace_completed_at = 0.0
        state.last_tool_trace_logged = False
        logger.info(
            "[%s] reminder reached tool group=%s trigger=%s wait_seconds=%.3f "
            "request_hint_removed=%s run_context_hint_removed=%s request=%s run_context=%s",
            PLUGIN_ID,
            group_id,
            trigger_id,
            max(0.0, now - injected_at),
            bool(request_removed),
            bool(context_removed),
            binding.request_id or "-",
            binding.run_context_id or "-",
        )
        return True

    @filter.on_agent_begin(desc="记录群名片提醒对应的 Agent 上下文")
    async def bind_group_card_reminder_context(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
    ) -> None:
        binding = self._find_reminder_binding(event)
        if binding is None:
            return
        binding.run_context = run_context
        binding.run_context_id = f"{id(run_context):x}"
        now = time.time()
        hint_in_context = any(
            CARD_HINT_MARKER in self._part_text(part)
            for message in (getattr(run_context, "messages", []) or [])
            for part in (
                getattr(message, "content", [])
                if isinstance(getattr(message, "content", None), list)
                else [getattr(message, "content", "")]
            )
        )
        logger.info(
            "[%s] bound reminder to agent group=%s trigger=%s request=%s "
            "run_context=%s inject_to_agent_begin_seconds=%.3f "
            "hint_in_context=%s messages=%s",
            PLUGIN_ID,
            binding.group_id,
            binding.trigger_id,
            binding.request_id or "-",
            binding.run_context_id,
            max(0.0, now - binding.injected_at),
            hint_in_context,
            len(getattr(run_context, "messages", []) or []),
        )

    @filter.on_using_llm_tool(desc="群名片工具调用前清理已消费的临时提醒")
    async def consume_group_card_reminder_before_tool(
        self,
        event: AstrMessageEvent,
        tool: FunctionTool,
        tool_args: dict[str, Any] | None,
    ) -> None:
        del tool_args
        if _clean_text(getattr(tool, "name", "")) != CARD_TOOL_NAME:
            return
        binding = self._find_reminder_binding(event)
        if binding is None:
            return
        state = self._states[binding.state_key or binding.group_id]
        self._consume_pending_reminder(event, state, binding.group_id, time.time())

    @filter.on_agent_done(desc="清理未调用工具时残留的群名片提醒")
    async def release_group_card_reminder(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
        response: LLMResponse,
    ) -> None:
        del response
        binding = self._find_reminder_binding(event)
        if binding is None:
            return
        self._restore_reminder_tools(binding, "agent_done")
        self._reminder_bindings.pop(binding.event_id, None)
        if binding.consumed:
            now = time.time()
            final_context_removed = self._remove_hint_from_run_context(
                run_context,
                binding.hint_text,
            )
            logger.info(
                "[%s] reminder agent finished group=%s trigger=%s "
                "tool_to_agent_done_seconds=%.3f total_seconds=%.3f "
                "tool_completed=%s run_context_hint_removed=%s request=%s run_context=%s",
                PLUGIN_ID,
                binding.group_id,
                binding.trigger_id,
                max(0.0, now - (binding.tool_completed_at or binding.tool_called_at)),
                max(0.0, now - binding.injected_at),
                bool(binding.tool_completed_at),
                bool(binding.run_context_hint_removed or final_context_removed),
                binding.request_id or "-",
                binding.run_context_id or "-",
            )
            return
        request_removed = self._remove_hint_from_request(binding)
        context_removed = self._remove_hint_from_run_context(run_context, binding.hint_text)
        logger.info(
            "[%s] released unconsumed reminder group=%s trigger=%s "
            "request_hint_removed=%s run_context_hint_removed=%s request=%s",
            PLUGIN_ID,
            binding.group_id,
            binding.trigger_id,
            bool(request_removed),
            bool(context_removed),
            binding.request_id or "-",
        )

    def _log_tool_trace_before_send(
        self,
        state: GroupCardState,
        group_id: str,
        now: float,
    ) -> None:
        """Log the per-group timing once, immediately before the final send."""
        trigger_id = state.last_tool_trace_trigger_id
        completed_at = state.last_tool_trace_completed_at
        if not trigger_id or not completed_at or state.last_tool_trace_logged:
            return
        if now < completed_at or now - completed_at > 1800:
            return

        state.last_tool_trace_logged = True
        logger.info(
            "[%s] timing group=%s trigger=%s reminder_to_tool_seconds=%.3f tool_to_final_send_seconds=%.3f total_seconds=%.3f",
            PLUGIN_ID,
            group_id,
            trigger_id,
            max(0.0, state.last_tool_trace_called_at - state.last_tool_trace_injected_at),
            max(0.0, now - completed_at),
            max(0.0, now - state.last_tool_trace_injected_at),
        )

    def _request_tool_names(self, req: ProviderRequest) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        names_attr = getattr(tool_set, "names", None)
        if callable(names_attr):
            try:
                return [_clean_text(name) for name in names_attr() if _clean_text(name)]
            except Exception:
                pass
        if names_attr and not callable(names_attr):
            return [_clean_text(name) for name in names_attr if _clean_text(name)]

        tools = getattr(tool_set, "tools", None)
        if callable(tools):
            try:
                tools = tools()
            except TypeError:
                tools = []
        if isinstance(tools, dict):
            iterable = tools.values()
        elif tools:
            iterable = tools
        else:
            iterable = []

        names: list[str] = []
        for tool in iterable:
            name = _clean_text(getattr(tool, "name", ""))
            if name:
                names.append(name)
        return names

    def _format_tool_names_for_log(self, tool_names: list[str]) -> str:
        if not tool_names:
            return "-"
        visible = list(tool_names[:12])
        if CARD_TOOL_NAME in tool_names and CARD_TOOL_NAME not in visible:
            visible = [CARD_TOOL_NAME, *visible[:11]]
        suffix = ""
        hidden_count = max(0, len(tool_names) - len(visible))
        if hidden_count:
            suffix = f",...(+{hidden_count} more)"
        return ",".join(visible) + suffix

    @filter.on_llm_response(desc="清理模型误输出的群名片工具协议和草稿内容")
    async def sanitize_group_card_tool_followup(
        self,
        event: AstrMessageEvent,
        response: LLMResponse,
    ) -> None:
        settings = self._settings()
        if not settings.enabled or not settings.llm_tool_enabled:
            return

        group_context = self._extract_group_context(event)
        if group_context is None:
            return

        _, group_id, self_id = group_context
        state = self._states[self._state_key(event, group_id, self_id)]
        original = _clean_text(getattr(response, "completion_text", ""))
        cleaned, stripped_tool_call = _strip_leaked_tool_call_blocks(original)
        now = time.time()
        completed_at = event.get_extra("dynamic_card_completed_at", 0)
        pending_followup = bool(completed_at and now - completed_at <= 300)
        reminder_binding = self._find_reminder_binding(event) if pending_followup else None

        if settings.debug_log:
            tool_names = [
                _clean_text(name)
                for name in (getattr(response, "tools_call_name", None) or [])
                if _clean_text(name)
            ]
            logger.info(
                "[%s] llm response group=%s phase=%s role=%s tool_count=%s "
                "completion_chars=%s reasoning_chars=%s pending_followup=%s "
                "tool_to_response_seconds=%.3f followup_schema=%s context_messages=%s",
                PLUGIN_ID,
                group_id,
                "tool_call" if tool_names else "assistant",
                _clean_text(getattr(response, "role", ""), "-"),
                len(tool_names),
                len(original),
                len(_clean_text(getattr(response, "reasoning_content", ""))),
                pending_followup,
                max(0.0, now - state.last_tool_trace_completed_at)
                if pending_followup and state.last_tool_trace_completed_at
                else 0.0,
                "compact"
                if reminder_binding is not None and reminder_binding.followup_tools_compacted
                else "full/unknown",
                len(getattr(reminder_binding.run_context, "messages", []) or [])
                if reminder_binding is not None
                else 0,
            )

        if pending_followup:
            state.pending_tool_followup_until = 0.0
            event.set_extra("dynamic_card_completed_at", 0)
            draft_cleaned = _extract_visible_reply_from_leaked_draft(cleaned)
            if draft_cleaned != cleaned:
                cleaned = draft_cleaned

        if not stripped_tool_call and (not pending_followup or cleaned == original):
            return

        response.completion_text = cleaned
        chain = getattr(response, "result_chain", None)
        if hasattr(chain, "chain"):
            non_text = [part for part in chain.chain if not isinstance(part, Plain)]
            chain.chain = ([Plain(cleaned)] if cleaned else []) + non_text
        logger.info(
            "[%s] sanitized leaked group card tool response group=%s stripped_tool_call=%s pending_followup=%s",
            PLUGIN_ID,
            group_id,
            stripped_tool_call,
            pending_followup,
        )

    @filter.on_decorating_result(desc="自动模式下在发送回复前刷新 QQ 群名片")
    async def modify_card_before_send(self, event: AstrMessageEvent) -> None:
        if event.get_extra("dynamic_card_readonly", False):
            return
        settings = self._settings()
        if not settings.enabled:
            return

        group_context = self._extract_group_context(event)
        if group_context is None:
            return

        client, group_id, self_id = group_context
        if self._is_blacklisted(event, group_id, settings):
            return

        group_key = self._state_key(event, group_id, self_id)
        state = self._states[group_key]
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        self._remember_exchange(state, event, settings)
        self._log_tool_trace_before_send(state, group_id, time.time())
        if settings.operation_mode != "auto_update":
            return

        async with state.update_lock:
            now = time.time()
            if now < state.retry_after:
                return
            if now - state.last_update_at < settings.auto_update_interval_seconds:
                return

            await self._refresh_dynamic_suffixes(event, state, settings, now)
            new_card = self._build_card(state, settings)
            if not new_card:
                logger.info("[%s] group=%s generated empty card, skipped", PLUGIN_ID, group_id)
                state.last_update_at = now
                return

            if new_card == state.last_card:
                state.last_update_at = now
                return

            if settings.debug_log:
                logger.info("[%s] updating group=%s card=%s", PLUGIN_ID, group_id, new_card)

            ok = await self._set_group_card(
                client=client,
                group_id=group_id,
                self_id=self_id,
                card=new_card,
                retry_count=settings.retry_count,
            )
            self._record_update_result(state, ok, settings)
            if ok:
                state.last_card = new_card
                state.last_update_at = time.time()

    async def handle_tool_call(
        self,
        event: AstrMessageEvent,
        kwargs: dict[str, Any],
    ) -> str:
        settings = self._settings()
        if not settings.enabled:
            return "失败：DynamicCardPlus 当前已在配置中禁用。"
        if not settings.llm_tool_enabled:
            return "失败：群名片 LLM 工具当前已在配置中禁用。"

        group_context = self._extract_group_context(event)
        if group_context is None:
            return "失败：只能在 aiocqhttp 的 QQ 群聊里修改群名片。"

        _, group_id, self_id = group_context
        if self._is_blacklisted(event, group_id, settings):
            return f"失败：群 {group_id} 在黑名单中，不能使用动态名片插件。"

        group_key = self._state_key(event, group_id, self_id)
        state = self._states[group_key]
        async with state.update_lock:
            try:
                return await self._handle_tool_call_locked(event, kwargs, settings, group_context, state)
            finally:
                binding = self._find_reminder_binding(event)
                if binding is not None and not binding.tool_completed_at:
                    self._restore_reminder_tools(binding, "tool_not_completed")

    async def _handle_tool_call_locked(
        self, event: AstrMessageEvent, kwargs: dict[str, Any],
        settings: PluginSettings, group_context: tuple[Any, str, str],
        state: GroupCardState,
    ) -> str:
        client, group_id, self_id = group_context
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        now = time.time()
        reminder_consumed = self._consume_pending_reminder(event, state, group_id, now)
        reminder_binding = self._find_reminder_binding(event)
        if reminder_binding is not None and (
            not (reminder_consumed or reminder_binding.consumed)
            or reminder_binding.group_id != group_id
        ):
            reminder_binding = None
        if now < state.retry_after:
            return f"失败：上次接口调用失败，约 {int(state.retry_after - now) + 1} 秒后可再试。{state.last_error}"
        cooldown_left = settings.llm_tool_min_interval_seconds - (now - state.last_tool_update_at)
        if cooldown_left > 0:
            if reminder_binding is not None:
                self._restore_reminder_tools(reminder_binding, "tool_rejected_cooldown")
            return f"失败：刚刚已经改过群名片，请约 {int(cooldown_left)} 秒后再试。"

        mode = _clean_text(kwargs.get("mode"), "suffix")
        if mode not in {"suffix", "full_card", "clear_manual"}:
            return "失败：mode 只能是 suffix、full_card 或 clear_manual，未修改群名片。"
        source = _clean_text(kwargs.get("source"), "manual")
        if mode == "suffix" and source not in {"manual", "thought", "schedule", "whim", "random"}:
            return "失败：不支持这个后缀来源，未修改群名片。"
        # Work on a candidate. Rejected/failed/cancelled calls must preserve the
        # existing suffix, expiry and generated content.
        target_state = state
        state = copy.copy(target_state)
        reason = _truncate(_clean_text(kwargs.get("reason")), 200)
        duration_seconds = _read_int(
            kwargs.get("duration_seconds"),
            settings.llm_tool_manual_ttl_seconds,
            minimum=0,
            maximum=604800,
        )
        state.manual_until = 0.0 if duration_seconds == 0 else now + duration_seconds

        if mode == "clear_manual":
            state.manual_suffix = ""
            state.manual_full_card = ""
            state.manual_until = 0.0
            state.last_tool_reason = reason
            if settings.operation_mode == "tool_reminder":
                self._clear_dynamic_suffixes(state)
        elif mode == "full_card":
            if not settings.llm_tool_allow_full_card:
                if reminder_binding is not None:
                    self._restore_reminder_tools(reminder_binding, "tool_rejected_full_card_disabled")
                return "失败：配置不允许 LLM 工具直接设置完整群名片。"
            state.manual_full_card = _truncate(
                _clean_text(kwargs.get("full_card")),
                min(settings.llm_tool_max_length, settings.max_card_length),
            )
            state.manual_suffix = ""
            state.last_tool_reason = reason
            if not state.manual_full_card:
                if reminder_binding is not None:
                    self._restore_reminder_tools(reminder_binding, "tool_rejected_empty_full_card")
                return "失败：full_card 为空，未修改群名片。"
        else:
            if settings.operation_mode == "tool_reminder":
                self._clear_dynamic_suffixes(state)
            source = _clean_text(kwargs.get("source"), "manual")
            if source not in {"manual", "thought", "schedule", "whim", "random"}:
                source = "manual"
            suffix = _clean_text(kwargs.get("suffix"))
            source_labels = {
                "manual": "手动后缀",
                "thought": "会话想法摘要",
                "schedule": "当天日程",
                "whim": "随心后缀",
            }
            source_label = source_labels.get(source, "手动后缀")
            if suffix and source in {"thought", "schedule", "whim"}:
                if source == "thought":
                    state.thought_suffix = suffix
                    state.thought_generated_at = now
                elif source == "schedule":
                    state.schedule_suffix = suffix
                    state.schedule_generated_at = now
                elif source == "whim":
                    state.whim_suffix = suffix
                    state.whim_generated_at = now
            elif source != "manual" or not suffix:
                suffix, source_label = await self._build_suffix_from_source(
                    event=event,
                    state=state,
                    settings=settings,
                    source=source,
                    now=now,
                    unified_msg_origin=_normalize_id(getattr(event, "unified_msg_origin", "")),
                    allow_llm_fallback=settings.operation_mode != "tool_reminder",
                )
            state.manual_suffix = _truncate(suffix, settings.llm_tool_max_length)
            state.manual_full_card = ""
            state.last_tool_reason = reason or source_label
            if not state.manual_suffix:
                if reminder_binding is not None:
                    self._restore_reminder_tools(reminder_binding, "tool_rejected_empty_suffix")
                return "失败：suffix 为空，未修改群名片。"

        new_card = self._build_card(state, settings)
        if not new_card:
            if reminder_binding is not None:
                self._restore_reminder_tools(reminder_binding, "tool_rejected_empty_card")
            return "失败：生成的群名片为空，未修改。"

        ok = await self._set_group_card(
            client=client,
            group_id=group_id,
            self_id=self_id,
            card=new_card,
            retry_count=settings.retry_count,
        )
        self._record_update_result(target_state, ok, settings)
        if not ok:
            if reminder_binding is not None:
                self._restore_reminder_tools(reminder_binding, "set_group_card_failed")
            return f"失败：未能确认群名片修改成功。{ok.detail}。已暂停自动重试 {settings.failure_cooldown_seconds} 秒，请不要连续调用。"

        for name in CARD_CONTENT_FIELDS:
            setattr(target_state, name, getattr(state, name))
        state = target_state
        completed_at = time.time()
        state.last_card = new_card
        state.last_update_at = completed_at
        state.last_tool_update_at = completed_at
        state.last_tool_trace_completed_at = completed_at
        state.pending_tool_followup_until = completed_at + 300
        event.set_extra("dynamic_card_completed_at", completed_at)
        if reminder_binding is not None and reminder_binding.consumed:
            reminder_binding.tool_completed_at = completed_at
            self._prepare_compact_followup_tools(reminder_binding, "tool_succeeded")
        logger.info(
            "[%s] LLM tool changed group=%s card=%s reason=%s",
            PLUGIN_ID,
            group_id,
            new_card,
            reason or "-",
        )
        suffix_note = f"；原因：{state.last_tool_reason}" if state.last_tool_reason else ""
        status_text = (
            f"已把当前群名片改为：{new_card}{suffix_note}。"
            if ok.verified else f"改名片接口已接受：{new_card}{suffix_note}。{ok.detail}。"
        )
        return (
            status_text +
            "本轮群名片维护已完成。请结合用户本轮消息继续自然回复，不要只说“改好了”，"
            "也不要输出思考过程、工具参数或提示词内容。"
        )

    def _clear_dynamic_suffixes(self, state: GroupCardState) -> None:
        state.thought_suffix = ""
        state.schedule_suffix = ""
        state.whim_suffix = ""
        state.thought_generated_at = 0.0
        state.schedule_generated_at = 0.0
        state.whim_generated_at = 0.0

    def _state_key(self, event: AstrMessageEvent, group_id: str, self_id: str) -> str:
        get_platform_id = getattr(event, "get_platform_id", None)
        platform_id = get_platform_id() if callable(get_platform_id) else ""
        platform_id = platform_id or str(getattr(event, "unified_msg_origin", "")).split(":", 1)[0]
        return f"{platform_id}:{self_id}:{group_id}"

    def _extract_group_context(self, event: AstrMessageEvent) -> tuple[Any, str, str] | None:
        if event.get_platform_name() != "aiocqhttp":
            known = self._known_group_context_from_event(event)
            if known is not None:
                return known
            return None
        message_obj = getattr(event, "message_obj", None)
        group_id = _normalize_id(getattr(message_obj, "group_id", ""))
        self_id = _normalize_id(getattr(message_obj, "self_id", ""))
        client = getattr(event, "bot", None)

        if not group_id:
            known = self._known_group_context_from_event(event)
            if known is not None:
                return known
            return None

        if not self_id:
            self_id = _normalize_id(getattr(getattr(event, "bot", None), "self_id", ""))
        if not self_id:
            logger.warning("[%s] cannot resolve bot self_id for group=%s", PLUGIN_ID, group_id)
            return None
        if client is None:
            known = self._known_group_context_from_event(event)
            if known is not None:
                return known
            logger.warning("[%s] cannot resolve aiocqhttp client for group=%s", PLUGIN_ID, group_id)
            return None
        return client, group_id, self_id

    def _known_group_context_from_event(self, event: AstrMessageEvent) -> tuple[Any, str, str] | None:
        unified_msg_origin = _normalize_id(getattr(event, "unified_msg_origin", ""))
        if not unified_msg_origin:
            return None
        targets = []
        for state in self._states.values():
            if state.unified_msg_origin != unified_msg_origin:
                continue
            if state.client and state.group_id and state.self_id:
                targets.append((state.client, state.group_id, state.self_id))
        # A synthetic cron event has no QQ self_id. Refuse an ambiguous origin
        # instead of choosing the first account on a shared adapter.
        return targets[0] if len(targets) == 1 else None

    async def _remember_group_target(
        self,
        state: GroupCardState,
        event: AstrMessageEvent,
        client: Any,
        group_id: str,
        self_id: str,
        settings: PluginSettings,
    ) -> None:
        state.client = client
        state.group_id = _normalize_id(group_id)
        state.self_id = _normalize_id(self_id)
        state.unified_msg_origin = _normalize_id(getattr(event, "unified_msg_origin", ""))
        if (
            settings.enabled
            and settings.operation_mode == "tool_reminder"
            and settings.tool_reminder_trigger_mode == "active_agent_cron"
            and not self._is_blacklisted_origin(state.group_id, state.unified_msg_origin, settings)
        ):
            await self._ensure_active_cron_job(self._state_key(event, group_id, self_id), state, settings)

    def _is_blacklisted(
        self,
        event: AstrMessageEvent,
        group_id: str,
        settings: PluginSettings,
    ) -> bool:
        return self._is_blacklisted_origin(
            group_id,
            _normalize_id(getattr(event, "unified_msg_origin", "")),
            settings,
        )

    def _is_blacklisted_origin(
        self,
        group_id: str,
        unified_msg_origin: str,
        settings: PluginSettings,
    ) -> bool:
        return (
            _normalize_id(group_id) in settings.blacklist_group_ids
            or _normalize_id(unified_msg_origin) in settings.blacklist_unified_origins
        )

    def _remember_exchange(
        self,
        state: GroupCardState,
        event: AstrMessageEvent,
        settings: PluginSettings,
    ) -> None:
        self._remember_user_message(state, event, settings)
        bot_text = self._result_to_text(event.get_result())
        self._remember_bot_message(state, bot_text, settings)

    def _remember_user_message(
        self,
        state: GroupCardState,
        event: AstrMessageEvent,
        settings: PluginSettings,
    ) -> None:
        user_text = _clean_text(getattr(event, "message_str", ""))
        if not user_text and hasattr(event, "get_message_str"):
            try:
                user_text = _clean_text(event.get_message_str())
            except Exception:
                user_text = ""

        if not user_text or user_text == state.last_user_text:
            return
        state.last_user_text = user_text
        max_chars = settings.thought_context_message_max_chars
        state.recent_messages.append(f"用户: {_truncate(user_text, max_chars)}")
        while len(state.recent_messages) > settings.thought_context_messages:
            state.recent_messages.popleft()

    def _remember_bot_message(
        self,
        state: GroupCardState,
        bot_text: str,
        settings: PluginSettings,
    ) -> None:
        bot_text = _clean_text(bot_text)
        if not bot_text:
            return
        max_chars = settings.thought_context_message_max_chars
        state.recent_messages.append(f"我: {_truncate(bot_text, max_chars)}")

        while len(state.recent_messages) > settings.thought_context_messages:
            state.recent_messages.popleft()

    def _result_to_text(self, result: Any) -> str:
        if result is None:
            return ""
        if hasattr(result, "get_plain_text"):
            try:
                return _clean_text(result.get_plain_text())
            except Exception:
                pass

        chain = getattr(result, "chain", None)
        if not chain:
            return ""

        parts: list[str] = []
        for comp in chain:
            if isinstance(comp, Plain):
                parts.append(comp.text)
                continue
            text = getattr(comp, "text", None)
            if text:
                parts.append(str(text))
        return _clean_text("".join(parts))

    async def _build_tool_reminder_suggestion(
        self,
        *,
        event: AstrMessageEvent,
        state: GroupCardState,
        settings: PluginSettings,
        now: float,
    ) -> tuple[str, str, str]:
        del event, state, now
        source = self._pick_tool_reminder_source(settings)

        if source == "thought":
            return (
                "请根据最近对话自己想一个很短的当前想法后缀，并在工具参数里直接填写 suffix；不要把旧后缀拼进去。",
                "会话想法摘要",
                "thought",
            )

        if source == "schedule":
            if settings.schedule_mode == "llm":
                return (
                    "请你把今天的日程状态概括成很短的后缀，并在工具参数里直接填写 suffix；不要留给工具二次生成。",
                    "当天日程",
                    "schedule",
                )
            schedule = self._build_schedule_rule_suffix(settings)
            if schedule:
                return (
                    f"请你把今天的日程状态概括成很短的后缀，并在工具参数里直接填写 suffix；可以参考“{schedule}”。",
                    "当天日程",
                    "schedule",
                )
            return (
                "请你把今天的日程状态概括成很短的后缀，并在工具参数里直接填写 suffix。",
                "当天日程",
                "schedule",
            )

        if source == "whim":
            return (
                "请你自己随心想一个很短的后缀，并在工具参数里直接填写 suffix；不要参考旧后缀，不要把旧后缀拼进去。",
                "随心后缀",
                "whim",
            )

        return "请你自己想一个很短的动态后缀，并在工具参数里直接填写 suffix。", "动态后缀", "whim"

    async def _build_suffix_from_source(
        self,
        *,
        event: AstrMessageEvent | None,
        state: GroupCardState,
        settings: PluginSettings,
        source: str,
        now: float,
        unified_msg_origin: str = "",
        allow_llm_fallback: bool = True,
        use_generic_fallback: bool = True,
    ) -> tuple[str, str]:
        source = _clean_text(source, "manual")
        if source == "random":
            candidates = ["thought", "schedule", "whim"]
            random.shuffle(candidates)
            for candidate in candidates:
                suffix, label = await self._build_suffix_from_source(
                    event=event,
                    state=state,
                    settings=settings,
                    source=candidate,
                    now=now,
                    unified_msg_origin=unified_msg_origin,
                    allow_llm_fallback=allow_llm_fallback,
                    use_generic_fallback=False,
                )
                if suffix:
                    return suffix, f"随机:{label}"
            if not allow_llm_fallback and use_generic_fallback:
                return "思考中", "随机动态来源"
            return "", "随机动态来源"

        if source == "thought":
            suffix = await self._build_thought_suffix(
                event,
                state,
                settings,
                unified_msg_origin,
                allow_llm=allow_llm_fallback,
            )
            if not suffix and not allow_llm_fallback and use_generic_fallback:
                suffix = "思考中"
            state.thought_suffix = suffix
            state.thought_generated_at = now
            return suffix, "会话想法摘要"

        if source == "schedule":
            suffix = await self._build_schedule_suffix(
                event,
                settings,
                unified_msg_origin,
                allow_llm=allow_llm_fallback,
            )
            state.schedule_suffix = suffix
            state.schedule_generated_at = now
            return suffix, "当天日程"

        if source == "whim":
            suffix = await self._build_whim_suffix(
                event,
                settings,
                unified_msg_origin,
                allow_llm=allow_llm_fallback,
            )
            state.whim_suffix = suffix
            state.whim_generated_at = now
            return suffix, "随心后缀"

        return "", "手动后缀"

    async def _refresh_dynamic_suffixes(
        self,
        event: AstrMessageEvent,
        state: GroupCardState,
        settings: PluginSettings,
        now: float,
    ) -> None:
        state.clear_expired_manual(now)

        if settings.auto_include_schedule and now - state.schedule_generated_at >= settings.schedule_refresh_seconds:
            state.schedule_suffix = await self._build_schedule_suffix(event, settings)
            state.schedule_generated_at = now

        if settings.auto_include_whim and now - state.whim_generated_at >= settings.whim_refresh_seconds:
            state.whim_suffix = await self._build_whim_suffix(event, settings)
            state.whim_generated_at = now

        if settings.auto_include_thought and now - state.thought_generated_at >= settings.thought_refresh_seconds:
            state.thought_suffix = await self._build_thought_suffix(event, state, settings)
            state.thought_generated_at = now

    def _build_card(self, state: GroupCardState, settings: PluginSettings) -> str:
        now = time.time()
        state.clear_expired_manual(now)
        if state.has_active_manual_card(now):
            return clean_card(state.manual_full_card, settings.max_card_length, settings.max_card_bytes)

        metrics = self._collect_metrics()
        cpu_text = _render_template(settings.cpu_template, metrics) if settings.include_cpu else ""
        memory_text = _render_template(settings.memory_template, metrics) if settings.include_memory else ""
        time_text = _render_template(settings.time_template, metrics) if settings.include_time else ""
        metric_parts = [part for part in (_clean_text(cpu_text), _clean_text(memory_text), _clean_text(time_text)) if part]
        card_template = self._active_card_template(settings)
        manual_suffix = state.manual_suffix if state.has_active_manual_suffix(now) else ""
        thought_suffix = state.thought_suffix
        schedule_suffix = state.schedule_suffix
        whim_suffix = state.whim_suffix

        if manual_suffix:
            if "{thought_suffix}" in card_template and thought_suffix == manual_suffix:
                thought_suffix = ""
            if "{schedule_suffix}" in card_template and schedule_suffix == manual_suffix:
                schedule_suffix = ""
            if "{whim_suffix}" in card_template and whim_suffix == manual_suffix:
                whim_suffix = ""

        suffix_parts = self._build_suffix_parts(
            settings=settings,
            template=card_template,
            manual_suffix=manual_suffix,
            thought_suffix=thought_suffix,
            schedule_suffix=schedule_suffix,
            whim_suffix=whim_suffix,
        )

        values = {
            **metrics,
            "bot_name": settings.bot_name,
            "cpu_text": _clean_text(cpu_text),
            "memory_text": _clean_text(memory_text),
            "time_text": _clean_text(time_text),
            "metrics": " ".join(metric_parts),
            "suffixes": " ".join(suffix_parts),
            "manual_suffix": manual_suffix,
            "thought_suffix": thought_suffix,
            "schedule_suffix": schedule_suffix,
            "whim_suffix": whim_suffix,
            "static_suffix": settings.static_suffix,
        }
        card = _render_template(card_template, values)
        return clean_card(card, settings.max_card_length, settings.max_card_bytes)

    def _active_card_template(self, settings: PluginSettings) -> str:
        if settings.operation_mode == "tool_reminder":
            return settings.tool_reminder_card_template
        return settings.auto_card_template

    def _collect_metrics(self) -> dict[str, Any]:
        now = datetime.now()
        return {
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "memory": round(psutil.virtual_memory().percent, 1),
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
            "weekday": self._weekday_name(now),
        }

    def _build_suffix_parts(
        self,
        *,
        settings: PluginSettings,
        template: str,
        manual_suffix: str,
        thought_suffix: str,
        schedule_suffix: str,
        whim_suffix: str,
    ) -> list[str]:
        parts: list[str] = []
        if settings.static_suffix and "{static_suffix}" not in template:
            parts.append(settings.static_suffix)
        if manual_suffix and "{manual_suffix}" not in template:
            parts.append(manual_suffix)
        if (
            (settings.operation_mode == "tool_reminder" or settings.auto_include_schedule)
            and schedule_suffix
            and schedule_suffix != manual_suffix
            and "{schedule_suffix}" not in template
        ):
            parts.append(self._with_prefix(settings.schedule_prefix, schedule_suffix))
        if (
            (settings.operation_mode == "tool_reminder" or settings.auto_include_whim)
            and whim_suffix
            and whim_suffix != manual_suffix
            and "{whim_suffix}" not in template
        ):
            parts.append(self._with_prefix(settings.whim_prefix, whim_suffix))
        if (
            (settings.operation_mode == "tool_reminder" or settings.auto_include_thought)
            and thought_suffix
            and thought_suffix != manual_suffix
            and "{thought_suffix}" not in template
        ):
            parts.append(self._with_prefix(settings.thought_prefix, thought_suffix))
        return parts

    def _with_prefix(self, prefix: str, text: str) -> str:
        if not text:
            return ""
        return f"{prefix}{text}" if prefix else text

    async def _build_schedule_suffix(
        self,
        event: AstrMessageEvent | None,
        settings: PluginSettings,
        unified_msg_origin: str = "",
        *,
        allow_llm: bool = True,
    ) -> str:
        if settings.schedule_mode == "llm" and allow_llm:
            values = self._schedule_template_values()
            prompt = (
                f"{_render_template(settings.schedule_prompt, values)}\n"
                f"今天：{values['date']}，{values['weekday']}，当前时间：{values['time']}。\n"
                f"要求：不超过 {settings.schedule_max_length} 个字。"
            )
            generated = await self._llm_short_text(
                event,
                prompt,
                settings,
                settings.schedule_max_length,
                unified_msg_origin,
            )
            if generated:
                return generated
        return self._build_schedule_rule_suffix(settings)

    def _build_schedule_rule_suffix(self, settings: PluginSettings) -> str:
        values = self._schedule_template_values()
        selected = self._select_schedule_line(settings.schedule_lines, datetime.now())
        if not selected:
            selected = settings.schedule_empty_text
        return _truncate(_render_template(selected, values), settings.schedule_max_length)

    def _schedule_template_values(self) -> dict[str, str]:
        now = datetime.now()
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "weekday": self._weekday_name(now),
        }

    def _select_schedule_line(self, lines: list[str], now: datetime) -> str:
        if not lines:
            return ""

        key_groups = (
            {now.strftime("%Y-%m-%d")},
            {now.strftime("%m-%d")},
            {self._weekday_name(now), self._weekday_name(now, short=True),
             now.strftime("%A").lower(), now.strftime("%a").lower()},
        )
        matches: dict[int, str] = {}
        fallback = ""
        for line in lines:
            key, value = self._split_schedule_line(line)
            if not key:
                fallback = fallback or value
                continue
            normalized_key = key.strip().lower()
            if normalized_key in {"daily", "everyday", "每天", "每日"}:
                fallback = fallback or value
                continue
            for priority, keys in enumerate(key_groups):
                if normalized_key in keys:
                    matches.setdefault(priority, value)
                    break
        return matches[min(matches)] if matches else fallback

    def _split_schedule_line(self, line: str) -> tuple[str, str]:
        text = _clean_text(line)
        for separator in ("=", "：", ":"):
            if separator not in text:
                continue
            key, value = text.split(separator, 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                return key, value
        return "", text

    async def _build_whim_suffix(
        self,
        event: AstrMessageEvent | None,
        settings: PluginSettings,
        unified_msg_origin: str = "",
        *,
        allow_llm: bool = True,
    ) -> str:
        if settings.whim_mode == "llm" and allow_llm:
            prompt = (
                f"{settings.whim_prompt}\n"
                f"要求：不超过 {settings.whim_max_length} 个字。"
            )
            generated = await self._llm_short_text(
                event,
                prompt,
                settings,
                settings.whim_max_length,
                unified_msg_origin,
            )
            if generated:
                return generated
        if not settings.whim_pool:
            return ""
        return _truncate(random.choice(settings.whim_pool), settings.whim_max_length)

    async def _build_thought_suffix(
        self,
        event: AstrMessageEvent | None,
        state: GroupCardState,
        settings: PluginSettings,
        unified_msg_origin: str = "",
        *,
        allow_llm: bool = True,
    ) -> str:
        if not state.recent_messages:
            return ""

        if not allow_llm:
            return ""

        context_text = "\n".join(list(state.recent_messages)[-settings.thought_context_messages :])
        prompt = (
            f"{settings.thought_prompt}\n"
            f"要求：不超过 {settings.thought_max_length} 个字。\n\n"
            f"最近对话：\n{context_text}"
        )
        return await self._llm_short_text(
            event,
            prompt,
            settings,
            settings.thought_max_length,
            unified_msg_origin,
        )

    async def _llm_short_text(
        self,
        event: AstrMessageEvent | None,
        prompt: str,
        settings: PluginSettings,
        max_length: int,
        unified_msg_origin: str = "",
    ) -> str:
        provider_id = settings.llm_provider_id
        if not provider_id:
            umo = unified_msg_origin
            if not umo and event is not None:
                umo = _normalize_id(getattr(event, "unified_msg_origin", ""))
            try:
                provider_id = await self.context.get_current_chat_provider_id(umo)
            except Exception as exc:
                logger.warning("[%s] cannot resolve current chat provider: %r", PLUGIN_ID, exc)
                return ""
        if not provider_id:
            return ""

        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=(
                    "你正在生成 QQ 群名片上的极短后缀。"
                    "只输出后缀文本，不要解释，不要 Markdown。"
                ),
            )
        except Exception as exc:
            logger.warning("[%s] llm suffix generation failed: %r", PLUGIN_ID, exc)
            return ""

        text = _clean_text(getattr(response, "completion_text", ""))
        return _first_clean_line(text, max_length)

    async def _set_group_card(
        self, *, client: Any, group_id: str, self_id: str, card: str, retry_count: int,
    ) -> CardUpdateResult:
        settings = self._settings()
        result = await OneBotCardClient(
            client, self_id, timeout=settings.api_timeout_seconds,
        ).update(
            group_id, card, attempts=retry_count,
            retry_delay=settings.retry_delay_seconds, verify=settings.verify_after_write,
        )
        log = logger.info if result.ok else logger.warning
        log("[%s] set_group_card group=%s self_id=%s ok=%s verified=%s attempts=%s chars=%s bytes=%s detail=%s",
            PLUGIN_ID, group_id, self_id, result.ok, result.verified, result.attempts,
            len(card), len(card.encode("utf-8")), result.detail or "-")
        return result

    def _record_update_result(
        self, state: GroupCardState, result: CardUpdateResult, settings: PluginSettings,
    ) -> None:
        state.last_error = result.detail
        state.last_verified = result.verified
        state.retry_after = 0 if result.ok else time.time() + settings.failure_cooldown_seconds

    @filter.command("名片预览")
    async def preview_card(self, event: AstrMessageEvent, suffix: str = ""):
        """预览当前模板，可在命令后填写一个后缀；不会修改 QQ 群名片。"""
        event.set_extra("dynamic_card_readonly", True)
        context = self._extract_group_context(event)
        if context is None:
            yield event.plain_result("请在 OneBot 接入的 QQ 群里使用。")
            return
        _, group_id, self_id = context
        settings = self._settings()
        if self._is_blacklisted(event, group_id, settings):
            yield event.plain_result("这个群或会话已禁用动态名片。")
            return
        state = copy.copy(self._states[self._state_key(event, group_id, self_id)])
        if suffix:
            state.manual_full_card = ""
            state.manual_suffix = suffix
            state.manual_until = 0
            if settings.operation_mode == "tool_reminder":
                self._clear_dynamic_suffixes(state)
        card = self._build_card(state, settings)
        yield event.plain_result(f"名片预览：{card or '（空）'}\n{len(card)} 个字符 / {len(card.encode('utf-8'))} 字节；未修改群名片。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("名片检查")
    async def diagnose_card(self, event: AstrMessageEvent):
        """检查连接、账号、当前群名片及最近的更新结果，不执行改名。"""
        event.set_extra("dynamic_card_readonly", True)
        context = self._extract_group_context(event)
        if context is None:
            yield event.plain_result("请在 OneBot 接入的 QQ 群里使用。")
            return
        client, group_id, self_id = context
        settings = self._settings()
        state = self._states[self._state_key(event, group_id, self_id)]
        api = OneBotCardClient(client, self_id, timeout=settings.api_timeout_seconds)
        mode = "自动更新（回复时触发）" if settings.operation_mode == "auto_update" else "提醒模型改名片"
        enabled = settings.enabled and not self._is_blacklisted(event, group_id, settings)
        lines = [f"动态名片：{'启用' if enabled else '禁用'}；{mode}", f"群号：{group_id}；机器人：{self_id}"]
        try:
            version = await api.call("get_version_info")
            if isinstance(version, dict):
                lines.append(f"客户端：{version.get('app_name', '未知')} {version.get('app_version', '')}")
        except Exception as exc:
            lines.append(f"客户端信息：{failure_detail(exc)[0]}")
        try:
            member = await api.member(group_id)
            lines.append(f"当前名片：{member['card'] or '（未设置）'}；群角色：{member.get('role', '未知')}")
        except Exception as exc:
            lines.append(f"读取成员信息：{failure_detail(exc)[0]}")
        if state.last_card:
            lines.append(f"上次提交：{state.last_card}；{'已回读确认' if state.last_verified else '未回读确认'}")
        if state.last_error:
            lines.append(f"最近提示：{state.last_error}")
        if state.retry_after > time.time():
            lines.append(f"失败冷却剩余：{int(state.retry_after - time.time()) + 1} 秒")
        lines.append("本次只检查，不修改群名片。")
        yield event.plain_result("\n".join(lines))

    def _weekday_name(self, when: datetime, *, short: bool = False) -> str:
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        full_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return names[when.weekday()] if short else full_names[when.weekday()]
