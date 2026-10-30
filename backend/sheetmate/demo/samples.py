"""生成示例 Excel（快递月结账单场景）。

- 运费明细_2026-09.xlsx：第 1 行是合并的标题行，第 2 行才是表头；一个运单多行费用（运费/保价费/包装费）
- 店铺归属表.xlsx：店铺 → 归属（故意缺两家店，匹配不到要填「新食研」）
- 运费明细_2026-10.xlsx：下个月的新文件，列名换成了「单号」「店铺」，用来演示重复执行 + 字段映射
- 账单_人工结果_2026-09.xlsx：人工做好的期望结果（SheetAgent 的「输入 + 输出」学习样例）
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

SHOPS = ["鲜果日记旗舰店", "山野茶舍", "海风零食铺", "木子家居", "小满烘焙", "青柠生活馆", "北纬咖啡", "云朵母婴"]
OWNERS = {
    "鲜果日记旗舰店": ("华东事业部", "王敏"),
    "山野茶舍": ("华南事业部", "李想"),
    "海风零食铺": ("华东事业部", "王敏"),
    "木子家居": ("华北事业部", "赵磊"),
    "小满烘焙": ("华南事业部", "李想"),
    "青柠生活馆": ("华北事业部", "赵磊"),
}
FEE_TYPES = ["运费", "保价费", "包装费"]
DEFAULT_OWNER = "新食研"


def fee_rows(month: int, count: int, seed: int) -> list[dict]:
    """生成「一个运单多行费用」的明细。"""
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(count):
        waybill = f"SF{1460000000 + month * 100000 + i * 37}"
        day = dt.datetime(2026, month, 1 + (i * 7) % 28)
        shop = SHOPS[(i * 5 + month) % len(SHOPS)]
        weight = round(rng.uniform(0.3, 18.0), 1)
        freight = round(8 + weight * 2.2, 2)
        rows.append({"运单号": waybill, "下单日期": day, "店铺名称": shop, "费用类型": "运费", "金额": freight, "重量(kg)": weight})
        if i % 3 == 0:
            rows.append({"运单号": waybill, "下单日期": day, "店铺名称": shop, "费用类型": "保价费", "金额": round(rng.uniform(1, 6), 2), "重量(kg)": weight})
        if i % 5 == 1:
            rows.append({"运单号": waybill, "下单日期": day, "店铺名称": shop, "费用类型": "包装费", "金额": 3.0, "重量(kg)": weight})
    return rows


def _write_with_title(path: Path, title: str, df: pd.DataFrame, sheet: str = "明细") -> None:
    """写一个「第 1 行是合并标题、第 2 行是表头」的 Excel，模拟真实账单。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append([title])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(df.columns))
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)
    ws.append(list(df.columns))
    for row in df.itertuples(index=False, name=None):
        ws.append(list(row))
    for cell in ws[2]:
        cell.font = Font(bold=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def expected_result(fees: pd.DataFrame, owners: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """用 pandas 独立算出「人工结果」（与 SheetMate 引擎互相印证）。"""
    keys = ["运单号"]
    first = fees.groupby(keys, sort=False).agg({"下单日期": "first", "店铺名称": "first", "重量(kg)": "first"}).reset_index()
    wide = fees.pivot_table(index="运单号", columns="费用类型", values="金额", aggfunc="sum", fill_value=0).reset_index()
    for fee in FEE_TYPES:
        if fee not in wide.columns:
            wide[fee] = 0.0
    detail = first.merge(wide[["运单号", *FEE_TYPES]], on="运单号", how="left")
    detail["合计"] = (detail["运费"] + detail["保价费"] + detail["包装费"]).round(2)
    owner_map = dict(zip(owners["店铺"], owners["归属"]))
    detail["归属"] = detail["店铺名称"].map(owner_map).fillna(DEFAULT_OWNER)
    summary = detail.groupby("归属", sort=False).agg(票数=("运单号", "count"), 合计=("合计", "sum")).reset_index()
    summary["合计"] = summary["合计"].round(2)
    return {"账单明细": detail, "归属汇总": summary}


def write_samples(target: Path) -> dict[str, Path]:
    """写出全部示例文件，返回 {用途: 路径}。"""
    target.mkdir(parents=True, exist_ok=True)
    sep = pd.DataFrame(fee_rows(9, 48, seed=9))
    oct_ = pd.DataFrame(fee_rows(10, 55, seed=10)).rename(columns={"运单号": "单号", "店铺名称": "店铺"})
    owners = pd.DataFrame(
        [{"店铺": shop, "归属": owner, "负责人": person} for shop, (owner, person) in OWNERS.items()]
    )

    paths = {
        "fees_sep": target / "运费明细_2026-09.xlsx",
        "fees_oct": target / "运费明细_2026-10.xlsx",
        "owners": target / "店铺归属表.xlsx",
        "expected_sep": target / "账单_人工结果_2026-09.xlsx",
    }
    _write_with_title(paths["fees_sep"], "顺达物流 · 2026 年 9 月运费明细", sep)
    _write_with_title(paths["fees_oct"], "顺达物流 · 2026 年 10 月运费明细", oct_)
    with pd.ExcelWriter(paths["owners"], engine="openpyxl") as writer:
        owners.to_excel(writer, sheet_name="归属", index=False)
    with pd.ExcelWriter(paths["expected_sep"], engine="openpyxl") as writer:
        for name, frame in expected_result(sep, owners).items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return paths


def demo_recipe_body(fee_alias: str = "运费明细", owner_alias: str = "店铺归属") -> dict:
    """示例客户的 Recipe：长转宽 → 计算合计 → 匹配归属（匹配不到填新食研）→ 按归属汇总，输出两个 sheet。"""
    return {
        "name": "月结账单整理",
        "description": "把运费明细里一单多行的费用拆成列，算出每单合计，匹配店铺归属（匹配不到记为新食研），再按归属汇总票数和金额。",
        "inputs": [
            {
                "alias": fee_alias,
                "description": "每月的运费明细（一个运单可能有多行费用）",
                "file_pattern": "运费明细*",
                "sheet": "明细",
                "expected_columns": ["运单号", "下单日期", "店铺名称", "费用类型", "金额", "重量(kg)"],
                "required_columns": ["运单号", "店铺名称", "费用类型", "金额"],
                "key_columns": ["运单号"],
                "column_matching": "fuzzy",
                "expected_row_range": [20, 400],
                "header": {"header_row": 2, "header_rows": 1, "title": ""},
            },
            {
                "alias": owner_alias,
                "description": "店铺与归属事业部的对照表",
                "file_pattern": "店铺归属*",
                "sheet": "归属",
                "expected_columns": ["店铺", "归属", "负责人"],
                "required_columns": ["店铺", "归属"],
                "key_columns": ["店铺"],
                "column_matching": "fuzzy",
                "expected_row_range": [1, 200],
                "header": {"header_row": 1, "header_rows": 1, "title": ""},
            },
        ],
        "steps": [
            {
                "id": 1,
                "name": "费用拆成列",
                "action": "pivot",
                "formula": "pivot_wide",
                "params": {
                    "table": fee_alias,
                    "index": ["运单号"],
                    "pivot_column": "费用类型",
                    "value_column": "金额",
                    "aggfunc": "sum",
                    "carry_columns": ["下单日期", "店铺名称", "重量(kg)"],
                    "fill_value": 0,
                    "values": FEE_TYPES,
                },
                "output": "运单费用",
                "description": "每个运单一行，运费、保价费、包装费各一列，没有的费用记 0",
                "explanation": "人工结果里每个运单只有一行，费用类型变成了列",
                "confidence": 0.95,
                "assertions": [
                    {"type": "no_duplicate_keys", "params": {"columns": ["运单号"]}, "severity": "error"},
                    {"type": "no_dropped_values", "params": {}, "severity": "warning"},
                ],
            },
            {
                "id": 2,
                "name": "计算合计",
                "action": "calculate",
                "formula": "compute_column",
                "params": {"table": "运单费用", "column": "合计", "expression": 'round("运费" + "保价费" + "包装费", 2)'},
                "output": "运单合计",
                "description": "合计 = 运费 + 保价费 + 包装费",
                "explanation": "人工结果新增合计列，等于三项费用之和",
                "confidence": 0.95,
            },
            {
                "id": 3,
                "name": "匹配归属",
                "action": "lookup",
                "formula": "lookup",
                "params": {
                    "table": "运单合计",
                    "lookup_table": owner_alias,
                    "key": "店铺名称",
                    "lookup_key": "店铺",
                    "columns": ["归属"],
                    "missing_fill": {"归属": DEFAULT_OWNER},
                },
                "output": "账单明细",
                "description": "用店铺名称去店铺归属表找归属，找不到的记为「新食研」",
                "explanation": "业务规则：匹配不到归属的店铺统一记到新食研",
                "confidence": 0.9,
                "assertions": [{"type": "match_rate_min", "params": {"min": 0.5}, "severity": "warning"}],
            },
            {
                "id": 4,
                "name": "按归属汇总",
                "action": "aggregate",
                "formula": "group_aggregate",
                "params": {
                    "table": "账单明细",
                    "group_by": ["归属"],
                    "aggregations": [
                        {"column": "运单号", "func": "count", "as": "票数"},
                        {"column": "合计", "func": "sum", "as": "合计"},
                    ],
                },
                "output": "归属汇总",
                "description": "按归属统计票数和金额合计",
                "explanation": "人工结果第二个 sheet 是按归属的汇总",
                "confidence": 0.9,
            },
        ],
        "output_spec": [
            {"name": "账单明细", "source": "账单明细", "description": "每个运单一行的账单"},
            {"name": "归属汇总", "source": "归属汇总", "description": "按归属汇总"},
        ],
        "validation_rules": [
            {"type": "column_exists", "description": "账单必须有运单号、合计、归属", "params": {"columns": ["运单号", "合计", "归属"], "sheet": "账单明细"}, "severity": "error"},
            {"type": "unique_check", "description": "运单号不能重复", "params": {"columns": ["运单号"], "sheet": "账单明细"}, "severity": "error"},
            {"type": "no_nulls", "description": "归属不能为空", "params": {"columns": ["归属"], "sheet": "账单明细"}, "severity": "error"},
            {"type": "sum_preservation", "description": "账单合计必须等于运费明细的金额合计（防止漏算或重复算）",
             "params": {"column": "合计", "input": fee_alias, "input_column": "金额", "sheet": "账单明细", "tolerance": 0.0001},
             "severity": "error"},
            {"type": "sum_preservation", "description": "归属汇总的合计必须等于账单明细的合计",
             "params": {"column": "合计", "input": "账单明细", "input_column": "合计", "sheet": "归属汇总", "tolerance": 0.0001},
             "severity": "error"},
        ],
        "metadata": {"tags": ["月结账单", "长转宽", "匹配"], "confidence_overall": 0.92},
    }
