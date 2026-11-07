"""Pure validation for model-generated Recipe JSON, before scripts are saved."""

from __future__ import annotations

import re
from typing import Any

from sheetmate.agent.structured import StructuredOutputError, validate_payload


_TEXT = {"type": "string"}
_NONEMPTY_TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
_TEXT_LIST = {"type": "array", "items": _NONEMPTY_TEXT}

_ASSERTION = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": [
            "not_empty", "row_count_range", "row_count_ratio", "match_rate_min",
            "no_duplicate_keys", "no_nulls", "columns_exist", "no_dropped_values",
        ]},
        "params": {"type": "object"},
        "severity": {"type": "string", "enum": ["error", "warning"]},
        "description": _TEXT,
    },
    "required": ["type"],
}

_RULE = {
    "type": "object",
    "properties": {
        "id": _TEXT,
        "type": {"type": "string", "enum": [
            "row_count_ratio", "sum_preservation", "no_nulls", "unique_check",
            "column_exists", "type_check", "historical_range",
        ]},
        "description": _TEXT,
        "params": {"type": "object"},
        "severity": {"type": "string", "enum": ["error", "warning", "info"]},
        "requires_history": {"type": "boolean"},
    },
    "required": ["type"],
}

_STEP = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": _TEXT,
        "kind": {"type": "string", "enum": ["formula", "sql", "python"]},
        "action": _TEXT,
        "formula": _TEXT,
        "params": {
            "type": "object",
            "properties": {
                "output_tables": {"type": ["object", "null"], "additionalProperties": _NONEMPTY_TEXT},
                "file_tables": {"type": "object", "additionalProperties": {"type": "object"}},
            },
        },
        "output": _NONEMPTY_TEXT,
        "description": _TEXT,
        "explanation": _TEXT,
        "confidence": {"type": "number"},
        "assertions": {"type": "array", "items": _ASSERTION},
    },
    "anyOf": [
        {"properties": {"formula": _NONEMPTY_TEXT}, "required": ["formula"]},
        {"properties": {"action": _NONEMPTY_TEXT}, "required": ["action"]},
        {"properties": {"kind": {"enum": ["sql", "python"]}}, "required": ["kind"]},
    ],
}

_OUTPUT = {
    "type": "object",
    "properties": {
        "name": _TEXT,
        "source": _NONEMPTY_TEXT,
        "description": _TEXT,
        "columns": {"type": ["array", "null"], "items": _TEXT},
        "split_by": {"type": ["string", "null"]},
    },
    "required": ["source"],
}

RECIPE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "recipe_name": _TEXT,
        "recipe_description": _TEXT,
        "steps": {"type": "array", "minItems": 1, "items": _STEP},
        "output_sheets": {"type": "array", "items": _OUTPUT},
        "output_spec": {"type": "array", "items": _OUTPUT},
        "validation_rules": {"type": "array", "items": _RULE},
        "key_columns": {"type": "object", "additionalProperties": _TEXT_LIST},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {"question": _NONEMPTY_TEXT, "options": _TEXT_LIST},
            "required": ["question"],
        }},
        "alignment_report": {
            "type": "object",
            "properties": {"confidence_overall": {"type": "number"}},
        },
        "confidence_overall": {"type": "number"},
        "notes": _TEXT,
    },
    "required": ["steps"],
}


def validate_recipe_response(value: Any) -> None:
    """Reject invalid declarations without constructing Recipe/RecipeStep models.

    RecipeStep materializes Python code, so the repair callback only examines raw
    JSON. Optional omissions still use the learner's existing defaults.
    """
    validate_payload(value, RECIPE_RESPONSE_SCHEMA)
    used_outputs: set[str] = set()
    step_outputs: dict[int, str] = {}
    known_tables: set[str] = set()
    for index, raw in enumerate(value["steps"], start=1):
        output = str(raw.get("output") or f"步骤{index}结果").strip()
        while output in used_outputs:
            output = f"{output}_{index}"
        used_outputs.add(output)
        # Match RecipeStep's output normalization, without its script validator.
        output = output[:60]
        step_outputs[index] = output
        formula = str(raw.get("formula") or raw.get("action") or "").strip()
        if raw.get("kind") == "python" or (raw.get("kind") != "sql" and formula == "python_script"):
            params = raw.get("params", {})
            mapping = params.get("output_tables")
            known_tables.update([output] if mapping is None else mapping.values())
            known_tables.update(params.get("file_tables", {}))
        else:
            known_tables.add(output)

    errors: list[dict[str, str]] = []
    # Validate every explicit declaration, including an alternate legacy field.
    # An empty or omitted declaration may still select the default final table.
    for field in ("output_sheets", "output_spec"):
        for index, raw in enumerate(value.get(field, [])):
            source = raw["source"]
            match = re.fullmatch(r"step_(\d+)(?:_output)?", source)
            if match:
                source = step_outputs.get(int(match.group(1)), source)
            if source not in known_tables:
                errors.append({"path": f"/{field}/{index}/source", "message": "必须引用步骤实际声明的输出表"})
                if len(errors) >= 6:
                    raise StructuredOutputError(errors)
    if errors:
        raise StructuredOutputError(errors)
