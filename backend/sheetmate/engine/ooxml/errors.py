"""OOXML 编辑的统一失败类型。"""

from __future__ import annotations


class OoxmlUnsupported(ValueError):
    """这次编辑无法被证明是安全的：调用方应丢弃结果，改用其他方式。

    它不是程序错误。模块的原则是"不确定就拒绝"——宁可不改，也不静默写出引用错位的文件。
    message 说明具体原因，便于日志与排查。
    """
