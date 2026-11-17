"""把每条脱敏规则的匹配逻辑摊开给用户看（设置页「查看」）：展示的就是代码里真正在用的正则和关键词，不是另写的一份说明。"""

from __future__ import annotations

from typing import Any

from sheetmate.privacy import address as _address
from sheetmate.privacy import detect as _detect
from sheetmate.privacy import rules as _r

Item = dict[str, Any]

# 与 address._STRONG 一一对应的名称（数量不一致时单元测试会报错，提醒这里要同步）
STRONG_ADDRESS_LABELS = (
    "省 + 市/区/县 + 路街小区等细节",
    "路名 + 门牌号",
    "小区/楼宇/园区 + 栋单元室",
    "镇/乡/村 + 名称 + 门牌号",
    "栋 + 单元 + 房间号",
    "写字楼：楼宇名 + 座/层 + 房间号",
)


def _regex(label: str, pattern: str) -> Item:
    shown = pattern.replace(_address._PROV, "〔省份名〕").replace(_address._CITY, "〔城市名〕").replace("一-龥", "\\u4e00-\\u9fa5")   # 汉字范围
    return {"label": label, "kind": "regex", "value": shown}


def _words(label: str, words: Any) -> Item:
    return {"label": label, "kind": "words", "value": sorted(words, key=str)}


def _text(label: str, text: str) -> Item:
    return {"label": label, "kind": "text", "value": text}


def _header_words(*kinds: str) -> list[str]:
    cn = [word for kind in kinds for word in _r._HEADER_CN[kind]]
    en = sorted({word for kind in kinds for word in _r._HEADER_EN_WORDS.get(kind, ())})
    return cn + en


def _shared_column_sections() -> list[dict[str, Any]]:
    return [{"title": "不当作编号处理 / 不看列名的情况", "items": [
        _words("列名含这些词时一律不处理（编号、品牌、金额等）", _r._HEADER_NEGATIVE),
        _words("编号类列名：内容像电话也不整列处理", _r._ID_LIKE_HEADER),
    ]}]


def phone_logic() -> list[dict[str, Any]]:
    return [
        {"title": "单元格或文字里的号码", "items": [
            _regex("手机号（可带 +86、空格、横线）", _r.MOBILE_RE.pattern),
            _regex("座机：区号 + 分隔符/括号 + 7～8 位", _r.LANDLINE_RE.pattern),
            _regex("紧凑座机（057188888888）", _r.LANDLINE_COMPACT_RE.pattern),
            _regex("半隐藏手机号（152****1234）", _r.MOBILE_MASKED_RE.pattern),
            _regex("半隐藏座机（0571-****8888）", _r.LANDLINE_MASKED_RE.pattern),
            _regex("带标签的号码：标签 + 至少 7 位数字", _r.PHONE_CTX_RE.pattern),
        ]},
        {"title": "整列判断", "items": [
            _words("列名含这些词：整列替换", _header_words("phone", "landline")),
            _text("列名没线索时", f"至少 {_detect.WHOLE_COLUMN_RATE:.0%} 的单元格是手机号或座机格式，才整列替换"),
            _text("同一格里除了号码还有文字", "只替换号码，其余文字（如姓名）保留"),
            _text("纯数字（如月结号、订单号）", "不会因为位数像电话而处理，只认带分隔符的座机、11 位手机号、半隐藏写法"),
        ]},
        *_shared_column_sections(),
        {"title": "脱敏模型（已配置时）", "items": [
            _text("参与方式", "列名是电话但样例里没有号码，模型可以否决；规则没发现、模型判为电话且样例里有 7 位以上数字，才采用"),
        ]},
    ]


def address_logic() -> list[dict[str, Any]]:
    weak = [word for word in _r._HEADER_CN["address"] if word not in _detect._STRONG_ADDRESS_WORDS]
    strong = [word for word in _r._HEADER_CN["address"] if word in _detect._STRONG_ADDRESS_WORDS]
    return [
        {"title": "单元格或文字里的详细地址", "items": [
            *[_regex(label, pattern.pattern) for label, pattern in zip(STRONG_ADDRESS_LABELS, _address._STRONG)],
            _regex("带标签的地址：标签 + 冒号 + 4～60 个字", _r.ADDR_CTX_RE.pattern),
        ]},
        {"title": "整列判断", "items": [
            _words("列名含这些词：一定是详细地址列", strong + sorted(_r._HEADER_EN_WORDS["address"])),
            _words("列名含这些词：可能只写省市，要看内容", weak),
            _text("列名没线索时", f"至少 {_detect.WHOLE_COLUMN_RATE:.0%} 的单元格匹配上面的详细地址格式，才整列替换"),
            _text("替换方式", "整个地址（含省市区）换成一个令牌；格子里的姓名保留"),
        ]},
        {"title": "不处理", "items": [
            _regex("只有省、市名称的内容（武汉、成都、武汉武汉、浙江省杭州市）", _address._REGION_ONLY.pattern),
            _text("列里全是省市的发货地、收货地列", "不识别，也不标记"),
        ]},
        {"title": "脱敏模型（已配置时）", "items": [
            _text("参与方式", "只有列名含「发货地」这类含糊词时，模型看过样例说不是地址就放过；规则没发现、模型判为地址且样例里有详细内容，才采用"),
        ]},
    ]


LOGIC = {"phone": phone_logic, "address": address_logic}


def describe(rule_id: str) -> list[dict[str, Any]]:
    build = LOGIC.get(rule_id)
    return build() if build else []
