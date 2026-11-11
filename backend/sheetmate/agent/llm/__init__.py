"""Model access: providers, structured output, budgets, caching and prompts."""
from importlib import import_module

__all__ = ['LLMClient', 'LLMError', 'LLMTimeout', 'LLMNotConfigured', 'aux_json', 'get_llm', 'set_llm_factory', 'parse_json', 'translate_error', 'test_connection', 'contains_llm_not_configured_text', 'is_llm_not_configured_message', 'llm_stage']


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("sheetmate.agent.llm.client"), name)
    globals()[name] = value
    return value
