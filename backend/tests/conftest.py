"""测试公共设置：独立的数据目录 + 可编排的假 LLM（不需要真实的 API Key）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# 必须在导入 sheetmate 之前设置：配置在导入时读取
_DATA = Path(tempfile.mkdtemp(prefix="sheetmate_test_"))
os.environ["SHEETMATE_DATA_DIR"] = str(_DATA)
os.environ["SEED_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from sheetmate.demo.samples import write_samples  # noqa: E402


# ---------------------------------------------------------------------------
# 假 LLM：按剧本返回流式文本 / 工具调用，complete_json 按提示词类型路由
# ---------------------------------------------------------------------------


def _chunk(content: str | None = None, tool_calls: list | None = None, finish: str | None = None) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _tool_delta(index: int, call_id: str | None, name: str | None, arguments: str | None) -> Any:
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=fn)


class FakeLLM:
    """script：stream_chat 的回合列表，每个元素是 {"text": "..."} 或 {"tools": [(name, args), ...]}。
    json_handlers：complete_json 时依次匹配 (关键词, 返回值或函数)。
    默认自动计划只有一步，自检重复最后一条执行结论，不消耗下一次请求的剧本。
    专门测试自检工具调用时设 script_reflection=True；all_calls 始终记录全部模型调用。
    """

    def __init__(self, script: list[dict[str, Any]] | None = None, json_handlers: list[tuple[str, Any]] | None = None,
                 intent_mode: str = "analyze", save_recipe_requested: bool = False, delete_recipe_ids: list[str] | None = None,
                 script_reflection: bool = False):
        self.script = list(script or [])
        self.json_handlers = list(json_handlers or [])
        self.calls: list[list[dict[str, Any]]] = []
        self.all_calls: list[list[dict[str, Any]]] = []
        self.reflection_calls: list[list[dict[str, Any]]] = []
        self.json_prompts: list[str] = []
        self.json_image_parts: list[list[dict[str, Any]]] = []
        self.intent_mode = intent_mode
        self.save_recipe_requested = save_recipe_requested
        self.delete_recipe_ids = list(delete_recipe_ids or [])
        self.script_reflection = script_reflection
        self._last_text: str | None = None

    async def stream_chat(self, messages, tools=None, tool_choice=None):
        self.all_calls.append(messages)
        reflection = bool(messages and "快速自检" in str(messages[-1].get("content", "")))
        if reflection:
            self.reflection_calls.append(messages)
        if reflection and not self.script_reflection:
            turn = {"text": self._last_text if self._last_text is not None else "## ✅ 最终结论\n- 完成"}
        else:
            self.calls.append(messages)
            turn = self.script.pop(0) if self.script else {"text": "## ✅ 最终结论\n- 完成"}
        if "tools" in turn and tool_choice == "none":
            turn = {"text": "（最后一轮，直接回答）"}
        if "tools" in turn:
            lead = turn.get("text", "")  # 工具调用之前模型顺带说的话（「我先看一下表结构」）
            for i in range(0, len(lead), 12):
                yield _chunk(content=lead[i : i + 12])
            for i, (name, args) in enumerate(turn["tools"]):
                raw = json.dumps(args, ensure_ascii=False)
                # 模拟分片：名字一片，参数拆成两片
                yield _chunk(tool_calls=[_tool_delta(i, f"call_{len(self.calls)}_{i}", name, None)])
                yield _chunk(tool_calls=[_tool_delta(i, None, None, raw[: len(raw) // 2])])
                yield _chunk(tool_calls=[_tool_delta(i, None, None, raw[len(raw) // 2 :])])
            yield _chunk(finish="tool_calls")
        else:
            text = turn.get("text", "")
            self._last_text = text
            for i in range(0, len(text), 12):
                yield _chunk(content=text[i : i + 12])
            yield _chunk(finish="stop")

    async def complete_json(self, prompt: str, system: str | None = None, retries: int = 1, *, image_parts=None):
        blob = f"{system or ''}\n{prompt}"
        self.json_prompts.append(blob)
        self.json_image_parts.append(list(image_parts or []))
        for i, (keyword, value) in enumerate(self.json_handlers):
            if keyword in blob:
                if isinstance(value, list):  # 列表：依次弹出
                    result = value.pop(0) if len(value) > 1 else value[0]
                else:
                    result = value
                return result(blob) if callable(result) else result
        if "重复流程保存授权判断助手" in blob:
            return {"save_recipe": self.save_recipe_requested}
        if "重复流程删除授权判断助手" in blob:
            return {"delete_recipe_ids": self.delete_recipe_ids}
        if "处理意图判断助手" in blob:
            return {"mode": self.intent_mode}
        if "规划助手" in blob:
            return {"title": "自动执行计划", "steps": [{"id": 1, "description": "执行本次需求"}]}
        if "真正的歧义" in blob:
            return {"needs_clarification": False}
        if "长期记住" in blob:
            return {"items": []}
        return {}

    async def chat(self, messages, tools=None, **kwargs):
        return SimpleNamespace(content="测试对话", tool_calls=None)


@pytest.fixture(scope="session")
def samples() -> dict[str, Path]:
    return write_samples(Path(tempfile.mkdtemp(prefix="sheetmate_samples_")))


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from sheetmate.app import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"account": "admin", "password": "admin123"})
        assert r.status_code == 200
        yield c


@pytest.fixture
def fake_llm():
    """安装假 LLM，测试结束后还原。用法：fake_llm(script=[...], json_handlers=[...])"""
    from sheetmate.agent import llm as llm_module

    holder: dict[str, FakeLLM] = {}

    def install(**kwargs) -> FakeLLM:
        holder["llm"] = FakeLLM(**kwargs)
        llm_module.set_llm_factory(lambda: holder["llm"])
        return holder["llm"]

    yield install
    llm_module.set_llm_factory(None)


def read_sse(response) -> list[dict[str, Any]]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return events
