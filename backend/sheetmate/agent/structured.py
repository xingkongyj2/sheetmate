"""Shared contracts for model responses and tool arguments.

Validation never coerces types or fills in business values.  The same schema
is used locally regardless of the serving endpoint's generation capabilities.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jsonschema import Draft202012Validator


class StructuredOutputError(ValueError):
    """A response cannot be used; errors contain JSON pointer field paths."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("；".join(f"{item['path']}: {item['message']}" for item in errors))


def _pointer(parts: Any) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def validate_payload(value: Any, schema: dict[str, Any]) -> Any:
    """Validate a JSON value without altering it or including its data in errors."""
    validator = Draft202012Validator(schema)
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for error in validator.iter_errors(value):
        path = list(error.absolute_path)
        if error.validator == "required":
            missing = [key for key in error.validator_value if key not in error.instance]
            for key in missing:
                entry = (_pointer([*path, key]), "缺少必填字段")
                if entry not in seen:
                    errors.append({"path": entry[0], "message": entry[1]})
                    seen.add(entry)
                if len(errors) >= 6:
                    break
            if len(errors) >= 6:
                break
            continue
        elif error.validator == "type":
            message = f"类型应为 {error.validator_value}，不能自动转换类型"
        elif error.validator == "enum":
            message = f"必须使用允许的值：{error.validator_value}"
        elif error.validator == "additionalProperties":
            message = "存在未声明的字段，请使用 schema 中的字段名"
        elif error.validator in {"oneOf", "anyOf"}:
            message = "不符合任何允许的结构，请检查必填字段和条件分支"
        else:
            # jsonschema's full messages can contain an entire SQL/script value.
            message = f"不符合 {error.validator} 约束：{str(error.validator_value)[:180]}"
        entry = (_pointer(path), message)
        if entry not in seen:
            errors.append({"path": entry[0], "message": entry[1]})
            seen.add(entry)
        if len(errors) >= 6:
            break
    if errors:
        raise StructuredOutputError(errors)
    return value


async def complete_structured(llm: Any, prompt: str, *, schema: dict[str, Any],
                              schema_name: str = "response", system: str | None = None,
                              validator: Callable[[Any], None] | None = None, **kwargs: Any) -> Any:
    """Request a contract and also validate clients substituted by callers/tests."""
    extra = {"validator": validator} if validator is not None else {}
    value = await llm.complete_json(prompt, system=system, schema=schema, schema_name=schema_name, **extra, **kwargs)
    validate_payload(value, schema)
    if validator is not None:
        validator(value)
    return value
