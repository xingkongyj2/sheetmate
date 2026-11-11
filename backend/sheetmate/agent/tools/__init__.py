"""Reusable tool contracts, registration and execution.

Feature implementations live in their own packages and are loaded on demand.
"""
from importlib import import_module

_EXPORTS = {'AgentContext': 'sheetmate.agent.tools.contracts', 'Tool': 'sheetmate.agent.tools.contracts', 'ToolHost': 'sheetmate.agent.tools.contracts', 'ToolResult': 'sheetmate.agent.tools.contracts', 'ToolSchemaError': 'sheetmate.agent.tools.contracts', 'ToolRegistry': 'sheetmate.agent.tools.registry', 'ToolCleanupPending': 'sheetmate.agent.tools.execution', 'execute_tool': 'sheetmate.agent.tools.execution', 'validate_tool_args': 'sheetmate.agent.tools.contracts', 'tool_argument_error': 'sheetmate.agent.tools.contracts', 'reserved_tool_names': 'sheetmate.agent.tools.registry', 'table_payload': 'sheetmate.agent.tools.presentation', 'ephemeral_payload': 'sheetmate.agent.tools.presentation', 'public_detail_text': 'sheetmate.agent.tools.presentation', 'public_detail_section': 'sheetmate.agent.tools.presentation'}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
