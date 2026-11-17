"""脱敏设置（设置页的总开关和规则开关）→ 脱敏模块。每次要用时读一次设置文件，改了立即生效，不用重启。"""

from __future__ import annotations

from sheetmate.core.config.settings import get_privacy_switches
from sheetmate.privacy import RULES, privacy

RULE_IDS = tuple(rule.id for rule in RULES)


def sync_privacy_settings() -> bool:
    """把保存的设置应用到脱敏模块，返回总开关是否打开。"""
    enabled, rules = get_privacy_switches(RULE_IDS)
    privacy.apply(enabled, [rule_id for rule_id, on in rules.items() if on])
    return enabled
