"""识别规则：联系方式（手机号、座机、邮箱、微信/QQ）和地址。

只用标准库。这里的每条规则都偏向「宁可多遮」：多遮的代价只是 AI 少看到几个样例，漏遮就是泄露。
"""

from __future__ import annotations

import numbers
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

RULES_VERSION = "5"   # 规则变了就加一：已缓存的识别结果会按新规则重新识别

KIND_LABELS = {
    "phone": "手机号", "landline": "座机", "email": "邮箱", "im": "微信/QQ",
    "address": "地址", "mixed": "含联系方式的文本", "custom": "通用",
}


@dataclass(frozen=True)
class Rule:
    """一条脱敏规则：在设置页里可以单独开关。以后要增加规则，在 RULES 里加一项（并让识别代码认得它的 kinds）即可。"""

    id: str
    name: str
    description: str
    kinds: tuple[str, ...]      # 这条规则负责的内容类型（对应 KIND_LABELS 里的键）


RULES: tuple[Rule, ...] = (
    Rule("phone", "电话号码", "手机号和座机，包括已被部分隐藏的写法（如 152****1234）", ("phone", "landline")),
    Rule("address", "详细地址", "带街道、门牌、小区、楼栋的详细地址，整个地址一起替换；只有省市的不处理", ("address",)),
)
ALL_KINDS = frozenset(KIND_LABELS)


def active_kinds(enabled_rules: Iterable[str]) -> frozenset[str]:
    """打开的规则负责的类型；「含联系方式的文本」（mixed）只是逐格扫描的方式，永远可用，真正遮什么仍由各类型决定。"""
    wanted = set(enabled_rules)
    return frozenset(kind for rule in RULES if rule.id in wanted for kind in rule.kinds) | {"mixed", "custom"}


# 令牌前缀：手机和座机共用 TEL，AI 不需要区分
KIND_PREFIX = {"phone": "TEL", "landline": "TEL", "email": "MAIL", "im": "IM", "address": "ADDR", "custom": "TXT"}
ACTION_LABELS = {
    "tokenize": "整列替换为令牌",
    "address_detail": "整个详细地址替换为令牌（只有省市的不动）",
    "cell_scan": "逐个单元格扫描替换",
}

# ---------------------------------------------------------------------------
# 列名
# ---------------------------------------------------------------------------

_NORMALIZE = re.compile(r"[\s（）()\[\]【】:：_\-/\\.,，、]+")

# 中文关键词按「包含」判断；英文关键词按单词判断（避免 hotel 命中 tel、email 命中 mail）。
_HEADER_CN = {
    "phone": ("手机", "移动电话", "联系电话", "联系方式", "联系号码", "联络电话", "电话", "联系手机", "收件电话", "寄件电话"),
    "landline": ("座机", "固话", "固定电话"),
    "email": ("邮箱", "电子邮件", "电子邮箱", "邮件地址"),
    "im": ("微信", "企业微信", "qq号", "qq"),
    "address": ("地址", "收货地", "收件地", "寄件地", "派送地", "派件地", "取件地", "详细地", "门牌", "发货地", "发件地", "始发地", "到货地", "送货地"),
}
_HEADER_EN_WORDS = {
    "phone": {"mobile", "phone", "cellphone", "cell", "tel", "contact", "mobileno", "phoneno"},
    "landline": {"landline", "telephone"},
    "email": {"email", "emailaddress", "mail"},
    "im": {"wechat", "weixin", "wx", "qq"},
    "address": {"address", "addr", "street"},
}
# 即使列名里有关键词，这些词说明它是别的东西（邮编、编号、品牌…）：不作为列名依据
_HEADER_NEGATIVE = ("邮编", "邮政编码", "区号", "地址编码", "地址编号", "地址类型", "是否", "品牌", "型号", "壳", "膜", "次数", "数量", "金额")
# 订单号、运单号这类长数字列，内容上像手机号也不能据此整列处理
_ID_LIKE_HEADER = ("单号", "编号", "编码", "订单", "运单", "流水", "批次", "条码", "id",
                   "月结", "账号", "账户", "客户号", "合同", "卡号", "票号", "工号")

_EN_TOKEN = re.compile(r"[A-Za-z]+")


def normalize_header(name: object) -> str:
    return _NORMALIZE.sub("", str(name)).lower()


def header_kind(name: object) -> tuple[str, str] | None:
    """按列名判断类型，返回 (kind, 命中的关键词)；没有命中返回 None。"""
    text = normalize_header(name)
    if not text or any(word in text for word in _HEADER_NEGATIVE):
        return None
    for kind in ("landline", "email", "im", "address", "phone"):  # 先看更具体的类型
        for word in _HEADER_CN[kind]:
            if word in text:
                return kind, word
    words = {item.lower() for item in _EN_TOKEN.findall(str(name))} | {text}
    for kind in ("landline", "email", "im", "address", "phone"):
        hit = words & _HEADER_EN_WORDS[kind]
        if hit:
            return kind, sorted(hit)[0]
    return None


_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def is_label(text: object) -> bool:
    """这段字是列头或标签（「收货地」「联系电话：」「地址」），不是内容：只有列名关键词本身，最多再带几个字，且没有数字。
    表头、表格上方的说明行常常混在数据列里；它们不是联系方式或地址，换成令牌只会让表看不懂。
    带了具体内容的不算（「收货地址：杭州西湖XX驿站」比关键词长得多，仍按地址处理）。"""
    raw = str(text).strip()
    norm = normalize_header(raw)
    if not norm or len(norm) > 8 or any(ch.isdigit() for ch in norm) or not _CJK_CHAR.search(norm):
        return False
    hit = header_kind(raw)
    return hit is not None and len(norm) <= len(normalize_header(hit[1])) + 3


def is_id_like_header(name: object) -> bool:
    """编号类的列（月结号、账号、单号…）：内容长得像电话也不能据此整列处理。以「号」结尾又不是电话/微信这类联系方式列名的，也算。"""
    text = normalize_header(name)
    return any(word in text for word in _ID_LIKE_HEADER) or (text.endswith("号") and header_kind(name) is None)


# ---------------------------------------------------------------------------
# 内容：整格匹配（用来判断一列）
# ---------------------------------------------------------------------------

_SEPARATORS = re.compile(r"[\s\-—()（）+]")
_MOBILE_FULL = re.compile(r"(?:86|0086)?1[3-9]\d{9}")
_LANDLINE_FULL = re.compile(r"0\d{9,11}(?:转\d{1,6})?")
# 来源系统已经遮了一部分的号码（152****1234、0571****8888）：同样要换成令牌
_MASK = "[*＊xX×●•]"
_MOBILE_MASKED_FULL = re.compile(rf"(?:86|0086)?1[3-9]\d{_MASK}{{3,6}}\d{{2,4}}")
_LANDLINE_MASKED_FULL = re.compile(rf"0\d{{2,3}}{_MASK}{{3,6}}\d{{2,4}}")
_EMAIL_FULL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def cell_text(value: object) -> str:
    """单元格 → 文本。Excel 常把手机号存成数字（13800138000.0）。"""
    if value is None:
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        value = int(value)
    return str(value).strip()


def _compact(text: str) -> str:
    """去掉分隔符；全角数字（１３８…）先转成半角，只用于判断，不改动原文。"""
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", text))


def is_mobile(text: str) -> bool:
    compact = _compact(text)
    return bool(_MOBILE_FULL.fullmatch(compact) or _MOBILE_MASKED_FULL.fullmatch(compact))


def is_landline(text: str) -> bool:
    # 400/800 是企业热线，不是个人号码
    compact = _compact(text)
    return bool(_LANDLINE_FULL.fullmatch(compact) or _LANDLINE_MASKED_FULL.fullmatch(compact))


def is_numeric_cell(value: object) -> bool:
    """数字类型的单元格（含 numpy 数字）；布尔不算。"""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)   # numpy 的整数、小数也是 numbers.Real


def is_email(text: str) -> bool:
    return bool(_EMAIL_FULL.fullmatch(text))


# ---------------------------------------------------------------------------
# 内容：文本里的片段（用来扫描任意文字）
# ---------------------------------------------------------------------------

# 前后不能紧挨数字或字母：运单号（SF…、YT…、纯长数字）里的片段不会被当成手机号
# 11 位号码，数字之间允许任意位置出现一个空格或横线（138 0013 8000、1380 013 8000、138-0013-8000 都认）
MOBILE_RE = re.compile(r"(?<![\dA-Za-z])(?:(?:\+|00)?86[\s-]?)?1[3-9](?:[\s-]?\d){9}(?!\d)")
# 座机：区号后面要有分隔符或括号，不然和 12 位的订单号分不开
LANDLINE_RE = re.compile(r"(?<![\dA-Za-z])(?:\(0\d{2,3}\)[\s-]?|0\d{2,3}[\s-])\d{7,8}(?:[-转]\d{1,6})?(?!\d)")
# 紧凑座机（057188888888）：区号必须是真实的区号开头（010、02x、03xx～09xx），再加 7～8 位，共 11～12 位；
# 这样不会碰到不以 0 开头的运单号、订单号
LANDLINE_COMPACT_RE = re.compile(r"(?<![\dA-Za-z])0(?:10|2\d|[3-9]\d{2})\d{7,8}(?!\d)")
# 半隐藏的手机号、座机：152****1234、152 **** 1234、0571-****8888
MOBILE_MASKED_RE = re.compile(rf"(?<![\dA-Za-z])(?:(?:\+|00)?86[\s-]?)?1[3-9]\d[\s-]?{_MASK}{{3,6}}[\s-]?\d{{2,4}}(?!\d)")
LANDLINE_MASKED_RE = re.compile(rf"(?<![\dA-Za-z])0\d{{2,3}}[\s-]?{_MASK}{{3,8}}[\s-]?\d{{2,4}}(?!\d)")
# 变形邮箱：user(at)example.com、user[at]example.com
EMAIL_OBF_RE = re.compile(r"(?<![\w.+\-])[A-Za-z0-9._%+\-]+\s*[(\[]\s*at\s*[)\]]\s*[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?![\w\-])", re.I)

# ---- 带上下文的值：前面有「电话 / 微信 / QQ / 地址」这样的标签时，不管后面的值长什么格式，都按联系方式处理 ----
# 这是「比格式规则更宽」的一层：格式规则认不出的写法（紧凑座机、各种分隔、微信号、写字楼地址），只要有标签就能兜住；
# 没有标签的纯数字（运单号、订单号）不受影响，所以不会把正常的快递数据误拦。
# 中文标签后面可以直接跟账号；英文标签（vx、wx、qq…）后面必须有冒号或空格，否则 wxid_abc 这类账号会被拆成「wx + id_abc」
IM_CTX_RE = re.compile(r"(?<![A-Za-z])(?:(?:微信号?|威信|v信|扣扣)\s*[:=]?\s*|(?:vx|wechat|weixin|wx|qq号?)(?:\s*[:=]\s*|\s+))"
                       r"([A-Za-z][A-Za-z0-9_\-]{4,19}|[1-9]\d{4,11})(?![A-Za-z0-9_])", re.I)
PHONE_CTX_RE = re.compile(r"(?<![A-Za-z])(?:手机号?码?|联系电话|联系方式|联系人电话|电话号码|电话|座机|固话|mobile|phone|tel|cell)"
                          r"\s*[:=]?\s*(\+?\d[\d\s\-()]{6,22}\d)(?!\d)", re.I)
ADDR_CTX_RE = re.compile(r"(?<!下载)(?<!网)(?<!邮件)(?<!IP)(?<!MAC)(?<!URL)(?<!链接)"
                         r"(?<!IP )(?<!MAC )(?<!URL )(?<!ip )(?<!mac )(?<!url )(?<!下载 )(?<!邮件 )(?<!链接 )"
                         r"(?:收货地址|收件地址|收货地|收件地|寄件地址|寄件地|派送地址|取件地址|详细地址|联系地址|家庭住址|住址|地址)"
                         r"\s*[:=]\s*([^\n,;|。，；]{4,60})")


def phone_context_ok(value: str) -> bool:
    """标签后面的一串数字，至少 7 位才算电话（避免把「电话 3 号窗口」这类误判）。"""
    return sum(ch.isdigit() for ch in value) >= 7


def address_context_ok(value: str) -> bool:
    """标签后面的值是否值得当作地址遮住：里面已经有令牌（复查时令牌会被换成 §）的不用再遮；
    太短又没有数字的（「地址：无」）不算。"""
    if "§" in value:
        return False
    # 脱敏副本检查时把各单元格用「¦」连起来：标签单元格后面紧跟的下一格如果本身也是个标签（「开票信息：」「发货地」），
    # 那不是这个标签的值，跳过它们接着看后面；后面是真地址的话照旧拦下
    if "¦" in value:
        parts = [part.strip() for part in value.split("¦")]
        while parts and (not parts[0] or parts[0].endswith((":", "：")) or is_label(parts[0])):
            parts.pop(0)
        if not parts:
            return False
        value = parts[0]
    return len(value.strip()) >= 6 or any(ch.isdigit() for ch in value)

# ---- 全角 → 半角 ----
# 位置一一对应（一个字换一个字），所以在折叠后的文字里找到的位置可以直接用在原文上，
# 这样 １３８００１３８０００、ｕｓｅｒ＠ｅｘａｍｐｌｅ．ｃｏｍ 这类写法也能识别，而且替换的是原文里的那一段。
_FOLD = str.maketrans({
    **{chr(0xFF10 + i): str(i) for i in range(10)},
    **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},
    **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)},
    "－": "-", "＋": "+", "＠": "@", "．": ".", "（": "(", "）": ")", "：": ":", "＝": "=", "＿": "_", "　": " ",
})


def fold(text: str) -> str:
    return text.translate(_FOLD)
EMAIL_RE = re.compile(r"(?<![\w.+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?![\w\-])")
