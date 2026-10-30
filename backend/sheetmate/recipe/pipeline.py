"""Recipe 闭环：学习 → 结构检查 → 执行（出错自动修复）→ 校验 → 与人工结果比对（不通过就修正再跑）→ 前后对比 → 保存。

主流程按 SheetAgent：diff 作证据、LLM 生成 Recipe、用训练输入重跑并验证；
执行层按 SheetMate：每一步是公式 + 参数，由 DuckDB 执行，断言 + 内层修复 + 超限上报。

数据库、文件这些和存储有关的事情交给 PipelineHost（服务层实现），这里只管流程。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from sheetmate.agent.repair import LLMStepRepairer
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.differ import diff_tables, diff_view
from sheetmate.recipe.executor import InputSource, RecipeRunner
from sheetmate.recipe.learner import LearnContext, RecipeLearner
from sheetmate.recipe.schema import Recipe, check_recipe
from sheetmate.recipe.validator import validate_run
from sheetmate.recipe.verifier import verify_outputs


@dataclass
class _Attempt:
    """生成-执行-比对的一轮。"""

    runner: RecipeRunner
    handle: RunHandle
    verification: dict[str, Any] | None = None
    informational: bool = False  # 修订模式：比对只作参考，不参与选优

    def score(self) -> tuple[int, int, float]:
        """执行成功 > 失败；再比比对状态和匹配率。"""
        executed = 0 if self.runner.report.get("failed_stage") in {"input", "step", "error"} else 1
        valid = 0 if self.runner.report.get("failed_stage") == "validation" else 1
        if self.informational:
            return (executed, valid, 0.0)
        rank = {"pass": 2, "warn": 1, "fail": 0}.get((self.verification or {}).get("status", "pass"), 0)
        return (executed, rank * 2 + valid, (self.verification or {}).get("overall_match_rate", 1.0))


@dataclass
class RunHandle:
    """一次运行的资源：运行 id、独立的 DuckDB 工作区、输出文件路径。"""

    run_id: str
    ws: Workspace
    output_path: Path


class PipelineHost(Protocol):
    def open_run(self, kind: str, recipe: Recipe, request_text: str = "") -> RunHandle: ...

    def finish_run(self, handle: RunHandle, recipe: Recipe, report: dict[str, Any], *,
                   verification: dict[str, Any] | None = None, diff: dict[str, Any] | None = None,
                   register_output: bool = True) -> dict[str, Any] | None:
        """记录运行结果；有输出文件且 register_output 时登记文件并返回 {file_id, filename, ...}。"""

    def save_recipe(self, recipe: Recipe, *, verification: dict[str, Any] | None, run_report: dict[str, Any]) -> dict[str, Any]: ...

    def history_metrics(self, recipe_id: str) -> list[dict[str, Any]]: ...


class RecipePipeline:
    def __init__(self, llm: Any, library: FormulaLibrary, host: PipelineHost, *, max_rounds: int = 2, max_repair: int = 3):
        self.llm = llm
        self.library = library
        self.host = host
        self.max_rounds = max_rounds
        self.max_repair = max_repair
        self.learner = RecipeLearner(llm)
        self.repairer = LLMStepRepairer(llm)
        self.result: dict[str, Any] = {}

    async def build(self, ctx: LearnContext, *, base: Recipe | None = None, instruction: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """生成（或按 instruction 修订 base）Recipe，并跑完整闭环。最终结果在 self.result。"""
        mode = "revise" if base is not None else "learn"
        revising = base is not None and bool(instruction)
        yield {"type": "recipe_learn_start", "mode": mode, "inputs": list(ctx.input_frames), "expected": list(ctx.expected)}
        if ctx.expected and ctx.diff is None:
            ctx.diff = diff_tables(ctx.input_frames, ctx.expected)
        if ctx.diff:
            yield {"type": "recipe_evidence", "diff": diff_view(ctx.diff)}

        # ---- 1. 生成 / 修订（外层：先规划完整步骤）----
        yield {"type": "recipe_thinking", "message": "正在根据需求和差异证据规划处理步骤…" if mode == "learn" else "正在按要求修订流程…"}
        if base is not None and instruction:
            recipe = await self.learner.revise(ctx, base, instruction)
        else:
            recipe = await self.learner.generate(ctx)
        recipe = await self._ensure_structure(ctx, recipe)
        if recipe is None:
            yield {"type": "recipe_failed", "error": self.result["error"]}
            return
        yield {"type": "recipe_generated", "recipe": recipe.to_dict(), "flow": recipe.flow(),
               "questions": recipe.metadata.get("questions", [])}

        # ---- 2. 执行 + 比对（不通过就修正，最多 max_rounds 轮；保留效果最好的一轮）----
        attempts: list[_Attempt] = []
        for round_no in range(1, self.max_rounds + 2):
            handle = self.host.open_run("modify", recipe, ctx.requirement)
            runner = RecipeRunner(recipe, self.library, handle.ws, field_mapping=ctx.field_mapping,
                                  repairer=self.repairer, max_repair=self.max_repair, run_id=handle.run_id)
            async for event in runner.run(self._sources(ctx), output_path=handle.output_path,
                                          history=self.host.history_metrics(recipe.id)):
                yield {**event, "round": round_no}
            recipe, report = runner.recipe, runner.report
            attempt = _Attempt(runner, handle, informational=revising)
            attempts.append(attempt)
            stage = report.get("failed_stage")
            if stage in {"input", "step", "error"}:
                break  # 执行层面的失败（已经过内层修复），交给用户处理
            if revising and instruction and not stage:
                added = await asyncio.to_thread(_keep_requested_columns, recipe, runner, handle, instruction)
                if added:
                    yield {"type": "recipe_thinking", "message": f"按你的要求，输出里保留了新列：{'、'.join(added)}"}
            if ctx.expected and revising:
                # 修订是用户有意改了规则：和原人工结果的对比只作参考（看看改动之外有没有误伤），不据此自动修正
                attempt.verification = verify_outputs(runner.collect_outputs(), ctx.expected)
                attempt.verification["informational"] = True
                yield {"type": "recipe_verify", "report": attempt.verification, "round": round_no, "informational": True}
                if stage != "validation":
                    break
                feedback = _validation_feedback(report["validation"])
            elif ctx.expected:
                attempt.verification = verify_outputs(runner.collect_outputs(), ctx.expected)
                yield {"type": "recipe_verify", "report": attempt.verification, "round": round_no}
                rate = attempt.verification["overall_match_rate"]
                if stage == "validation" and rate >= 0.999:
                    _relax_rules(recipe, report)  # 和人工结果完全一致，说明是 AI 写的校验规则太严
                    stage = ""
                if attempt.verification["status"] == "pass" and not stage:
                    break
                feedback = attempt.verification
            elif stage == "validation":
                feedback = _validation_feedback(report["validation"])
            else:
                break
            if round_no > self.max_rounds:
                break
            yield {"type": "recipe_refine", "round": round_no + 1, "reason": feedback["summary"]}
            refined = await self.learner.refine(ctx, recipe, feedback)
            refined = await self._ensure_structure(ctx, refined)
            if refined is None:
                break
            recipe = refined
            yield {"type": "recipe_generated", "recipe": recipe.to_dict(), "flow": recipe.flow(),
                   "questions": recipe.metadata.get("questions", []), "round": round_no + 1}

        best = max(attempts, key=lambda a: a.score())
        for other in attempts:  # 没被采用的几轮也记入运行记录（不登记输出文件）
            if other is not best:
                self.host.finish_run(other.handle, other.runner.recipe, other.runner.report,
                                     verification=other.verification, register_output=False)
        if best is not attempts[-1]:
            yield {"type": "recipe_thinking", "message": f"采用效果最好的第 {attempts.index(best) + 1} 轮结果"}
        runner, handle, verification = best.runner, best.handle, best.verification
        recipe = runner.recipe
        report = runner.report
        ok = report["status"] not in {"failed", "escalated"}

        # ---- 3. 前后对比 ----
        outputs: dict[str, pd.DataFrame] = runner.collect_outputs() if ok else {}
        before_after = diff_view(diff_tables(ctx.input_frames, outputs)) if outputs else None
        if before_after:
            yield {"type": "recipe_diff", "diff": before_after}

        # ---- 4. 记录运行、登记输出文件、保存 Recipe ----
        file_info = self.host.finish_run(handle, recipe, report, verification=verification, diff=before_after)
        if file_info:
            yield {"type": "file", **file_info}
        recipe.metadata["verification"] = _verification_brief(verification)
        recipe.metadata["last_run"] = {"run_id": report["run_id"], "status": report["status"]}
        # 修订时用户明确要求了改动，和原样例的差异不影响启用；学习时必须复现人工结果才启用
        saved = self.host.save_recipe(recipe, verification=None if revising else verification, run_report=report)
        recipe.version = saved.get("version", recipe.version)
        yield {"type": "recipe_saved", **saved, "flow": recipe.flow()}
        self.result = {
            "ok": ok,
            "recipe": recipe,
            "saved": saved,
            "report": report,
            "verification": verification,
            "outputs": outputs,
            "file": file_info,
            "summary": runner.summary(),
        }

    async def _ensure_structure(self, ctx: LearnContext, recipe: Recipe) -> Recipe | None:
        problems = check_recipe(recipe, self.library)
        if problems:
            recipe = await self.learner.fix_structure(ctx, recipe, problems)
            problems = check_recipe(recipe, self.library)
        if problems:
            self.result = {"ok": False, "recipe": recipe, "error": "生成的流程结构有问题：" + "；".join(problems)}
            return None
        return recipe

    @staticmethod
    def _sources(ctx: LearnContext) -> dict[str, InputSource]:
        sources = {}
        for alias, df in ctx.input_frames.items():
            meta = ctx.input_meta.get(alias)
            sources[alias] = InputSource(alias=alias, df=df, filename=meta.filename if meta else "",
                                         sheet=meta.sheet if meta else None, file_id=meta.file_id if meta else None)
        return sources


def _validation_feedback(validation: dict[str, Any]) -> dict[str, Any]:
    """没有人工结果时，用运行校验的失败项作为修正依据（格式和比对报告一致，方便复用修正提示词）。"""
    bad = [f"{c['description']}：{c.get('message') or c.get('actual')}" for c in validation.get("checks", []) if c["status"] == "fail"]
    return {"status": "fail", "summary": "运行校验未通过", "details": bad, "sheets": [], "overall_match_rate": 0.0}


def _relax_rules(recipe: Recipe, report: dict[str, Any]) -> None:
    """结果和人工结果一致但校验规则不通过：把这些规则降级为警告，并更新运行状态。"""
    failed_ids = {c["rule_id"] for c in (report.get("validation") or {}).get("checks", []) if c["status"] == "fail"}
    for rule in recipe.validation_rules:
        if rule.id in failed_ids:
            rule.severity = "warning"
    report["status"] = "warning"
    report["failed_stage"] = ""


def _keep_requested_columns(recipe: Recipe, runner: RecipeRunner, handle: RunHandle, instruction: str) -> list[str]:
    """修订时的兜底：结果表里有用户在修改要求中点名的新列，但被 output_spec 的列清单过滤掉了 → 补进清单并重写输出文件。"""
    added: list[str] = []
    for spec in recipe.output_spec:
        if not spec.columns or not runner.ws.exists(spec.source):
            continue
        columns = runner.ws.require(spec.source).column_names
        missing = [c for c in columns if c not in spec.columns and c in instruction]
        if missing:
            spec.columns = [*spec.columns, *missing]
            added.extend(missing)
    if added:
        outputs = runner.collect_outputs()
        runner.report["outputs"] = runner.write_outputs(handle.output_path, outputs)
        runner.report["validation"] = validate_run(recipe, runner.input_frames, outputs)
    return added


def _verification_brief(verification: dict[str, Any] | None) -> dict[str, Any] | None:
    if not verification:
        return None
    return {"status": verification["status"], "match_rate": verification["overall_match_rate"], "summary": verification["summary"],
            "informational": bool(verification.get("informational"))}
