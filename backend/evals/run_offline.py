"""Reproducible synthetic acceptance and context-size measurements. No API calls.

Run: backend/.venv/bin/python backend/evals/run_offline.py --output docs/offline-eval.json
Token estimates use the local Chinese/ASCII heuristic, not a model tokenizer.
Synthetic acceptance checks do not measure live model quality or actual cost.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def synthetic_verified_repair() -> dict:
    """Explicit synthetic evidence; never represent an unverified insight as useful recall."""
    return {"kind": "verified_repair", "verified": True, "failure_kind": "type_mismatch",
            "execution_id": "synthetic_execution", "failed_evidence_id": "ev_synthetic_failed",
            "repaired_evidence_id": "ev_synthetic_repaired", "verification": "completion_check"}


def table_observation_eval(ctx) -> tuple[dict[str, bool], dict]:
    """Exercise complete small results and stable continuations without a model."""
    import pandas as pd

    from sheetmate.agent.tools.analysis.builtin import _tool_preview, _tool_run_sql
    from sheetmate.agent.tools.presentation import OBSERVATION_MAX_CHARS

    checks: dict[str, bool] = {}
    measurements: dict = {"max_chars": OBSERVATION_MAX_CHARS, "small_results": {}}
    sizes = []
    for count in (21, 25):
        source = f"observation_source_{count}"
        result_name = f"observation_result_{count}"
        values = [[index, f"第{index}条原文：" + "保留完整单元格" * 10] for index in range(count)]
        ctx.ws.put_dataframe(source, pd.DataFrame(values, columns=["编号", "说明"]))
        output = _tool_run_sql({"sql": f'SELECT * FROM "{source}"', "result_name": result_name}, ctx)
        observation = json.loads(output.text)
        sizes.append(len(output.text))
        checks[f"tool_observation_{count}_rows_complete"] = (
            observation["complete"] is True
            and observation["returned_rows"] == observation["total_rows"] == count
            and observation["rows"] == values
            and observation["next_call"] is None)
        measurements["small_results"][str(count)] = {
            "returned_rows": observation["returned_rows"], "characters": len(output.text),
            "complete": observation["complete"]}

    values = [[index, f"条目{index}"] for index in range(237)]
    table = "observation_paged"
    ctx.ws.put_dataframe(table, pd.DataFrame(values, columns=["编号", "说明"]))
    args = {"table": table, "offset": 0, "limit": 17}
    revision = ctx.ws.require(table).revision
    collected, pages, pinned = [], 0, True
    first_continuation = None
    while args and pages < 32:
        output = _tool_preview(args, ctx)
        observation = json.loads(output.text)
        sizes.append(len(output.text))
        pages += 1
        collected.extend(observation["rows"])
        next_call = observation["next_call"]
        if next_call:
            pinned = pinned and next_call["args"]["expected_revision"] == revision
            first_continuation = first_continuation or next_call["args"]
        args = next_call["args"] if next_call else None
    checks["tool_observation_pagination_exact"] = args is None and collected == values
    checks["tool_observation_pagination_revision_pinned"] = pinned and first_continuation is not None
    checks["tool_observation_char_budget"] = all(size <= OBSERVATION_MAX_CHARS for size in sizes)
    ctx.ws.put_dataframe(table, pd.DataFrame({"编号": ["replacement"]}))
    try:
        _tool_preview(first_continuation, ctx)
        checks["tool_observation_revision_drift_rejected"] = False
    except ValueError as error:
        checks["tool_observation_revision_drift_rejected"] = (
            "表版本已变化" in str(error) and '"offset":0' in str(error))
    measurements["pagination"] = {"total_rows": len(values), "reconstructed_rows": len(collected),
                                  "pages": pages, "page_limit": 17, "largest_observation_chars": max(sizes)}
    return checks, measurements


def context_memory_eval(ctx, registry) -> tuple[dict[str, bool], dict]:
    """Measure source-to-prompt projections without models or external I/O."""
    from copy import deepcopy

    from sheetmate.agent.memory.short_term.context import (
        ContextBlock,
        HISTORY_TOKEN_BUDGETS,
        assemble_context,
        stage_history,
    )
    from sheetmate.agent.memory.short_term.errors import ContextOverflow
    from sheetmate.agent.memory.long_term.formatting import format_memories
    from sheetmate.agent.memory.long_term.retrieval.selector import retrieve_memories
    from sheetmate.core.security.privacy import contains_sensitive_memory, redact_derived_text
    from sheetmate.agent.memory.short_term.tokens import estimate_tokens, history_tokens
    from sheetmate.core.observability.conversation_log import _normalise

    def text_size(text: str, *, preview_chars: int = 1_400) -> dict:
        original = text
        truncated = len(text) > preview_chars
        if truncated:
            head = preview_chars // 2
            text = text[:head] + "\n[评测报告省略中间展示；尺寸按完整投影计算]\n" + text[-(preview_chars - head):]
        return {"characters": len(original), "tokens_estimate": estimate_tokens(original),
                "text": text, "text_preview_truncated": truncated}

    def history_size(history: list[dict]) -> dict:
        text = "\n\n".join(
            f"[{item['role']}; source_message_id={item.get('message_id', item.get('id', 'projection_notice'))}]\n"
            + str(item.get("content") or "") for item in history)
        preview = text_size(text)
        return {"characters": sum(len(str(item.get("content") or "")) for item in history),
                "rendered_characters": len(text), "tokens_estimate": history_tokens(history), "message_count": len(history),
                "source_message_ids": [item.get("message_id", item.get("id")) for item in history],
                "text": preview["text"], "text_preview_truncated": preview["text_preview_truncated"]}

    def memory(memory_id: int, value: str, *, key: str = "金额", category: str = "domain_knowledge", **extra) -> dict:
        return {"id": memory_id, "version": 1, "category": category, "key": key,
                "value": value, "source": "ai", "scope_label": "流程", "customer_id": "synthetic_customer",
                "recipe_id": "synthetic_recipe", "source_message_id": 101, **extra}

    checks: dict[str, bool] = {}
    memories = [memory(i, f"回答使用中文，近期偏好编号{i}", key=f"偏好{i}", category="preferences")
                for i in range(1, 21)]
    relevant = memory(30, "运单号匹配前去掉两端空格", key="运单号", category="history_insights",
                      execution_evidence=synthetic_verified_repair())
    unverified = memory(33, "运单号匹配前去掉两端空格，尚未核验", key="运单号", category="history_insights")
    required = memory(31, "金额不得四舍五入", source="human")
    unrelated = memory(32, "发票每月五号开具", key="开票")
    memories.extend([unrelated, relevant, required, unverified])
    memory_source = deepcopy(memories)
    recall = retrieve_memories(memories, "核对运单号", limit=2, max_chars=1600)
    selected_ids = {item["id"] for item in recall["items"]}
    checks["context_memory_relevant_recall"] = 30 in selected_ids and 32 not in selected_ids
    checks["context_memory_human_rule_retained"] = 31 in selected_ids
    unverified_recall = retrieve_memories([unverified], "核对运单号", limit=10)
    checks["context_memory_unverified_insight_excluded"] = not unverified_recall["items"]
    checks["context_memory_sources_and_method_explicit"] = (
        "source_message_id=101" in format_memories(recall["items"])
        and recall["metadata"]["embedding"] is False
        and recall["metadata"]["method"] == "lexical_mmr_recent")
    checks["context_memory_source_not_mutated"] = memories == memory_source

    duplicates = [memory(41, "金额保留两位小数"), memory(42, "金额保留两位小数", key="另一个键"),
                  memory(43, "金额保留两位小数", recipe_id="another_recipe"),
                  memory(44, "金额不能保留两位小数")]
    dedup = retrieve_memories(duplicates, "金额", limit=10)
    checks["context_memory_exact_duplicate_only_within_scope"] = (
        {item["id"] for item in dedup["items"]} == {41, 43, 44}
        and dedup["metadata"]["duplicate_count"] == 1)
    diverse = [memory(51, "金额必须保留两位小数并四舍五入，发票另行整理。"),
               memory(52, "金额必须保留两位小数并四舍五入，发票单独整理。"),
               memory(53, "金额不包含运费")]
    diversity = retrieve_memories(diverse, "金额", limit=2)
    checks["context_memory_diverse_relevant_facts"] = {item["id"] for item in diversity["items"]} == {51, 53}

    history = [{"role": "user", "message_id": 1,
                "content": "FIRST_GOAL：核对运单金额，保留两位小数。"}]
    history.extend({"role": "user" if index % 2 == 0 else "assistant", "message_id": index,
                    "content": "历史背景说明，金额口径需依据原文。" * 60} for index in range(2, 36))
    history.extend([
        {"role": "user", "message_id": 36, "content": "LATEST_REQUIREMENT：只统计签收订单。"},
        {"role": "assistant", "message_id": 37, "content": "旧助手背景说明。" * 1200},
    ])
    history_source = deepcopy(history)
    stages = {}
    for stage in ("execution", "planning", "specialist"):
        selected = stage_history(history, stage)
        stages[stage] = {"budget_tokens_estimate": HISTORY_TOKEN_BUDGETS[stage], **history_size(selected)}
        content = "\n".join(str(item.get("content") or "") for item in selected)
        checks[f"context_history_{stage}_within_budget"] = history_tokens(selected) <= HISTORY_TOKEN_BUDGETS[stage]
        checks[f"context_history_{stage}_first_and_latest_user"] = (
            "FIRST_GOAL" in content and "LATEST_REQUIREMENT" in content)
        retrieval_instruction = ("协调器" in content if stage == "specialist"
                                 else "inspect_conversation_history" in content)
        checks[f"context_history_{stage}_omission_retrieval"] = retrieval_instruction
    checks["context_history_planning_and_specialist_smaller"] = (
        stages["specialist"]["tokens_estimate"] < stages["planning"]["tokens_estimate"]
        < stages["execution"]["tokens_estimate"] < history_tokens(history))
    tiny_budgets = {}
    for budget in (1, 64, 128, 600):
        try:
            selected = stage_history(history, "planning", token_budget=budget)
        except ContextOverflow as error:
            tiny_budgets[str(budget)] = {"status": "explicit_overflow", "error": str(error)}
            checks[f"context_history_small_budget_{budget}"] = True
        else:
            tiny_budgets[str(budget)] = {"status": "projected", **history_size(selected)}
            content = "\n".join(str(item.get("content") or "") for item in selected)
            checks[f"context_history_small_budget_{budget}"] = (
                history_tokens(selected) <= budget
                and (budget < 600 or ("FIRST_GOAL" in content and "LATEST_REQUIREMENT" in content)))
    checks["context_history_source_not_mutated"] = history == history_source

    constraint = "人工规则：金额不得四舍五入；只统计已签收订单；source_message_id=101"
    reference = "\n".join(f"参考规则{index}：金额保留两位小数。" for index in range(100))
    blocks = [ContextBlock("saved_memories", reference, references=("memory_id=7",),
                           retrieve="调用 inspect_saved_memories 按 memory_id 或关键词回查。"),
              ContextBlock("human_constraints", constraint, required=True)]
    before_blocks = "\n\n".join(f"[source={block.source}]\n{block.text}" for block in blocks)
    context_after = assemble_context(blocks, token_budget=600)
    checks["context_assembly_required_constraints_intact"] = constraint in context_after
    checks["context_assembly_budget_and_omission_receipt"] = (
        estimate_tokens(context_after) <= 600 and "未完整" in context_after
        and "saved_memories" in context_after and "回查" in context_after)
    checks["context_assembly_optional_reference_retrieval"] = "inspect_saved_memories" in context_after
    checks["context_assembly_no_partial_numeric_lines"] = all(
        line in reference.splitlines() for line in context_after.splitlines() if line.startswith("参考规则"))
    try:
        assemble_context([ContextBlock("required", constraint * 20, required=True)], token_budget=40)
        checks["context_assembly_required_overflow_explicit"] = False
    except ContextOverflow:
        checks["context_assembly_required_overflow_explicit"] = True
    checks["context_assembly_source_not_mutated"] = blocks[0].text == reference and blocks[1].text == constraint

    # Every value here is deliberately synthetic, including the credential.
    sensitive = ("联系 finance@example.com；联系电话：13800138000；"
                 "api_key=syntheticCredential123456；data:image/png;base64,YWJjZGVmZw==")
    business = ("订单号：13800138000；金额：13800138000；数量：13800138000；"
                "source_message_id=101；file_id=file_13800138000；evidence_id=ev_test123")
    private_source = {"message_id": 101, "evidence_id": "ev_test123", "content": sensitive,
                      "business": business}
    private_original = deepcopy(private_source)
    private_after = _normalise(private_source)
    redacted = redact_derived_text(sensitive)
    checks["context_privacy_derived_contacts_credentials_images_redacted"] = all(
        value not in redacted for value in ("finance@example.com", "13800138000",
                                           "syntheticCredential123456", "YWJjZGVmZw=="))
    checks["context_privacy_business_numbers_source_ids_unchanged"] = (
        redact_derived_text(business) == business and private_after["business"] == business
        and private_after["message_id"] == 101 and private_after["evidence_id"] == "ev_test123")
    checks["context_privacy_sensitive_auto_memory_rejected"] = (
        contains_sensitive_memory(sensitive) and not contains_sensitive_memory(business))
    checks["context_privacy_source_not_mutated"] = private_source == private_original

    retrieval_tools = {"inspect_conversation_history", "inspect_saved_memories"}
    coordinator_names = {tool.name for tool in registry.core_for_context(ctx)}
    specialist = SimpleNamespace(mode="analyze", customer=None, tool_group="core",
                                 enabled_tool_groups=set(), table_filter={"orders"})
    specialist_names = {tool.name for tool in registry.for_context(specialist)}
    checks["context_retrieval_tools_coordinator_visible"] = retrieval_tools <= coordinator_names
    checks["context_retrieval_tools_specialist_hidden"] = not (retrieval_tools & specialist_names)
    checks["context_retrieval_tools_specialist_cannot_load_by_name"] = all(
        registry.load_named_tool(name, specialist) is None for name in retrieval_tools)

    projections = {
        "memory_recall": {"query": "核对运单号", "before": text_size(format_memories(memories)),
                          "after": text_size(format_memories(recall["items"])), "metadata": recall["metadata"]},
        "deduplication": {"before": text_size(format_memories(duplicates)),
                          "after": text_size(format_memories(dedup["items"])), "metadata": dedup["metadata"],
                          "fixture_note": "Mixed scopes test duplicate identity only; real services must filter access before ranking."},
        "diversity": {"before": text_size(format_memories(diverse)),
                      "after": text_size(format_memories(diversity["items"])), "metadata": diversity["metadata"]},
        "history": {"before": history_size(history), "after_by_stage": stages, "small_budgets": tiny_budgets},
        "assembly": {"budget_tokens_estimate": 600, "before": text_size(before_blocks),
                     "after": text_size(context_after)},
        "privacy": {"fixture_note": "All contacts and credentials are synthetic public test values.",
                    "before": text_size(json.dumps(private_source, ensure_ascii=False)),
                    "after": text_size(json.dumps(private_after, ensure_ascii=False))},
        "tool_visibility": {"coordinator": sorted(coordinator_names), "specialist": sorted(specialist_names)},
    }
    return checks, projections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with TemporaryDirectory(prefix="sheetmate-eval-") as directory:
        os.environ["SHEETMATE_DATA_DIR"] = directory
        import pandas as pd
        from sheetmate.agent.memory.long_term.policy import MemoryContextLimit
        from sheetmate.agent.memory.long_term.retrieval.selector import select_relevant
        from sheetmate.agent.tools.contracts import AgentContext
        from sheetmate.agent.tools.registry import ToolRegistry
        from sheetmate.agent.tools.analysis.builtin import _tool_value_counts
        from sheetmate.engine.formulas import FormulaLibrary
        from sheetmate.engine.tables.memory import format_table_memory
        from sheetmate.engine.tables.workspace import Workspace
        from sheetmate.recipe.definition.schema import Recipe, ValidationRule
        from sheetmate.recipe.validation.rules import validate_run
        from sheetmate.recipe.validation.comparison import values_equal, verify_outputs

        checks = {}
        for name, left, right in (("identifier", "10000001", "10000002"),
                                  ("leading_zero", "001234", "1234"),
                                  ("large_amount", 1000000, 1000900)):
            checks[name] = not values_equal(left, right)
        expected = pd.DataFrame({"单号": [f"X{i}" for i in range(100)], "金额": [0] * 100})
        checks["extra_duplicate"] = verify_outputs({"结果": pd.concat([expected, expected.iloc[[0]]])},
                                                    {"结果": expected})["status"] == "fail"
        wide = pd.DataFrame({"单号": [f"X{i}" for i in range(100)], "客户": ["A"] * 100,
                             **{f"字段{i}": [0] * 100 for i in range(98)}})
        wrong = wide.copy()
        wrong["客户"] = "B"
        checks["wrong_critical_column"] = verify_outputs({"结果": wrong}, {"结果": wide})["status"] == "fail"
        checks["empty_expected"] = verify_outputs({"结果": pd.DataFrame({"a": [1]})},
                                                   {"结果": pd.DataFrame({"a": []})})["status"] == "fail"
        for kind, params in (("no_nulls", {"columns": ["Missing"]}), ("unique_check", {"columns": ["Missing"]}),
                             ("type_check", {"columns": {"Missing": "numeric"}})):
            recipe = Recipe(name="评测", validation_rules=[ValidationRule(type=kind, severity="error", params=params)])
            checks[f"missing_{kind}"] = validate_run(recipe, {}, {"结果": expected})["status"] == "fail"
        recipe = Recipe(name="评测", validation_rules=[ValidationRule(type="historical_range", severity="error",
                                                                       params={"column": "金额"})])
        checks["zero_variance_outlier"] = validate_run(recipe, {}, {"结果": pd.DataFrame({"金额": [1000000]})},
                                                       [{"结果.金额.sum": 100}] * 3)["status"] == "fail"
        insight = dict(category="history_insights", key="运单号", value="核对空格", source="ai",
                       execution_evidence=synthetic_verified_repair())
        standing = [dict(category="domain_knowledge", key=f"规则{i}", value="一般背景", source="ai") for i in range(30)]
        checks["relevant_memory"] = insight in select_relevant([*standing, insight], "检查运单号")
        try:
            select_relevant([dict(category="domain_knowledge", key=f"规则{i}", value="甲" * 500, source="human") for i in range(100)], "金额")
            checks["human_rule_overflow_explicit"] = False
        except MemoryContextLimit:
            checks["human_rule_overflow_explicit"] = True

        ws = Workspace()
        try:
            ws.put_dataframe("orders", pd.DataFrame({"订单号": [f"X{i:05}" for i in range(5000)]}))
            ctx = AgentContext("analyze", ws, FormulaLibrary(), SimpleNamespace(), None, "offline-eval")
            output = _tool_value_counts({"table": "orders", "column": "订单号", "limit": 5000}, ctx)
            info = ws.require("orders")
            memory = format_table_memory(info)
            registry = ToolRegistry()
            full = registry.for_context(ctx)
            core = registry.core_for_context(ctx)
            measurements = {
                "distribution_rows": 5000,
                "tool_result_chars": len(output.text), "next_table_context_chars": len(memory),
                "stored_distribution_rows": len(info.probe_memory["observations"][0]["result"]["rows"]),
                "full_tool_count": len(full), "core_tool_count": len(core),
                "full_tool_schema_chars": len(json.dumps([tool.definition() for tool in full], ensure_ascii=False)),
                "core_tool_schema_chars": len(json.dumps([tool.definition() for tool in core], ensure_ascii=False)),
            }
            checks["bounded_fact_projection"] = len(memory) <= 8000 and measurements["stored_distribution_rows"] == 5000
            context_checks, projections = context_memory_eval(ctx, registry)
            checks.update(context_checks)
            observation_checks, observation_measurements = table_observation_eval(ctx)
            checks.update(observation_checks)
            measurements["table_observations"] = observation_measurements
        finally:
            ws.close()
    result = {"kind": "synthetic_offline_acceptance", "api_calls": 0, "checks": checks,
              "passed": sum(checks.values()), "total": len(checks), "measurements": measurements,
              "context_memory_projections": projections,
              "failed_checks": [name for name, passed in checks.items() if not passed],
              "token_estimator": "Local heuristic: one non-ASCII character per token, ceil(ASCII characters / 4); histories add 8 per message. Not a provider tokenizer.",
              "limitations": "Synthetic deterministic acceptance only. No real model recall/answer quality, actual token usage, billing savings or latency claims. Projection size reductions are not measured model cost reductions. Privacy recognizers are a baseline, not complete PII detection."}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
