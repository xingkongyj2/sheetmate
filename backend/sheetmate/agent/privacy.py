"""Privacy projection for derived summaries, automatic memories and diagnostics.

Source messages, files and execution evidence remain authoritative and are not
rewritten. These recognizers are a conservative baseline, not complete PII
detection; opaque business identifiers are deliberately left alone.
"""

from __future__ import annotations

import re


_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )?PRIVATE KEY-----"
)
_IMAGE_DATA = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+(?:;[a-zA-Z0-9=.+-]+)*,[a-zA-Z0-9+/=_%-]+", re.IGNORECASE
)
_EMAIL = re.compile(
    r"(?<![a-zA-Z0-9_.+-])[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?![a-zA-Z0-9_-])"
)
_MOBILE = re.compile(r"(?<![a-zA-Z0-9_./-])(?:\+?86[- ]?)?1[3-9]\d{9}(?![a-zA-Z0-9_./-])")
_BUSINESS_NUMBER_LABEL = re.compile(
    r"(?:订单号|订单编号|运单号|单据号|文件编号|金额|合计|数量|order[_ ]?id|tracking[_ ]?(?:id|number))\s*[:：=]\s*$",
    re.IGNORECASE,
)
_TOKEN = re.compile(
    r"(?<![a-zA-Z0-9_])(?:sk-(?:proj-|svcacct-)?[a-zA-Z0-9_-]{16,}|"
    r"gh[pousr]_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"eyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,})(?![a-zA-Z0-9_])"
)
_LABELED_SECRET = re.compile(
    r"(?P<label>\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"authorization)\b[\"']?\s*[=:：]\s*[\"']?)(?:Bearer\s+)?[a-zA-Z0-9._~+/=-]{8,}",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[a-zA-Z0-9._~+/=-]{12,}", re.IGNORECASE)


def redact_derived_text(text: str) -> str:
    """Hide recognizable personal contacts, credentials and inline image bytes."""
    value = str(text or "")
    value = _PRIVATE_KEY.sub("[PRIVATE_KEY_REDACTED]", value)
    value = _IMAGE_DATA.sub("[IMAGE_DATA_REDACTED]", value)
    value = _LABELED_SECRET.sub(lambda match: match["label"] + "[REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _TOKEN.sub("[TOKEN_REDACTED]", value)
    value = _EMAIL.sub("[EMAIL_REDACTED]", value)

    def mobile(match: re.Match[str]) -> str:
        # An explicitly labeled order/amount is a business value, even if its
        # digits happen to have the shape of a mobile telephone number.
        before = value[max(0, match.start() - 40):match.start()]
        return match[0] if _BUSINESS_NUMBER_LABEL.search(before) else "[PHONE_REDACTED]"

    return _MOBILE.sub(mobile, value)


def contains_sensitive_memory(text: str) -> bool:
    """Automatic memory writers should skip sensitive facts, not rewrite them."""
    value = str(text or "")
    return redact_derived_text(value) != value
