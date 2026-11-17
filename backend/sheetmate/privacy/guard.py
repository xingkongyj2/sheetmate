"""出口拦截与入口还原。

发给正常模型的每一个字，先经过 Guard.protect_*；模型回来的每一个字，再经过 restore_* / StreamRestorer。
识别分四层，从精确到兜底：
  1. JSON：表格结构（columns + rows）、带列名的键值、带 samples 的列画像
  2. 文本表格：工具结果里常见的「列1 | 列2」竖线表
  3. 列画像文本行：「· 收件手机（text，…）样本：[…]」
  4. 文本片段：手机号、座机、邮箱、地址（任何位置）
最后再复查一遍；复查不通过就抛出 PrivacyBlocked，宁可不发。
"""

from __future__ import annotations

import ast
import json
import re
import threading
from collections import Counter, OrderedDict
from itertools import islice
from typing import Any, Callable, Iterable

from sheetmate.privacy.address import find_addresses, split_address
from sheetmate.privacy.detect import classify_column
from sheetmate.privacy.events import PrivacyLog
from sheetmate.privacy.rules import (
    ADDR_CTX_RE, ALL_KINDS, EMAIL_OBF_RE, EMAIL_RE, IM_CTX_RE, KIND_PREFIX, LANDLINE_COMPACT_RE, LANDLINE_MASKED_RE, LANDLINE_RE,
    MOBILE_MASKED_RE, MOBILE_RE, PHONE_CTX_RE,
    address_context_ok, cell_text, fold, header_kind, is_label, is_mobile, normalize_header, phone_context_ok,
)
from sheetmate.privacy.vault import MIN_KNOWN_CHARS, TOKEN_RE, Vault

FORCED = "!"       # 列类型后面带它：手动指定的整列，不管内容是什么每一格都换成这个类型的令牌
_JSON_START = re.compile(r"(?:^|(?<=[\s:：=]))[\[{]", re.M)      # 一段 JSON 可能的起点：行首，或前面是空白、冒号、等号
_CACHE_MIN_CHARS = 200
_CACHE_ENTRIES = 512
_CACHE_BYTES = 64 * 1024 * 1024    # 缓存总量上限：按字节算，不只按条数（一条历史消息可能有几 MB）
_CACHE_BYTES_PER_CHAR = 3          # 中文文字在内存里按每字约 3 字节估算
_MAX_DEPTH = 5  # JSON 里嵌 JSON 最多处理几层
_SAMPLE_KEYS = ("samples", "sample", "examples", "values", "top_values")
_ROW_KEYS = ("rows", "preview", "sample_rows", "rows_sample", "data")  # 和 columns 并列、按位置对齐的数据行
_NAME_KEYS = ("name", "column", "col", "列", "列名")
_SAMPLE_TAIL = re.compile(r"样本[：:]?\s*(?P<tail>.+?)\s*$")
_LEAD_MARKS = re.compile(r"^[\-\*·•\d\.\)、\s]+")
_QUOTED_NAME = re.compile(r"^(?:新列)?[「『\"“](.+?)[」』\"”]")
_NAME_END = re.compile(r"[（(：:，,\s]")
# 已知的敏感值整段出现时才替换：以空白、引号、逗号、顿号、括号、竖线为界，所以不会误伤别的值里恰好含有它的片段
_VALUE_RUN = re.compile(r"""[^\s'"“”‘’,，、;；|\[\](){}<>（）]+""")
_PARTIAL_TOKEN = re.compile(
    r"(?:T(?:E(?:L(?:_[A-Z2-7]{0,13})?)?)?|M(?:A(?:I(?:L(?:_[A-Z2-7]{0,13})?)?)?)?|I(?:M(?:_[A-Z2-7]{0,13})?)?"
    r"|A(?:D(?:D(?:R(?:_[A-Z2-7]{0,13})?)?)?)?)$"
)
_WORD_CHAR = re.compile(r"[A-Za-z0-9_]")


def _shadow(text: str) -> str:
    """用来找位置的文字：全角转半角，并把已经生成的令牌换成等长的「§」占位。

    令牌是随机字母数字，里面偶尔会出现 QQ、WX 这样的组合；不挖掉的话，规则会误匹配进令牌内部，把它再遮一半、弄坏。
    占位后位置仍和原文一一对应，找到的位置可以直接用在原文上。"""
    return fold(TOKEN_RE.sub(lambda match: "§" * len(match.group(0)), text))


class PrivacyBlocked(RuntimeError):
    """脱敏没有完成或检查没有通过：不能把这些内容交给正常模型。str(异常) 是给用户看的原因。"""

    code = "privacy_blocked"


class Registry:
    """已识别为敏感的列名（全局）：任何文件、任何位置出现同名列都按敏感处理。宁可多遮。"""

    def __init__(self) -> None:
        self._headers: dict[str, str] = {}
        self._lock = threading.Lock()
        # 识别结果每变一次就加一：处理结果的缓存带着它，列一旦被识别为敏感，之前缓存的旧结果就不能再用
        self.version = 0

    def replace_all(self, headers: dict[str, str]) -> None:
        with self._lock:
            fresh = {normalize_header(name): kind for name, kind in headers.items() if normalize_header(name)}
            if fresh != self._headers:
                self._headers = fresh
                self.version += 1

    def add(self, name: object, kind: str) -> None:
        key = normalize_header(name)
        if key:
            with self._lock:
                if key not in self._headers:
                    self._headers[key] = kind
                    self.version += 1

    def kind_for(self, name: object) -> str | None:
        return self._headers.get(normalize_header(name))

    def __len__(self) -> int:
        return len(self._headers)


class Stats:
    """一次请求里处理了什么（只有数量，没有内容）。"""

    def __init__(self) -> None:
        self.values: Counter[str] = Counter()
        self.methods: Counter[str] = Counter()
        self.texts = 0
        self.images = 0

    def add(self, kind: str, method: str, count: int = 1) -> None:
        self.values[kind] += count
        self.methods[method] += count

    def merge(self, other: "Stats") -> None:
        self.values.update(other.values)
        self.methods.update(other.methods)

    @property
    def total(self) -> int:
        return sum(self.values.values())


_NUMBER_TEXT = re.compile(r"[-+]?\d[\d,.\s]*")
# 整格就是一个号码（含空格、横线、括号和半隐藏的星号）；多出别的字就是「姓名 + 号码」这类混写
_PHONE_LIKE = re.compile(r"[\d\s\-—()（）+*＊xX×●•转]+")
_NAME_NOISE = re.compile(r"[\s,，、;；:：|/]+|收件人|收货人|联系人|姓名|电话|手机号?|地址")
_NAME_ONLY = re.compile(r"[\u4e00-\u9fff]{1,3}")


def _cannot_be(kind: str, value: object, text: str) -> bool:
    """这个格子是纯数字，不可能是 kind 这类内容。
    地址、邮箱：任何纯数字都不是；手机、座机、微信/QQ：位数太少（不足 5 位）的数不是（QQ 号至少 5 位，其余更长）。
    位数够的数（手机号常被表格存成数字）照旧整格处理，宁可多遮。"""
    numeric = isinstance(value, (bool, int, float)) or _NUMBER_TEXT.fullmatch(text.strip()) is not None
    if not numeric:
        return False
    if kind in {"address", "email"}:
        return True
    return sum(ch.isdigit() for ch in text) < 5


class Guard:
    def __init__(self, vault: Vault, registry: Registry, log: PrivacyLog) -> None:
        self.vault, self.registry, self.log = vault, registry, log
        self._cache: OrderedDict[tuple[int, int, frozenset[str], str], tuple[str, Counter[str], Counter[str]]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_costs: dict[tuple[int, int, frozenset[str], str], int] = {}
        self._cache_bytes = 0
        self._snippets: tuple[int, tuple[dict[str, str], str, list[int]]] | None = None
        self.active: frozenset[str] = ALL_KINDS      # 打开的规则负责的类型；没打开的类型一律不处理（见设置页的脱敏规则）

    def set_active(self, kinds: Iterable[str]) -> None:
        self.active = frozenset(kinds)

    def _off(self, kind: str) -> bool:
        kind = kind.rstrip(FORCED)
        return kind not in {"mixed", "custom"} and kind not in self.active

    # ------------------------------------------------------------------ 单元格

    def _token_cell(self, kind: str, value: object, stats: Stats, method: str) -> Any:
        text = cell_text(value)
        if not text or TOKEN_RE.search(text) or self._off(kind):
            return value
        forced, kind = kind.endswith(FORCED), kind.rstrip(FORCED)
        if kind == "custom" or forced:   # 用户在维护页里手动指定的整列：不管里面是什么，每一格都按选的类型换成令牌
            stats.add(kind, method)
            return self.vault.token(kind, text.strip())
        if is_label(text):
            return value             # 列头、「收货地址：」这类标签：不是内容，不换令牌
        if kind == "mixed":
            return self._scan(self._scan_learned(text, stats), stats)
        if _cannot_be(kind, value, text):
            # 列名含「收货地」「手机」这类词，但这个格子是个数（模型自己算出的「收货地价格缺失 = 0」之类）：
            # 数不可能是这种联系方式或地址。整格换成令牌会让 AI 把计数当成地址，查询结果就被读错。
            # 数字串里夹着手机号的情况仍然交给规则处理。
            out = self._scan(text, stats)
            return value if out == text else out
        if kind in {"phone", "landline"} and not _PHONE_LIKE.fullmatch(text):
            # 同一格里除了号码还有别的字（「张三 13800138000」）：只遮号码，姓名原样留着
            out = self._scan(text, stats)
            if out != text:
                return out
            if sum(ch.isdigit() for ch in text) < 5:
                return value                # 里面没有号码（多半是姓名、「无」「同上」）：不遮
        if kind == "address":
            kept = self._address_keeping_names(text, stats)
            if kept is not None:
                return kept
            if not split_address(text)[1].strip():
                return value                # 整格只有省、市（「武汉」「成都」「武汉武汉」）：没有详细地址，不遮
            stats.add("address", method)
            return self.vault.token("address", text.strip())   # 有详细地址：整个地址一起遮，省市区也不留
        stats.add(kind, method)
        return self.vault.token(kind, text)

    def _address_keeping_names(self, text: str, stats: Stats) -> str | None:
        """地址格子里，地址前后只多出一个姓名（最多 3 个字）和号码时：整个地址和号码遮掉，姓名留着。
        多出来的内容里有数字、或超过 3 个字时返回 None，交给整格处理：宁可多遮，也不能把门牌、楼栋号漏出去。"""
        spans = find_addresses(_shadow(text))
        if not spans:
            return None
        leftover = text
        for start, end in reversed(spans):
            leftover = leftover[:start] + leftover[end:]
        for pattern in (MOBILE_RE, MOBILE_MASKED_RE, LANDLINE_RE, LANDLINE_COMPACT_RE, LANDLINE_MASKED_RE):
            leftover = pattern.sub("", leftover)
        leftover = _NAME_NOISE.sub("", leftover)
        if not leftover or not _NAME_ONLY.fullmatch(leftover):
            return None
        out = self._scan(text, stats)
        return out if out != text else None

    def _column_kind(self, name: object, values: Iterable[object]) -> str | None:
        kind = self.registry.kind_for(name)
        if kind:
            return None if self._off(kind) else kind
        header = header_kind(name)
        if header:
            return None if self._off(header[0]) else header[0]
        finding = classify_column(name, values)
        return finding.kind if finding and not self._off(finding.kind) else None

    # ------------------------------------------------------------------ 1 JSON（递归：JSON 里的字符串可能又是 JSON、表格或样本行）

    def _protect(self, text: str, stats: Stats, depth: int = 0) -> str:
        """一段文字的完整处理流程。能解析成 JSON 就按结构处理，每个字符串叶子再完整处理一遍；否则按文本处理。"""
        data = self._try_json(text) if depth < _MAX_DEPTH else None
        if data is not None:
            before = stats.total
            data = self._walk(data, stats, depth)
            if stats.total == before:
                return text
            compact = ", " not in text[:300] and ": " not in text[:300]
            return json.dumps(data, ensure_ascii=False, separators=(",", ":") if compact else None)
        out = self._scan_learned(text, stats)                  # 整段正好是一个检查过的单元格 / 含有用户消息里的片段
        out = self._protect_embedded_json(out, stats, depth)   # 前面有说明文字、后面跟着 JSON（工具返回的就是这样）
        out = self._protect_pipe_tables(out, stats)
        out = self._protect_sample_lines(out, stats)
        out = self._scan_known(out, stats)
        return self._scan(out, stats)

    def _protect_embedded_json(self, text: str, stats: Stats, depth: int) -> str:
        """一段文字里夹着 JSON（比如 `[evidence_id=…]` 一行之后才是表格的 JSON）：把每一段 JSON 找出来，按结构处理，再放回原位。
        不这样的话，里面的表格只会被当成普通文字，按列、按单元格的识别结果都用不上。"""
        if depth >= _MAX_DEPTH or len(text) < 20 or ("{" not in text and "[" not in text):
            return text
        decoder, pieces, last, position = json.JSONDecoder(), [], 0, 0
        for match in islice(_JSON_START.finditer(text), 200):
            start = match.start()
            if start < position:
                continue                                       # 在前一段 JSON 里面
            try:
                data, end = decoder.raw_decode(text, start)
            except ValueError:
                continue
            position = end
            if not isinstance(data, (dict, list)) or end - start < 20:
                continue
            before = stats.total
            changed = self._walk(data, stats, depth)
            if stats.total != before:
                head = text[start:start + 300]
                compact = ", " not in head and ": " not in head
                pieces += [text[last:start], json.dumps(changed, ensure_ascii=False, separators=(",", ":") if compact else None)]
                last = end
        return "".join(pieces) + text[last:] if pieces else text

    @staticmethod
    def _try_json(text: str) -> Any:
        if text.lstrip()[:1] not in {"{", "["}:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _walk(self, obj: Any, stats: Stats, depth: int = 0) -> Any:
        if isinstance(obj, str):
            return self._protect(obj, stats, depth + 1) if len(obj) >= 4 else obj
        if isinstance(obj, bool) or obj is None:
            return obj
        if isinstance(obj, (int, float)):
            # JSON 里以数字存的手机号：换成字符串令牌，保证 JSON 仍然有效
            return self._token_cell("phone", obj, stats, "json_number") if is_mobile(cell_text(obj)) else obj
        if isinstance(obj, list):
            return [self._walk(item, stats, depth) for item in obj]
        if not isinstance(obj, dict):
            return obj
        columns = obj.get("columns")
        rows_key = next((key for key in _ROW_KEYS if isinstance(obj.get(key), list) and obj[key]
                         and all(isinstance(r, list) for r in obj[key])), None)
        handled: set[str] = set()
        if isinstance(columns, list) and columns and all(isinstance(c, str) for c in columns) and rows_key:
            rows = obj[rows_key]
            kinds = [self._column_kind(name, [r[i] for r in rows if i < len(r)]) for i, name in enumerate(columns)]
            # 被识别的列整列替换；其他列的单元格也过一遍（改了列名、别的表里同名列、夹着号码的文字，都不能漏）
            new_rows = []
            for row in rows:
                new_rows.append([self._token_cell(kinds[i], cell, stats, "json_table") if i < len(kinds) and kinds[i]
                                 else self._walk(cell, stats, depth) for i, cell in enumerate(row)])
            obj = {**obj, rows_key: new_rows}
            handled.add(rows_key)
        label = next((obj[key] for key in _NAME_KEYS if isinstance(obj.get(key), str)), None)
        sample_key = next((key for key in _SAMPLE_KEYS if isinstance(obj.get(key), list)), None)
        if label is not None and sample_key is not None:
            kind = self._column_kind(label, [v for v in obj[sample_key] if not isinstance(v, (dict, list))])
            samples = obj[sample_key]
            obj = {**obj, sample_key: [self._token_cell(kind, v, stats, "json_samples") if kind and not isinstance(v, (dict, list))
                                       else self._walk(v, stats, depth) for v in samples]}
            handled.add(sample_key)
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if key in handled:
                result[key] = value
                continue
            kind = self.registry.kind_for(key) or (header_kind(key) or (None,))[0]
            if kind and isinstance(value, (str, int, float)) and not isinstance(value, bool):
                result[key] = self._token_cell(kind, value, stats, "json_key")
            else:
                result[key] = self._walk(value, stats, depth)
        return result

    # ------------------------------------------------------------------ 2 竖线表格

    def _protect_pipe_tables(self, text: str, stats: Stats) -> str:
        if " | " not in text:
            return text
        lines = text.split("\n")
        i, changed = 0, False
        while i < len(lines) - 1:
            separators = lines[i].count(" | ")
            if separators and lines[i + 1].count(" | ") == separators:
                header = lines[i].split(" | ")
                j, rows = i + 1, []
                while j < len(lines) and lines[j].count(" | ") == separators and not lines[j].startswith("…"):
                    rows.append(lines[j].split(" | "))
                    j += 1
                kinds = [self._column_kind(name, [r[k] for r in rows]) for k, name in enumerate(header)]
                for row_index, row in enumerate(rows):
                    new = [str(self._token_cell(kinds[k], cell, stats, "text_table")) if kinds[k] else self._scan_learned(cell, stats)
                           for k, cell in enumerate(row)]
                    if new != row:
                        changed = True
                        lines[i + 1 + row_index] = " | ".join(new)
                i = j
            else:
                i += 1
        return "\n".join(lines) if changed else text

    # ------------------------------------------------------------------ 3 画像文本行

    @staticmethod
    def _line_column_name(prefix: str) -> str | None:
        """「样本」前面那段文字里的列名：`· 收货地址（…`、`- 收货地址：…`、`新列「收货地址」（…` 都认。"""
        text = _LEAD_MARKS.sub("", prefix.strip())
        quoted = _QUOTED_NAME.match(text)
        if quoted:
            return quoted.group(1)
        name = _NAME_END.split(text, 1)[0].strip()
        return name or None

    @staticmethod
    def _parse_samples(tail: str) -> tuple[list[Any], str] | None:
        """样本列表有两种写法：Python 列表 `['a', 'b']`，或用顿号连起来 `a、b、c`。"""
        tail = tail.strip()
        if tail.startswith("["):
            end = tail.rfind("]")
            try:
                value = ast.literal_eval(tail[:end + 1])
            except (ValueError, SyntaxError):
                return None
            return (value, "list") if isinstance(value, list) else None
        return (tail.split("、"), "joined") if tail else None

    def _protect_sample_lines(self, text: str, stats: Stats) -> str:
        """画像、记忆、规划里到处有「列名 … 样本 …」这样的行，格式各不相同：按『样本』二字定位，统一处理。"""
        if "样本" not in text:
            return text
        lines = text.split("\n")
        changed = False
        for index, line in enumerate(lines):
            match = _SAMPLE_TAIL.search(line)
            if not match:
                continue
            name = self._line_column_name(line[:match.start()])
            parsed = self._parse_samples(match.group("tail")) if name else None
            if not parsed:
                continue
            samples, style = parsed
            kind = self._column_kind(name, [v for v in samples if not isinstance(v, (dict, list))])
            if not kind:
                continue
            new = [v if isinstance(v, (dict, list)) else self._token_cell(kind, v, stats, "sample_line") for v in samples]
            if new != samples:
                tail = repr(new) if style == "list" else "、".join(str(v) for v in new)
                lines[index] = line[:match.start("tail")] + tail
                changed = True
        return "\n".join(lines) if changed else text

    # ------------------------------------------------------------------ 4 已知的敏感值（不依赖任何格式）

    def register_values(self, kind: str, values: Iterable[object]) -> int:
        """登记敏感列里的真实值：之后不管它出现在发给模型的什么文字里，都会被换成令牌。只登记地址和微信/QQ。"""
        if kind not in {"address", "im"} or self._off(kind):       # 「通用」的整列不登记：见 Vault 里的说明
            return 0
        kept = []
        for value in values:
            text = cell_text(value)
            if TOKEN_RE.search(text):
                continue  # 已经是令牌了
            if kind == "address" and not split_address(text)[1].strip():
                continue  # 只有省市区的不算
            kept.append(text)
        return self.vault.remember(kind, kept)

    def _scan_learned(self, text: str, stats: Stats) -> str:
        """脱敏模型事先判断出来的：
        1. 逐个检查过的单元格——整段文字正好是那个单元格，里面摘出的片段全部替换；
        2. 摘出的片段本身——不管后面嵌在多长的文字里（历史消息引用「客户备注：放菜鸟驿站旁边小卖部」），都要替换。
        这是规则不好覆盖的部分（没有路名门牌的地点、各种写法的联系方式），交给私部署的模型来判断。"""
        hits = self.vault.cell_hits().get(text)
        if hits:
            for snippet, kind in sorted(hits, key=lambda item: len(item[0]), reverse=True):
                if snippet in text and not TOKEN_RE.search(snippet) and not is_label(snippet):   # 模型曾把列头当成地址摘出来过：不理它
                    text = text.replace(snippet, self._pattern_token(kind, snippet, stats, "learned"))
        return self._scan_snippets(text, stats)

    def _snippet_index(self) -> tuple[dict[str, str], str, list[int]]:
        """所有学到的片段 → 类型。登记新发现时 known_version 会变，索引随之重建。
        太短的（不足 MIN_KNOWN_CHARS）、令牌、列头标签、只有行政区的地址不进索引：它们会误伤普通文字。"""
        version = self.vault.known_version
        cached = self._snippets
        if cached is not None and cached[0] == version:
            return cached[1]
        index: dict[str, str] = {}
        for items in self.vault.cell_hits().values():
            for snippet, kind in items:
                snippet = str(snippet).strip()
                if (len(snippet) < MIN_KNOWN_CHARS or kind not in KIND_PREFIX or TOKEN_RE.search(snippet) or is_label(snippet)
                        or kind == "address" and not split_address(snippet)[1].strip()):
                    continue
                index.setdefault(snippet, kind)
        built = (index, "".join({snippet[0] for snippet in index}), sorted({len(snippet) for snippet in index}, reverse=True))
        self._snippets = (version, built)
        return built

    def _scan_snippets(self, text: str, stats: Stats) -> str:
        index, firsts, lengths = self._snippet_index()
        if not index or len(text) < MIN_KNOWN_CHARS:
            return text
        out: list[str] = []
        position, size, last = 0, len(text), 0
        while position < size:
            if text[position] in firsts:
                for length in lengths:                       # 从长到短：同一处有长短两个片段时遮长的
                    kind = index.get(text[position:position + length]) if position + length <= size else None
                    if kind:
                        out += [text[last:position], self._pattern_token(kind, text[position:position + length], stats, "learned")]
                        position += length
                        last = position
                        break
                else:
                    position += 1
            else:
                position += 1
        return text if not out else "".join(out) + text[last:]

    def _scan_known(self, text: str, stats: Stats) -> str:
        known = self.vault.known()
        if not known:
            return text

        def replace(match: re.Match[str]) -> str:
            run = match.group(0)
            kind = known.get(run)
            if kind == "address" and not split_address(run)[1].strip():
                return run          # 旧版本登记进令牌库的「武汉」「上海」这类只有行政区的值：不是详细地址，不遮
            return run if kind is None else str(self._token_cell(kind, run, stats, "known_value"))

        return _VALUE_RUN.sub(replace, text)

    # ------------------------------------------------------------------ 4 文本片段

    def _scan(self, text: str, stats: Stats) -> str:
        """扫描一段文字里的联系方式和地址。找位置都在「全角转半角」之后的文字上，替换的却是原文那一段（两者位置一一对应），
        所以全角写法也能识别，还原时拿回的是原样的值。"""
        # 先处理地址：地址里夹着的电话号码一起进地址令牌，还原时整段原样回来
        for start, end in reversed(find_addresses(_shadow(text)) if "address" in self.active else []):
            segment = text[start:end]
            if TOKEN_RE.search(segment):
                continue
            if not split_address(segment)[1].strip():
                continue                    # 只有省市，没有详细地址
            stats.add("address", "pattern")
            text = text[:start] + self.vault.token("address", segment.strip()) + text[end:]
        # 带标签的值：「电话：…」「微信 …」「收件地址：…」，不管值长什么格式都遮（比格式规则更宽，又不会误伤没有标签的单号）
        text = self._mask(text, ADDR_CTX_RE, "address", stats, "context", group=1, accept=address_context_ok)
        text = self._mask(text, IM_CTX_RE, "im", stats, "context", group=1)
        text = self._mask(text, PHONE_CTX_RE, "phone", stats, "context", group=1, accept=phone_context_ok)
        for kind, pattern in (("email", EMAIL_RE), ("email", EMAIL_OBF_RE), ("phone", MOBILE_RE), ("phone", MOBILE_MASKED_RE),
                              ("landline", LANDLINE_RE), ("landline", LANDLINE_COMPACT_RE), ("landline", LANDLINE_MASKED_RE)):
            text = self._mask(text, pattern, kind, stats, "pattern")
        return text

    def _mask(self, text: str, pattern: re.Pattern[str], kind: str, stats: Stats, method: str, *, group: int = 0,
              accept: Callable[[str], bool] | None = None) -> str:
        spans = [(match.start(group), match.end(group)) for match in pattern.finditer(_shadow(text))]
        for start, end in reversed(spans):
            original = text[start:end]
            if TOKEN_RE.search(original) or (accept is not None and not accept(original)):
                continue
            if kind == "address":
                replacement = str(self._token_cell("address", original, stats, method))   # 有详细地址就整个遮
                if replacement == original:
                    continue
            else:
                replacement = self._pattern_token(kind, original, stats, method)
            text = text[:start] + replacement + text[end:]
        return text

    def _pattern_token(self, kind: str, value: str, stats: Stats, method: str = "pattern") -> str:
        if self._off(kind):
            return value
        stats.add(kind, method)
        return self.vault.token(kind, value)

    # ------------------------------------------------------------------ 对外

    def leaks(self, text: str) -> list[str]:
        """处理完之后再查一遍：还剩下什么联系方式或地址（令牌本身不算）。"""
        bare = fold(TOKEN_RE.sub("§", text))
        active = self.active
        found = []
        if "email" in active and (EMAIL_RE.search(bare) or EMAIL_OBF_RE.search(bare)):
            found.append("邮箱")
        if "phone" in active and (MOBILE_RE.search(bare) or MOBILE_MASKED_RE.search(bare)
                                  or any(phone_context_ok(m.group(1)) for m in PHONE_CTX_RE.finditer(bare))):
            found.append("手机号")
        if "landline" in active and (LANDLINE_RE.search(bare) or LANDLINE_COMPACT_RE.search(bare) or LANDLINE_MASKED_RE.search(bare)):
            found.append("座机")
        if "im" in active and IM_CTX_RE.search(bare):
            found.append("微信/QQ")
        if "address" in active and (find_addresses(bare) or any(address_context_ok(m.group(1)) and split_address(m.group(1))[1].strip()
                                                               for m in ADDR_CTX_RE.finditer(bare))):
            found.append("地址")
        return found

    def protect_text(self, text: str, stats: Stats) -> str:
        if not text:
            return text
        stats.texts += 1
        cacheable = len(text) >= _CACHE_MIN_CHARS
        # 登记了新的敏感值、或识别出新的敏感列，旧的处理结果就不能再用
        key = (self.vault.known_version, self.registry.version, self.active, text)
        if cacheable:
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit is not None:
                    self._cache.move_to_end(key)
            if hit is not None:
                stats.values.update(hit[1])
                stats.methods.update(hit[2])
                return hit[0]
        local = Stats()
        out = self._protect(text, local)
        remaining = self.leaks(out)
        if remaining:
            raise PrivacyBlocked(f"发送前检查发现仍有{'、'.join(remaining)}没能处理，为保护数据已停止发送。")
        stats.merge(local)
        if cacheable:
            cost = (len(text) + len(out)) * _CACHE_BYTES_PER_CHAR   # 键里存着全文、值里也存着全文
            with self._cache_lock:
                if key in self._cache:
                    self._cache_bytes -= self._cache_costs.pop(key, 0)
                self._cache[key] = (out, local.values, local.methods)
                self._cache_costs[key] = cost
                self._cache_bytes += cost
                while self._cache and (len(self._cache) > _CACHE_ENTRIES or self._cache_bytes > _CACHE_BYTES):
                    old, _ = self._cache.popitem(last=False)
                    self._cache_bytes -= self._cache_costs.pop(old, 0)
        return out

    def protect_cell(self, kind: str | None, value: Any, stats: Stats) -> Any:
        """一个单元格：所在列被识别为敏感（kind 不为空）就整格换成令牌，否则只处理里面夹着的联系方式和地址。
        和发给 AI 时用的是同一套逻辑，用来生成「AI 看到的样子」。"""
        if value is None or isinstance(value, bool) or (isinstance(value, float) and value != value):
            return value
        if kind:
            return self._token_cell(kind, value, stats, "copy")
        if isinstance(value, str) and len(value) >= 4:
            return self._scan(self._scan_known(self._scan_learned(value, stats), stats), stats)
        if isinstance(value, (int, float)) and is_mobile(cell_text(value)):
            return self._token_cell("phone", value, stats, "copy")   # 数字格式的手机号：发给 AI 时同样会换
        return value

    def protect_message(self, message: dict[str, Any], stats: Stats) -> dict[str, Any]:
        out = dict(message)
        for key in ("content", "reasoning_content"):
            value = message.get(key)
            if isinstance(value, str):
                out[key] = self.protect_text(value, stats)
            elif isinstance(value, list):
                parts = []
                for part in value:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append({**part, "text": self.protect_text(part["text"], stats)})
                    else:
                        if isinstance(part, dict) and part.get("type") not in {None, "text"}:
                            stats.images += 1
                        parts.append(part)
                out[key] = parts
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            new_calls = []
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    call = {**call, "function": {**function, "arguments": self.protect_text(function["arguments"], stats)}}
                new_calls.append(call)
            out["tool_calls"] = new_calls
        return out

    def protect_messages(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Stats]:
        stats = Stats()
        return [self.protect_message(message, stats) for message in messages], stats

    # ------------------------------------------------------------------ 还原

    def restore_text(self, text: str, *, json_safe: bool = False, counter: Counter[str] | None = None) -> str:
        if not text or "_" not in text:
            return text

        def replace(match: re.Match[str]) -> str:
            value = self.vault.reveal(match.group(0))
            if counter is not None:
                counter["restored" if value is not None else "unknown"] += 1
            if value is None:
                return match.group(0)
            return json.dumps(value, ensure_ascii=False)[1:-1] if json_safe else value

        return TOKEN_RE.sub(replace, text)

    def restore_object(self, value: Any, counter: Counter[str] | None = None) -> Any:
        if isinstance(value, str):
            return self.restore_text(value, counter=counter)
        if isinstance(value, list):
            return [self.restore_object(item, counter) for item in value]
        if isinstance(value, dict):
            return {key: self.restore_object(item, counter) for key, item in value.items()}
        return value

    def restorer(self, *, json_safe: bool = False, counter: Counter[str] | None = None) -> "StreamRestorer":
        return StreamRestorer(self, json_safe=json_safe, counter=counter)


class StreamRestorer:
    """流式还原：令牌可能被切在两个片段中间，所以把结尾可能是令牌开头的部分先留着，等后面的内容到了再处理。"""

    def __init__(self, guard: Guard, *, json_safe: bool = False, counter: Counter[str] | None = None) -> None:
        self._guard, self._json_safe, self._counter = guard, json_safe, counter
        self._held = ""

    def feed(self, piece: str) -> str:
        buffer = self._held + (piece or "")
        match = _PARTIAL_TOKEN.search(buffer)
        cut = len(buffer)
        if match and (match.start() == 0 or not _WORD_CHAR.match(buffer[match.start() - 1])):
            cut = match.start()
        self._held = buffer[cut:]
        return self._guard.restore_text(buffer[:cut], json_safe=self._json_safe, counter=self._counter)

    def flush(self) -> str:
        buffer, self._held = self._held, ""
        return self._guard.restore_text(buffer, json_safe=self._json_safe, counter=self._counter)
