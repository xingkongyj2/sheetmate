"""脱敏模块：把联系方式和地址换成令牌后才交给正常模型；令牌只在下载文件时才在本机还原。

这是一个独立的模块：只用标准库，不依赖项目里的其他任何部分；别处只通过本文件导出的接口调用它。
真实值永远只留在本机（令牌库 vault.db 和密钥 privacy.key 都在数据目录下）。

底线：宁可不处理，也不能让一条没脱敏的数据被正常模型读到。
  - 任何一步出错或检查没通过，都抛出 PrivacyBlocked，调用方必须停止，不得继续把内容发给模型。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterable

from sheetmate.privacy.detect import ACTIONS, Finding, classify_column, merge, region_only_column
from sheetmate.privacy.events import KINDS, PrivacyLog, Scope, bind, current_scope
from sheetmate.privacy.guard import Guard, PrivacyBlocked, Registry, Stats, StreamRestorer
from sheetmate.privacy.rules import ACTION_LABELS, KIND_LABELS, RULES, RULES_VERSION, Rule, active_kinds, header_kind
from sheetmate.privacy.vault import TOKEN_RE, Vault, token_legend

__all__ = [
    "ACTIONS", "ACTION_LABELS", "KINDS", "KIND_LABELS", "RULES_VERSION", "TOKEN_RE", "Finding", "Guard", "Privacy",
    "PrivacyBlocked", "PrivacyLog", "Registry", "Scope", "Stats", "StreamRestorer", "Vault", "bind", "classify_column", "region_only_column",
    "RULES", "Rule", "active_kinds", "current_scope", "header_kind", "merge", "privacy", "token_legend",
]


class Privacy:
    """模块入口：持有令牌库、拦截器、记录。第一次使用时才创建，创建后做一次自检。"""

    def __init__(self) -> None:
        self.registry = Registry()
        self.log = PrivacyLog()
        self.vault: Vault | None = None
        self.guard: Guard | None = None
        self.data_dir: Path | None = None
        self.healthy = False
        self.error: str | None = None
        self.enabled = True                                   # 总开关（设置页）：只管上传文件时的自动分析和脱敏；发给 AI 的内容什么时候都要检查替换
        self.rule_ids: frozenset[str] = frozenset(rule.id for rule in RULES)   # 打开的规则
        self._lock = threading.RLock()

    def apply(self, enabled: bool, rule_ids: Iterable[str]) -> None:
        """应用设置页的脱敏设置（开关和每条规则）。随时可调用，立即生效。"""
        with self._lock:
            self.enabled, self.rule_ids = bool(enabled), frozenset(rule_ids)
            if self.guard is not None:
                self.guard.set_active(active_kinds(self.rule_ids))

    @property
    def configured(self) -> bool:
        return self.guard is not None

    def configure(self, data_dir: Path) -> None:
        """准备令牌库和日志，并自检。重复调用（同一个目录）什么也不做。"""
        with self._lock:
            if self.guard is not None and self.data_dir == data_dir:
                return
            if self.vault is not None:
                self.vault.close()
            self.data_dir = data_dir
            try:
                log_path = self.log.configure(data_dir / "log")
                self.vault = Vault(data_dir / "privacy")
                self.guard = Guard(self.vault, self.registry, self.log)
            except Exception as exc:  # 连令牌库都建不起来，就一律不放行
                self.guard, self.healthy, self.error = None, False, f"脱敏模块无法初始化：{exc}"
                self.log.emit("error", "错误", self.error)
                return
            failures = self.self_check()          # 自检用全部类型；通过之后才按设置页打开的规则收窄
            self.guard.set_active(active_kinds(self.rule_ids))
            self.healthy, self.error = not failures, ("；".join(failures) if failures else None)
            if failures:
                self.log.emit("error", "启动", f"脱敏模块自检失败，已拒绝向模型发送任何内容：{self.error}")
            else:
                self.log.emit("info", "启动", f"脱敏模块自检通过；令牌库位于 {self.vault.directory}，日志文件 {log_path}",
                              tokens=self.vault.count())

    def require(self) -> Guard:
        """拿到可用的拦截器；模块不可用时必须停止，不能绕过。"""
        if self.guard is None or not self.healthy:
            raise PrivacyBlocked(self.error or "脱敏模块还没有准备好，为保护数据已停止发送。")
        return self.guard

    def self_check(self) -> list[str]:
        """用一段假数据走一遍完整流程：该遮的全部遮住，该还原的原样还原。"""
        guard = self.guard
        assert guard is not None
        secrets = ("13800138000", "test.user@example.com", "文三路90号3栋502室", "13900139000", "杭州西湖XX驿站", "0571-88888888")
        text = ("收件人电话 13800138000，邮箱 test.user@example.com，座机 0571-88888888，"
                "地址：浙江省杭州市西湖区文三路90号3栋502室。")
        table = json.dumps({"columns": ["运单号", "收件手机", "收货地址"],
                            "rows": [["SF123456", "13900139000", "杭州西湖XX驿站"]]}, ensure_ascii=False, separators=(",", ":"))
        failures: list[str] = []
        try:
            for sample, json_safe in ((text, False), (table, True)):
                protected = guard.protect_text(sample, Stats())
                if any(secret in protected for secret in secrets):
                    failures.append("样例数据没有被完全遮住")
                if guard.restore_text(protected, json_safe=json_safe) != sample:
                    failures.append("样例数据没有被原样还原")
        except Exception as exc:
            failures.append(f"自检过程出错：{exc}")
        return failures


privacy = Privacy()
