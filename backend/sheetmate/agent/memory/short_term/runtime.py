"""Small, transport-independent durable state used by a serial agent loop."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any


class AgentRuntime:
    def __init__(
        self,
        state: dict[str, Any] | None = None,
        *,
        save: Callable[[dict[str, Any]], Awaitable[None]],
        poll: Callable[[], Awaitable[list[dict[str, Any]]]],
        start_tool: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]],
        finish_tool: Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]],
    ):
        self.state = restore_runtime_state(state or {})
        self._save, self._poll = save, poll
        self._start_tool, self._finish_tool = start_tool, finish_tool

    @property
    def revision(self) -> int:
        return int(self.state.get("revision", 0))

    def effective_message(self, original: str) -> str:
        steers = [str(item.get("content", "")) for item in self.state.get("steers", []) if item.get("content")]
        result = original
        if steers:
            result += ("\n\n用户在执行中补充或更正了以下要求（按时间顺序，后面的更正优先）：\n"
                       + "\n".join(f"{index}. {text}" for index, text in enumerate(steers, 1)))
        planning = self.state.get("planning_state") or {}
        acceptance = (planning.get("plan") or {}).get("acceptance") or []
        issues = list(self.state.get("validation_issues") or [])
        for values in (planning.get("failed_checks") or {}).values():
            issues.extend(values)
        if acceptance:
            result += "\n原计划中仍有效的整体验收：\n" + "\n".join(str(item) for item in acceptance)
        if issues:
            result += "\n之前验收尚未通过的问题，必须实际解决后重新核对：\n" + "\n".join(dict.fromkeys(issues))
        if steers or acceptance or issues:
            result += "\n请复用已经完成的真实成果，依据更新后的完整目标继续，并验收所有仍有效的要求。"
        return result

    async def consume(self, ctx: Any) -> list[dict[str, Any]]:
        known = {str(item["message_id"]) for item in self.state.get("steers", [])}
        fresh = []
        messages = await self._poll()
        host = getattr(ctx, "host", None)
        if hasattr(host, "save_recipe_requested"):
            ctx.save_recipe_requested = host.save_recipe_requested
        for item in messages:
            message_id = item.get("message_id")
            content = str(item.get("content") or "").strip()
            if message_id is None or str(message_id) in known or not content:
                continue
            clean = {"message_id": message_id, "content": content}
            fresh.append(clean)
            known.add(str(message_id))
        if fresh:
            self.state.setdefault("steers", []).extend(fresh)
            self.state["revision"] = self.revision + len(fresh)
            # Capability discovery and other side paths must observe the same
            # current request as the main transcript.
            ctx.latest_user_text = fresh[-1]["content"]
        return fresh

    async def checkpoint(self) -> None:
        await self._save(compact_runtime_state(self.state))

    async def started(self, call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        return await self._start_tool(call_id, name, args)

    async def finished(self, call_id: str, result: dict[str, Any]) -> None:
        await self._finish_tool(call_id, result, compact_runtime_state(self.state))


def compact_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    """Store shared loop snapshots once without dropping recovery information."""
    result = durable_value(state)
    loop = result.get("loop")
    if isinstance(loop, dict):
        shared = dict(loop.get("_shared_state") or {})
        for nested, root in (("messages", "conversation"), ("budget", "budget")):
            if nested in loop and root in result and loop[nested] == result[root]:
                loop.pop(nested)
                shared[nested] = root
        if shared:
            loop["_shared_state"] = shared
    return result


def restore_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    """Expand compact snapshots; older checkpoints already contain both copies."""
    result = copy.deepcopy(state)
    loop = result.get("loop")
    if isinstance(loop, dict):
        shared = loop.pop("_shared_state", {})
        for nested, root in (("messages", "conversation"), ("budget", "budget")):
            if shared.get(nested) == root and nested not in loop and root in result:
                loop[nested] = copy.deepcopy(result[root])
    return result


def durable_value(value: Any) -> Any:
    """Project snapshots, leaving source messages and live reasoning intact.

    Image bytes are replaced with ordinal references and reattached from the
    authorized request on recovery. Raw provider reasoning is never durable.
    Credential rejection is enforced by the database persistence boundary.
    """
    images = 0

    def project(item: Any) -> Any:
        nonlocal images
        if isinstance(item, dict):
            if isinstance(item.get("type"), str) and item["type"] in {"image_url", "input_image"}:
                index, images = images, images + 1
                return {"_runtime_image": index, "_runtime_image_digest": _image_digest(item)}
            return {str(key): project(child) for key, child in item.items()
                    if key not in {"reasoning", "reasoning_content"}}
        if isinstance(item, (list, tuple)):
            return [project(child) for child in item]
        return copy.deepcopy(item)

    return project(value)


def _image_digest(part: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(part, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def runtime_images(ctx: Any) -> list[dict[str, Any]]:
    return getattr(ctx, "runtime_image_parts", ctx.image_parts)


def restore_messages(messages: list[dict[str, Any]], image_parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = [part for part in image_parts if isinstance(part.get("type"), str) and part["type"] in {"image_url", "input_image"}]
    by_digest = {_image_digest(part): part for part in images}

    def restore(item: Any) -> Any:
        if isinstance(item, dict):
            if "_runtime_image" in item:
                digest = item.get("_runtime_image_digest")
                if digest is not None:
                    if digest not in by_digest:
                        raise ValueError("恢复所需的历史图片不可用，请重新发起请求。")
                    return copy.deepcopy(by_digest[digest])
                index = item["_runtime_image"]
                if not isinstance(index, int) or index < 0 or index >= len(images):
                    raise ValueError("恢复所需的图片不可用，请重新发起请求。")
                return copy.deepcopy(images[index])
            return {key: restore(child) for key, child in item.items()}
        if isinstance(item, list):
            return [restore(child) for child in item]
        return copy.deepcopy(item)

    return restore(messages)
