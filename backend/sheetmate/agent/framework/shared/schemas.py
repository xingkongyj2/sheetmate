"""模型业务输出的结构约定；展示文案保留现有兼容处理，执行和核对字段严格校验。"""

from __future__ import annotations


_TEXT = {"type": "string"}
_NONEMPTY_TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
_TEXT_LIST = {"type": "array", "items": _NONEMPTY_TEXT}

_FILE_SELECTION = {
    "type": "object",
    "properties": {
        "selection": {"type": "string", "enum": ["selected", "all", "latest", "recent"]},
        "file_ids": {"type": "array", "items": _NONEMPTY_TEXT},
        "kinds": {"type": "array", "items": {"type": "string", "enum": ["input", "expected", "output"]}},
    },
    "required": ["selection"],
}

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["analyze", "modify", "create"]},
        # 旧协议仅返回 mode 时仍表示处理任务，不让合法历史服务配置失效。
        "action": {"type": "string", "enum": ["process", "chat", "show_files"]},
        "files": _FILE_SELECTION,
        "replace_file_ids": {"type": "array", "items": _NONEMPTY_TEXT, "uniqueItems": True, "maxItems": 20},
        "save_recipe": {"type": "boolean"},
        "task_relation": {"type": "string", "enum": ["new", "continue"]},
        "tool_groups": {"type": "array", "items": {"type": "string", "enum": ["workbook", "workflow", "python", "formulas"]},
                        "uniqueItems": True, "maxItems": 4},
        "memory_candidate": {"type": ["boolean", "null"]},
    },
    "required": ["mode"],
    "allOf": [{
        "if": {"properties": {"action": {"const": "show_files"}}, "required": ["action"]},
        "then": {"required": ["files"]},
    }],
}

_PLAN_STEP = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "title": _TEXT,
        "description": {**_NONEMPTY_TEXT, "maxLength": 4000},
        "narration": _TEXT,
        "check": {**_NONEMPTY_TEXT, "maxLength": 3000},
        "depends_on": {"type": "array", "items": {"type": "integer", "minimum": 1},
                       "uniqueItems": True, "maxItems": 7},
    },
    "required": ["description", "check"],
}
_PLAN_STEPS = {
    "type": "array", "minItems": 1, "maxItems": 8,
    "items": _PLAN_STEP,
}
_PLAN_PROPERTIES = {
    "title": _TEXT,
    "understanding": _TEXT,
    "assumptions": _TEXT_LIST,
    "risks": _TEXT_LIST,
    "acceptance": {"type": "array", "minItems": 1, "maxItems": 8,
                   "items": {**_NONEMPTY_TEXT, "maxLength": 1000}},
    "steps": _PLAN_STEPS,
    # show_plan 只控制展示；缺失或类型不合法时，沿用 planner 的隐藏/保留规则。
}

PLAN_SCHEMA = {"type": "object", "properties": _PLAN_PROPERTIES, "required": ["steps", "acceptance"]}

PLAN_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        **_PLAN_PROPERTIES,
        "verdict": {"type": "string", "enum": ["ok", "revise"]},
        "issues": _TEXT_LIST,
    },
    "required": ["verdict", "issues"],
    "allOf": [
        {"if": {"properties": {"verdict": {"const": "revise"}}},
         "then": {"required": ["steps"], "properties": {"issues": {"minItems": 1}}}},
        {"if": {"properties": {"verdict": {"const": "ok"}}},
         "then": {"properties": {"issues": {"maxItems": 0}}}},
    ],
}

STEP_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "issues": _TEXT_LIST,
        "checked": _TEXT,
        "advice": _TEXT,
        "carry_over": _TEXT,
    },
    "required": ["passed", "issues"],
}

ANSWER_AUDIT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "issues": _TEXT_LIST},
    "required": ["ok", "issues"],
}
