"""输入文件改名后，同步流程里记录的文件名（纯函数，不读写数据库）。

流程通过 learned_from.input_bindings 把输入别名绑定到具体文件 ID。文件改名后，这些输入的来源文件名、
说明文字和已经对不上新名字的文件规则需要跟着改，否则流程里显示的、用来匹配的名字与真实文件不一致。
"""

from __future__ import annotations

import fnmatch

from sheetmate.recipe.definition.schema import Recipe, file_pattern_from_name
from sheetmate.recipe.editing.references import describe, replace_filename


def _matches(pattern: str, filename: str) -> bool:
    """与输入匹配器一致：规则末尾没有 * 时按前缀匹配。"""
    return bool(pattern) and fnmatch.fnmatch(filename, pattern + ("" if pattern.endswith("*") else "*"))


def sync_renamed_files(recipe: Recipe, renamed: dict[str, tuple[str, str]]) -> list[str]:
    """renamed: {file_id: (旧文件名, 新文件名)}。就地修改 recipe，返回变更说明；没有受影响的输入时返回空列表。"""
    bindings = recipe.learned_from.get("input_bindings")
    if not renamed or not isinstance(bindings, dict):
        return []
    changes: list[str] = []
    touched: dict[str, str] = {}
    for inp in recipe.inputs:
        binding = bindings.get(inp.alias)
        file_id = binding.get("file_id") if isinstance(binding, dict) else None
        if file_id not in renamed:
            continue
        old, new = renamed[file_id]
        touched[old] = new
        if inp.source_file != new:
            changes.append(f"输入「{inp.alias}」来源文件名：{inp.source_file or '无'} → {new}")
            inp.source_file = new
        if old and old in inp.description:
            inp.description = inp.description.replace(old, new)
            changes.append(f"输入「{inp.alias}」说明已同步新文件名")
        if inp.file_pattern and not _matches(inp.file_pattern, new):
            pattern = file_pattern_from_name(new)
            changes.append(f"输入「{inp.alias}」文件规则：{inp.file_pattern} → {pattern}（原规则已匹配不上新文件名）")
            inp.file_pattern = pattern
    listed = recipe.learned_from.get("input_files")
    if touched and isinstance(listed, list):
        recipe.learned_from["input_files"] = [touched.get(item, item) for item in listed]
    # 旧文件名还可能出现在脚本、SQL、步骤说明、流程说明里：一起改掉，免得名字对不上
    for old, new in touched.items():
        if old and old != new:
            hits = replace_filename(recipe, old, new)
            if hits:
                changes.append(f"「{old}」→「{new}」已同步引用：{describe(hits)}")
    return changes
