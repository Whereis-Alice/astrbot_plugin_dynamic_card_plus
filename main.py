from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
from contextlib import suppress
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psutil
from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.api import AstrBotConfig, FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


PLUGIN_ID = "astrbot_plugin_dynamic_card_plus"
PLUGIN_VERSION = "0.8.7"
PLUGIN_DESC = "??????????????????????????????? LLM ?????"
PLUGIN_REPO = "https://github.com/Whereis-Alice/astrbot_plugin_dynamic_card_plus"

UPSTREAM_REPO = "https://github.com/zgojin/astrbot_plugin_botName"
CARD_TOOL_NAME = "set_dynamic_group_card"
CARD_HINT_MARKER = "[DynamicCardPlus]"
DEFAULT_TOOL_DESCRIPTION = (
    "???? QQ ???????"
    "????????????????????????"
    "???? source=thought?schedule?whim?random ????????"
    "???????????????????????????"
    "??????????????????"
)
DEFAULT_WEEK_SCHEDULE_LINES = [
    "??=?????",
    "??=????",
    "??=????",
    "??=????",
    "??=??????",
    "??=????",
    "??=????",
]
DEFAULT_SCHEDULE_PROMPT = (
    "?????????????? QQ ???????????"
    "???????????????????????"
)


def _clean_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _read_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled", "??", "?"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled", "??", "?"}:
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
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


def _read_list(value: Any, default: list[str] | None = None) -> list[str]:
    fallback = list(default or [])
    if isinstance(value, list):
        items = [_clean_text(item) for item in value]
        return [item for item in items if item]
    if isinstance(value, str):
        normalized = value.replace("?", ",").replace("?", ";")
        items = [
            item.strip()
            for chunk in normalized.split(";")
            for item in chunk.split(",")
        ]
        return [item for item in items if item]
    return fallback


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
        return template


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_clean_line(text: str, max_length: int) -> str:
    cleaned = text.replace("\r", "\n").strip()
    if not cleaned:
        return ""
    line = cleaned.splitlines()[0].strip()
    line = re.sub(r"^[-*#\s`\"'????????]+", "", line)
    line = line.strip("`\"'????????")
    return _truncate(line, max_length)


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool
    debug_log: bool
    operation_mode: str
    max_card_length: int
    retry_count: int
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
    tool_reminder_source: str
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
    last_update_at: float = 0.0
    last_card: str = ""
    last_tool_update_at: float = 0.0
    last_tool_reminder_at: float = 0.0
    last_tool_reason: str = ""

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
                    "description": "?????suffix ??????full_card ????????clear_manual ????????????",
                    "enum": ["suffix", "full_card", "clear_manual"],
                },
                "suffix": {
                    "type": "string",
                    "description": "mode=suffix ? source=manual ??????????????????????????????",
                },
                "source": {
                    "type": "string",
                    "description": "mode=suffix ???????manual ?? suffix?thought ???????????schedule ???????whim ?????random ???????????",
                    "enum": ["manual", "thought", "schedule", "whim", "random"],
                },
                "full_card": {
                    "type": "string",
                    "description": "mode=full_card ????????????????????????",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "????????????????????0 ??????? clear_manual ??????",
                },
                "reason": {
                    "type": "string",
                    "description": "????????????????????????",
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
            return "???DynamicCardPlus ????????????????"
        event = getattr(context.context, "event", None)
        if event is None:
            return "?????????????????????????"
        return await self.plugin.handle_tool_call(event, kwargs)


@register(PLUGIN_ID, "Huli3", PLUGIN_DESC, PLUGIN_VERSION, PLUGIN_REPO)
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
        self._active_cron_jobs: dict[str, str] = {}
        self._active_cron_register_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_cron_db_lock = asyncio.Lock()
        self._register_llm_tool()

    async def initialize(self) -> None:
        logger.info("[%s] initialized; upstream=%s", PLUGIN_ID, UPSTREAM_REPO)

    async def terminate(self) -> None:
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
                description="Dynamic Card Plus ???? bot ????",
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

        minutes = max(1, round(settings.tool_reminder_interval_seconds / 60))
        if minutes < 60:
            return f"*/{minutes} * * * *"
        if minutes % 60 == 0:
            hours = max(1, min(23, minutes // 60))
            return f"0 */{hours} * * *"
        return "*/30 * * * *"

    def _active_cron_note(self, settings: PluginSettings) -> str:
        source = settings.tool_reminder_source
        return self._required_group_card_tool_prompt(
            current_card="",
            source=source,
            source_label="????",
            suggestion="???? source ???????????",
            reason="???????????",
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
        current_card_text = f"??????????{current_card}?" if current_card else ""
        trigger_text = f"???????{trigger_id}?" if trigger_id else ""
        return (
            f"{CARD_HINT_MARKER} ????????"
            f"{current_card_text}"
            f"{trigger_text}"
            f"??????? {CARD_TOOL_NAME} ???? QQ ???????"
            "????????????????????????????????????????????????"
            "?????????????????????????????"
            f"??? assistant ????????? {CARD_TOOL_NAME}?????????????????"
            f"?????mode=suffix?source={source}?reason={reason}?"
            f"?????{source_label}?{suggestion}"
            "???????????????????????????????? source ?????"
            "??????????????????????"
            "??????????????????"
            "???????????????????????????"
            "????????????????????????? mode=suffix?"
            "?????????????????????????????"
            "????????????????????????????"
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
            tool_reminder_source=reminder_source,
            tool_reminder_active_cron_expression=_clean_text(reminder_mode.get("active_cron_expression")),
            thought_refresh_seconds=_read_int(
                thought.get("refresh_seconds"),
                1800,
                minimum=30,
                maximum=604800,
            ),
            thought_prefix=_clean_text(thought.get("prefix"), "??:"),
            thought_prompt=_clean_text(
                thought.get("prompt"),
                (
                    "??????????????????? QQ ???????????"
                    "???????????????????"
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
            schedule_prefix=_clean_text(schedule.get("prefix"), "??:"),
            schedule_prompt=_clean_text(schedule.get("prompt"), DEFAULT_SCHEDULE_PROMPT),
            schedule_lines=_read_list(schedule.get("schedule_lines"), DEFAULT_WEEK_SCHEDULE_LINES),
            schedule_empty_text=_clean_text(schedule.get("empty_text"), "????"),
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
                ["??????", "??????", "??????", "??????"],
            ),
            whim_prompt=_clean_text(
                whim.get("prompt"),
                (
                    "???????????????? QQ ?????????"
                    "????????????????????????"
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

    @filter.on_llm_request()
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

        state = self._states[_normalize_id(group_id)]
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        self._remember_user_message(state, event, settings)
        if settings.operation_mode != "tool_reminder":
            return

        if settings.tool_reminder_trigger_mode != "llm_request":
            return

        now = time.time()
        if now - state.last_tool_reminder_at < settings.tool_reminder_interval_seconds:
            return

        current_card = state.last_card or "?????"
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
            reason="?????????",
            trigger_id=f"{group_id}-{int(now)}",
        )
        if not self._append_provider_hint(req, hint):
            return
        tool_names = self._request_tool_names(req)
        has_tool = CARD_TOOL_NAME in tool_names
        state.last_tool_reminder_at = now
        logger.info(
            "[%s] injected tool reminder group=%s source=%s has_tool=%s tool_count=%s",
            PLUGIN_ID,
            group_id,
            source,
            has_tool,
            len(tool_names),
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

    def _append_provider_hint(self, req: ProviderRequest, hint: str) -> bool:
        return self._append_temp_user_hint(req, hint)

    def _append_temp_user_hint(self, req: ProviderRequest, hint: str) -> bool:
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            return False
        for part in parts:
            if CARD_HINT_MARKER in _clean_text(getattr(part, "text", "")):
                return False
        part = TextPart(text=hint)
        mark_as_temp = getattr(part, "mark_as_temp", None)
        if callable(mark_as_temp):
            mark_as_temp()
        parts.append(part)
        return True

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

    @filter.on_decorating_result()
    async def modify_card_before_send(self, event: AstrMessageEvent) -> None:
        settings = self._settings()
        if not settings.enabled:
            return

        group_context = self._extract_group_context(event)
        if group_context is None:
            return

        client, group_id, self_id = group_context
        if self._is_blacklisted(event, group_id, settings):
            return

        group_key = _normalize_id(group_id)
        state = self._states[group_key]
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        self._remember_exchange(state, event, settings)
        if settings.operation_mode != "auto_update":
            return

        now = time.time()
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
        if ok:
            state.last_card = new_card
            state.last_update_at = now

    async def handle_tool_call(
        self,
        event: AstrMessageEvent,
        kwargs: dict[str, Any],
    ) -> str:
        settings = self._settings()
        if not settings.enabled:
            return "???DynamicCardPlus ??????????"
        if not settings.llm_tool_enabled:
            return "?????? LLM ????????????"

        group_context = self._extract_group_context(event)
        if group_context is None:
            return "?????? aiocqhttp ? QQ ?????????"

        client, group_id, self_id = group_context
        if self._is_blacklisted(event, group_id, settings):
            return f"???? {group_id} ?????????????????"

        group_key = _normalize_id(group_id)
        state = self._states[group_key]
        await self._remember_group_target(state, event, client, group_id, self_id, settings)
        now = time.time()
        cooldown_left = settings.llm_tool_min_interval_seconds - (now - state.last_tool_update_at)
        if cooldown_left > 0:
            return f"??????????????? {int(cooldown_left)} ?????"

        mode = _clean_text(kwargs.get("mode"), "suffix")
        reason = _clean_text(kwargs.get("reason"))
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
                return "???????? LLM ????????????"
            state.manual_full_card = _truncate(
                _clean_text(kwargs.get("full_card")),
                min(settings.llm_tool_max_length, settings.max_card_length),
            )
            state.manual_suffix = ""
            state.last_tool_reason = reason
            if not state.manual_full_card:
                return "???full_card ??????????"
        else:
            if settings.operation_mode == "tool_reminder":
                self._clear_dynamic_suffixes(state)
            source = _clean_text(kwargs.get("source"), "manual")
            if source not in {"manual", "thought", "schedule", "whim", "random"}:
                source = "manual"
            suffix = _clean_text(kwargs.get("suffix"))
            source_label = "????"
            if source != "manual" or not suffix:
                suffix, source_label = await self._build_suffix_from_source(
                    event=event,
                    state=state,
                    settings=settings,
                    source=source,
                    now=now,
                    unified_msg_origin=_normalize_id(getattr(event, "unified_msg_origin", "")),
                )
            state.manual_suffix = _truncate(suffix, settings.llm_tool_max_length)
            state.manual_full_card = ""
            state.last_tool_reason = reason or source_label
            if not state.manual_suffix:
                return "???suffix ??????????"

        new_card = self._build_card(state, settings)
        if not new_card:
            return "????????????????"

        ok = await self._set_group_card(
            client=client,
            group_id=group_id,
            self_id=self_id,
            card=new_card,
            retry_count=settings.retry_count,
        )
        if not ok:
            return f"???????? {group_id} ???????"

        state.last_card = new_card
        state.last_update_at = now
        state.last_tool_update_at = now
        logger.info(
            "[%s] LLM tool changed group=%s card=%s reason=%s",
            PLUGIN_ID,
            group_id,
            new_card,
            reason or "-",
        )
        suffix_note = f"????{state.last_tool_reason}" if state.last_tool_reason else ""
        return f"??????????{new_card}{suffix_note}"

    def _clear_dynamic_suffixes(self, state: GroupCardState) -> None:
        state.thought_suffix = ""
        state.schedule_suffix = ""
        state.whim_suffix = ""
        state.thought_generated_at = 0.0
        state.schedule_generated_at = 0.0
        state.whim_generated_at = 0.0

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
        for state in self._states.values():
            if state.unified_msg_origin != unified_msg_origin:
                continue
            if state.client and state.group_id and state.self_id:
                return state.client, state.group_id, state.self_id
        return None

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
            await self._ensure_active_cron_job(state.group_id, state, settings)

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
        state.recent_messages.append(f"??: {_truncate(user_text, max_chars)}")
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
        state.recent_messages.append(f"?: {_truncate(bot_text, max_chars)}")

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
        source = settings.tool_reminder_source
        if source == "random":
            source = random.choice(["thought", "schedule", "whim"])

        if source == "thought":
            return (
                "????????????",
                "??????",
                "thought",
            )

        if source == "schedule":
            if settings.schedule_mode == "llm":
                return (
                    "????????????????? LLM ???????",
                    "????",
                    "schedule",
                )
            schedule = self._build_schedule_rule_suffix(settings)
            if schedule:
                return (
                    f"???????????{schedule}??",
                    "????",
                    "schedule",
                )
            return (
                "??????????",
                "????",
                "schedule",
            )

        if source == "whim":
            if settings.whim_mode == "pool" and settings.whim_pool:
                whim = _truncate(random.choice(settings.whim_pool), settings.whim_max_length)
                return (
                    f"?????????{whim}??",
                    "????",
                    "whim",
                )
            return (
                "????????????????",
                "????",
                "whim",
            )

        return "??????????????", "????", "whim"

    async def _build_suffix_from_source(
        self,
        *,
        event: AstrMessageEvent | None,
        state: GroupCardState,
        settings: PluginSettings,
        source: str,
        now: float,
        unified_msg_origin: str = "",
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
                )
                if suffix:
                    return suffix, f"??:{label}"
            return "", "??????"

        if source == "thought":
            suffix = await self._build_thought_suffix(event, state, settings, unified_msg_origin)
            state.thought_suffix = suffix
            state.thought_generated_at = now
            return suffix, "??????"

        if source == "schedule":
            suffix = await self._build_schedule_suffix(event, settings, unified_msg_origin)
            state.schedule_suffix = suffix
            state.schedule_generated_at = now
            return suffix, "????"

        if source == "whim":
            suffix = await self._build_whim_suffix(event, settings, unified_msg_origin)
            state.whim_suffix = suffix
            state.whim_generated_at = now
            return suffix, "????"

        return "", "????"

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
            return _truncate(_compact_spaces(state.manual_full_card), settings.max_card_length)

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

        if manual_suffix and "{manual_suffix}" in card_template:
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
        return _truncate(_compact_spaces(card), settings.max_card_length)

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
    ) -> str:
        if settings.schedule_mode == "llm":
            values = self._schedule_template_values()
            prompt = (
                f"{_render_template(settings.schedule_prompt, values)}\n"
                f"???{values['date']}?{values['weekday']}??????{values['time']}?\n"
                f"?????? {settings.schedule_max_length} ???"
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

        exact_keys = {
            now.strftime("%Y-%m-%d"),
            now.strftime("%m-%d"),
            self._weekday_name(now),
            self._weekday_name(now, short=True),
            now.strftime("%A").lower(),
            now.strftime("%a").lower(),
        }
        fallback = ""
        for line in lines:
            key, value = self._split_schedule_line(line)
            if not key:
                fallback = fallback or value
                continue
            normalized_key = key.strip().lower()
            if normalized_key in {"daily", "everyday", "??", "??"}:
                fallback = fallback or value
                continue
            if normalized_key in exact_keys:
                return value
        return fallback

    def _split_schedule_line(self, line: str) -> tuple[str, str]:
        text = _clean_text(line)
        for separator in ("=", "?", ":"):
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
    ) -> str:
        if settings.whim_mode == "llm":
            prompt = (
                f"{settings.whim_prompt}\n"
                f"?????? {settings.whim_max_length} ???"
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
    ) -> str:
        if not state.recent_messages:
            return ""

        context_text = "\n".join(list(state.recent_messages)[-settings.thought_context_messages :])
        prompt = (
            f"{settings.thought_prompt}\n"
            f"?????? {settings.thought_max_length} ???\n\n"
            f"?????\n{context_text}"
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
                    "????? QQ ??????????"
                    "??????????????? Markdown?"
                ),
            )
        except Exception as exc:
            logger.warning("[%s] llm suffix generation failed: %r", PLUGIN_ID, exc)
            return ""

        text = _clean_text(getattr(response, "completion_text", ""))
        return _first_clean_line(text, max_length)

    async def _set_group_card(
        self,
        *,
        client: Any,
        group_id: str,
        self_id: str,
        card: str,
        retry_count: int,
    ) -> bool:
        payload = {
            "group_id": group_id,
            "user_id": self_id,
            "card": card,
        }
        for retry in range(retry_count):
            try:
                result = await client.api.call_action("set_group_card", **payload)
                logger.info(
                    "[%s] set_group_card succeeded group=%s card=%s result=%s",
                    PLUGIN_ID,
                    group_id,
                    card,
                    result,
                )
                return True
            except Exception as exc:
                if retry < retry_count - 1:
                    logger.warning(
                        "[%s] set_group_card retry=%s group=%s error=%r",
                        PLUGIN_ID,
                        retry + 1,
                        group_id,
                        exc,
                    )
                else:
                    logger.error(
                        "[%s] set_group_card failed group=%s retries=%s error=%r",
                        PLUGIN_ID,
                        group_id,
                        retry_count,
                        exc,
                    )
        return False

    def _weekday_name(self, when: datetime, *, short: bool = False) -> str:
        names = ["??", "??", "??", "??", "??", "??", "??"]
        full_names = ["???", "???", "???", "???", "???", "???", "???"]
        return names[when.weekday()] if short else full_names[when.weekday()]
