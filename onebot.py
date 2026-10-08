"""Bounded OneBot v11 calls and verifiable group-card updates.

No backend-specific private API is needed by LLOneBot, LLBot or SnowLuma.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


def clean_card(text: str, max_chars: int, max_bytes: int) -> str:
    """Keep emoji joiners but discard control/surrogate and bidi-control text."""
    text = " ".join(str(text).split())
    text = "".join(
        char for char in text
        if unicodedata.category(char) not in {"Cc", "Cs"}
        and char not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    )
    return text[:max_chars].encode("utf-8")[:max_bytes].decode("utf-8", "ignore").rstrip()


class OneBotFailure(Exception):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(str(result.get("message") or result.get("wording") or ""))


def failure_detail(exc: Exception) -> tuple[str, bool]:
    """Return a useful diagnostic and whether repeating the write may help."""
    result = getattr(exc, "result", {})
    result = result if isinstance(result, dict) else {}
    code = result.get("retcode", getattr(exc, "retcode", None))
    message = str(result.get("message") or result.get("wording") or "").strip()
    if not message and not result:
        message = str(exc).strip() or type(exc).__name__
    message = re.sub(r"\s+", " ", message)[:200]
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "接口超时；请检查 OneBot 连接和客户端日志", True
    if code in (400, 1400, 404, 1404) or any(
        word in message.lower()
        for word in ("权限", "禁止", "permission", "not allowed", "not supported", "不支持")
    ):
        return f"接口拒绝请求（retcode={code}）：{message or '请检查接口支持情况和参数'}", False
    if code is not None:
        detail = message or "客户端未提供原因；请检查同一时间的 LLOneBot/llbot/SnowLuma 日志"
        return f"OneBot 执行失败（retcode={code}）：{detail}", True
    return f"OneBot 调用失败：{message or type(exc).__name__}", True


@dataclass(frozen=True)
class CardUpdateResult:
    ok: bool
    verified: bool = False
    attempts: int = 0
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


class OneBotCardClient:
    def __init__(self, client: Any, self_id: str, *, timeout: float = 10):
        self.client = client
        self.self_id = str(self_id)
        self.timeout = timeout

    async def call(self, action: str, **params: Any) -> Any:
        call_action = getattr(self.client, "call_action", None)
        if not callable(call_action):
            call_action = getattr(getattr(self.client, "api", None), "call_action", None)
        if not callable(call_action):
            raise RuntimeError("当前连接没有 OneBot call_action 接口")
        # aiocqhttp uses self_id to route cron/tool calls outside a message task.
        result = await asyncio.wait_for(
            call_action(action, self_id=self.self_id, **params), timeout=self.timeout,
        )
        # aiocqhttp unwraps data; other wrappers may return the full envelope.
        if isinstance(result, dict) and "status" in result and "retcode" in result:
            if result["status"] != "ok" or result["retcode"] not in (0, "0"):
                raise OneBotFailure(result)
            return result.get("data")
        return result

    async def member(self, group_id: str) -> dict[str, Any]:
        result = await self.call(
            "get_group_member_info", group_id=int(group_id),
            user_id=int(self.self_id), no_cache=True,
        )
        if not isinstance(result, dict) or not isinstance(result.get("card"), str):
            raise RuntimeError("成员信息没有返回 card 字段")
        # Never verify another account/group if a wrapper returns stale data.
        for key, expected in (("user_id", self.self_id), ("group_id", group_id)):
            if key in result and str(result[key]) != str(expected):
                raise RuntimeError("成员信息的账号或群号与请求不符")
        return result

    async def update(
        self, group_id: str, card: str, *, attempts: int = 3,
        retry_delay: float = 2, verify: bool = True,
    ) -> CardUpdateResult:
        if not all(re.fullmatch(r"[1-9][0-9]*", str(value)) for value in (group_id, self.self_id)):
            return CardUpdateResult(False, detail="群号或机器人 QQ 号无效")
        if not card:
            return CardUpdateResult(False, detail="生成的群名片为空")

        attempts = max(1, min(10, attempts))
        detail = ""
        for attempt in range(1, attempts + 1):
            accepted = False
            retryable = True
            try:
                await self.call(
                    "set_group_card", group_id=int(group_id),
                    user_id=int(self.self_id), card=card,
                )
                accepted = True
            except Exception as exc:
                detail, retryable = failure_detail(exc)

            if accepted and not verify:
                return CardUpdateResult(True, attempts=attempt, detail="接口已接受，未启用回读校验")

            # A backend may apply the card before reporting an error. Reading
            # also refreshes LLBot's member/UID cache before any delayed retry.
            for read_attempt in range(2 if accepted else 1):
                if read_attempt:
                    await asyncio.sleep(1)
                try:
                    actual = await self.member(group_id)
                except Exception as exc:
                    read_detail, _ = failure_detail(exc)
                    if accepted:
                        return CardUpdateResult(
                            True, attempts=attempt,
                            detail=f"接口已接受，回读不可用，尚未确认实际名片。{read_detail}",
                        )
                    detail = f"{detail}；回读失败：{read_detail}"
                    break
                if actual["card"] == card:
                    return CardUpdateResult(True, verified=True, attempts=attempt)
                if accepted:
                    detail = "接口已接受，但回读的群名片仍不一致；请检查客户端日志、QQ 限制或其他改名片插件"

            if not retryable or attempt == attempts:
                break
            await asyncio.sleep(min(30, max(1, retry_delay) * 2 ** (attempt - 1)))
        return CardUpdateResult(False, attempts=attempt, detail=detail)
