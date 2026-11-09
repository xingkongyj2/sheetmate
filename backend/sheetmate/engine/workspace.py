"""DuckDB 工作区。

TabClaw 把会话里的表放在内存字典 tables[table_id] 里；SheetMate 换成一个 DuckDB 数据库：

- 会话工作区：data/workspaces/{会话 id}.duckdb —— 上传的表、AI 计算出来的结果表都在这里，重启不丢
- 运行工作区：data/runs/{运行 id}.duckdb —— Recipe 每一步的中间表都保留，支持单步查看与断点续跑

安全：连接打开后立即关闭外部访问（不能读写本机文件、不能联网、不能装扩展），并锁定配置。
用户或 AI 写的 SQL 只允许单条 SELECT；建表只由引擎自己执行。
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pandas as pd

from sheetmate.engine.sqlutil import assert_single_select, frame_rows, quote_ident, sanitize_table_name

_CATALOG = "__sm_catalog"
_TMP_VIEW = "__sm_incoming"


class WorkspaceError(ValueError):
    """工作区操作失败（表不存在、SQL 执行错误等）。消息直接展示给用户/AI。"""


def duck_type_label(duck_type: str) -> str:
    """DuckDB 类型 → 粗粒度类型。"""
    t = duck_type.upper()
    if t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")):
        return "integer"
    if t.startswith(("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC")):
        return "float"
    if t.startswith(("DATE", "TIMESTAMP", "TIME")):
        return "datetime"
    if t.startswith("BOOLEAN"):
        return "boolean"
    return "text"


@dataclass
class TableInfo:
    """工作区里一张表的登记信息。"""

    name: str  # SQL 表名：AI 调用工具、Recipe 步骤里都用这个名字
    label: str = ""  # 展示名，例如「运费明细.xlsx · Sheet1」
    source: str = "computed"  # uploaded（上传）/ computed（计算结果）
    rows: int = 0
    columns: list[dict[str, str]] = field(default_factory=list)  # [{"name", "type"}]
    file_id: str | None = None
    file_version: str = ""  # 原件内容哈希；只有从该原件完整读取时才设置
    filename: str = ""
    sheet: str | None = None
    header: dict[str, Any] | None = None  # 读取时的表头信息（生成 Recipe 输入定义用）
    role: str = "input"  # input 输入 / expected 人工做好的期望结果 / result 流程产出
    created_by: str = ""  # 产生这张表的工具/步骤
    description: str = ""
    # 由公式生成的表记下「公式 + 参数」，分析模式里可以据此把结果沉淀成 Recipe
    step: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    # 表的内容版本与结构化探测记忆独立于对话历史。只有实际重建表才更换版本。
    revision: str = ""
    probe_memory: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableInfo:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def column_names(self) -> list[str]:
        return [c["name"] for c in self.columns]


class Workspace:
    """一个 DuckDB 工作区。所有方法线程安全（同一时间只有一个操作在用连接）。"""

    def __init__(self, path: Path | None = None):
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = self._connect()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """打开连接并进入沙箱：禁止访问外部文件/网络/扩展，锁定配置防止被 SQL 改回去。"""
        con = duckdb.connect(str(self.path) if self.path else ":memory:")
        con.execute("SET enable_external_access = false").fetchall()
        con.execute("SET lock_configuration = true").fetchall()
        con.execute(f"CREATE TABLE IF NOT EXISTS {_CATALOG} (name VARCHAR PRIMARY KEY, meta VARCHAR)").fetchall()
        return con

    def _run(self, sql: str, params: list[Any] | None = None, *, as_df: bool = False) -> Any:
        """所有 SQL 都从这里执行，并且立即取完全部结果（返回行列表或 DataFrame）。

        - DuckDB 2.0 要求上一个结果取完才能在同一连接上做别的事（例如注册 DataFrame），所以这里一律取完
        - DuckDB 遇到内部错误（InternalException / FatalException）会把整个数据库标记为失效，之后任何操作都会失败。
          这里捕获后自动重新打开工作区文件（已提交的表都还在），再把错误转成 WorkspaceError 交给上层（AI 会换种写法重试）
        """
        try:
            cursor = self._con.execute(sql, params or [])
            return cursor.df() if as_df else cursor.fetchall()
        except (duckdb.FatalException, duckdb.InternalException) as exc:
            self._recover()
            raise WorkspaceError(f"计算引擎内部错误，已自动恢复工作区，请换一种写法：{_friendly_error(exc)}") from exc
        except duckdb.Error as exc:
            raise WorkspaceError(_friendly_error(exc)) from exc

    def _recover(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass
        self._con = self._connect()

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._con.close()
            except duckdb.Error:
                pass

    def interrupt(self) -> None:
        """中断正在执行的查询（给「超时中止」用）。会从别的线程调用，所以这里不能拿锁——执行查询的线程正拿着它。"""
        try:
            self._con.interrupt()
        except duckdb.Error:
            pass

    @contextmanager
    def locked(self):
        """对读取版本、分析与保存记忆等复合操作持有同一把可重入锁。"""
        with self._lock:
            yield self

    def exists(self, name: str) -> bool:
        with self._lock:
            rows = self._run("SELECT count(*) FROM duckdb_tables() WHERE table_name = ? AND schema_name = 'main'", [name])
        return bool(rows and rows[0][0])

    def require(self, name: str) -> TableInfo:
        """取表信息；不存在时报错并列出现有的表（方便 AI 自我纠正）。"""
        info = self.info(name)
        if info is None:
            names = "、".join(t.name for t in self.list_tables()) or "（没有表）"
            raise WorkspaceError(f"表「{name}」不存在。现有的表：{names}")
        return info

    def unique_name(self, base: str) -> str:
        """生成不重名的表名：订单、订单_2、订单_3…"""
        name = sanitize_table_name(base)
        if not self.exists(name):
            return name
        i = 2
        while self.exists(f"{name}_{i}"):
            i += 1
        return f"{name}_{i}"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def put_dataframe(self, name: str, df: pd.DataFrame, **meta: Any) -> TableInfo:
        """把 DataFrame 存成表（同名覆盖）。"""
        df = df.copy()
        df.columns = [str(c) for c in df.columns]
        with self._lock:
            self._con.register(_TMP_VIEW, df)
            try:
                self._run(f"CREATE OR REPLACE TABLE {quote_ident(name)} AS SELECT * FROM {_TMP_VIEW}")
            finally:
                try:
                    self._con.unregister(_TMP_VIEW)
                except duckdb.Error:
                    pass
            return self._register(name, **meta)

    def create_table_as(self, name: str, select_sql: str, **meta: Any) -> TableInfo:
        """执行引擎生成的 SELECT，结果存成新表（同名覆盖）。select_sql 只能来自引擎内部。"""
        with self._lock:
            self._run(f"CREATE OR REPLACE TABLE {quote_ident(name)} AS {select_sql}")
            return self._register(name, **meta)

    def copy_table(self, source: str, target: str, **meta: Any) -> TableInfo:
        """复制一张表（用于把会话里的表带进运行工作区等场景）。"""
        from sheetmate.engine.table_memory import copy_table_memory

        with self._lock:
            source_info = self.require(source)
            self.create_table_as(target, f"SELECT * FROM {quote_ident(source)}", **meta)
            copy_table_memory(source_info, self, target)
            return self.require(target)

    def drop(self, name: str) -> None:
        with self._lock:
            self._run(f"DROP TABLE IF EXISTS {quote_ident(name)}")
            self._run(f"DELETE FROM {_CATALOG} WHERE name = ?", [name])

    def update_meta(self, name: str, **meta: Any) -> TableInfo:
        with self._lock:
            info = self.require(name)
            for key, value in meta.items():
                if hasattr(info, key) and key not in {"name", "revision", "probe_memory"}:
                    setattr(info, key, value)
            self._save_info(info)
        return info

    def save_probe_memory(self, name: str, memory: dict[str, Any], *, expected_revision: str) -> bool:
        """仅将探测写回同一内容版本，防止分析副本覆盖已经更新的原表记忆。"""
        with self._lock:
            info = self.info(name)
            if info is None or info.revision != expected_revision:
                return False
            info.probe_memory = memory
            self._save_info(info)
            return True

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def query(self, sql: str, limit: int | None = None) -> pd.DataFrame:
        """执行一条只读 SELECT（用户 / AI 提供的 SQL 走这里）。"""
        text = assert_single_select(sql)
        if limit is not None:
            text = f"SELECT * FROM ({text}) AS __sm_q LIMIT {int(limit)}"
        with self._lock:
            return self._run(text, as_df=True)

    def fetch(self, internal_sql: str, params: list[Any] | None = None) -> list[tuple]:
        """执行引擎内部的只读 SQL，返回元组列表。"""
        with self._lock:
            return self._run(internal_sql, params)

    def fetch_df(self, internal_sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self._run(internal_sql, params, as_df=True)

    def df(self, name: str, limit: int | None = None) -> pd.DataFrame:
        """整张表（或前 limit 行），保持原始行顺序。"""
        self.require(name)
        sql = f"SELECT * FROM {quote_ident(name)} ORDER BY rowid"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.fetch_df(sql)

    def page(self, name: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """分页读取（前端表格查看）。"""
        info = self.require(name)
        part = self.fetch_df(
            f"SELECT * FROM {quote_ident(name)} ORDER BY rowid LIMIT {int(limit)} OFFSET {int(offset)}"
        )
        return {
            "name": info.name,
            "label": info.label,
            "total": info.rows,
            "offset": offset,
            "limit": limit,
            "columns": info.columns,
            "rows": frame_rows(part),
        }

    def columns(self, name: str) -> list[dict[str, str]]:
        rows = self.fetch(f"DESCRIBE {quote_ident(name)}")
        return [{"name": r[0], "type": duck_type_label(r[1])} for r in rows]

    def row_count(self, name: str) -> int:
        return int(self.fetch(f"SELECT count(*) FROM {quote_ident(name)}")[0][0])

    def info(self, name: str) -> TableInfo | None:
        with self._lock:
            rows = self.fetch(f"SELECT meta FROM {_CATALOG} WHERE name = ?", [name])
            if not rows or not self.exists(name):
                return None
            return self._decode_info(rows[0][0])

    def list_tables(self) -> list[TableInfo]:
        with self._lock:
            rows = self.fetch(f"SELECT name, meta FROM {_CATALOG}")
            existing = {
                r[0] for r in self.fetch("SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'")
            }
            infos = [self._decode_info(meta) for name, meta in rows if name in existing]
            infos.sort(key=lambda t: t.created_at)
            return infos

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _register(self, name: str, **meta: Any) -> TableInfo:
        """登记表信息。每次重建表都重新登记（不继承旧表的来源信息，避免张冠李戴）。"""
        info = TableInfo(name=name, revision=uuid4().hex)
        for key, value in meta.items():
            if hasattr(info, key) and value is not None and key not in {"name", "revision", "probe_memory"}:
                setattr(info, key, value)
        if not info.label:
            info.label = name
        info.columns = self.columns(name)
        info.rows = self.row_count(name)
        self._save_info(info)
        return info

    def _decode_info(self, meta: str) -> TableInfo:
        info = TableInfo.from_dict(json.loads(meta))
        if not info.revision:
            # 旧工作区无须迁移数据库；第一次访问时补齐稳定、持久的内容版本。
            info.revision = uuid4().hex
            info.probe_memory = {}
            self._save_info(info)
        return info

    def _save_info(self, info: TableInfo) -> None:
        self._run(
            f"INSERT OR REPLACE INTO {_CATALOG} VALUES (?, ?)",
            [info.name, json.dumps(info.to_dict(), ensure_ascii=False, default=str)],
        )


def _friendly_error(exc: duckdb.Error) -> str:
    """把 DuckDB 报错整理成一句话（去掉 LINE 指示等噪音），方便 AI 修复。"""
    text = str(exc).strip()
    for marker in ("\nLINE ", "\n\nLINE "):
        if marker in text:
            text = text.split(marker)[0]
    return text.replace("\n", " ")[:600]


class WorkspaceManager:
    """缓存打开的工作区（DuckDB 文件同一进程只能打开一次）。"""

    def __init__(self, root: Path, max_open: int = 24):
        self.root = root
        self.max_open = max_open
        self._open: OrderedDict[str, Workspace] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Workspace:
        with self._lock:
            ws = self._open.get(key)
            if ws is not None:
                self._open.move_to_end(key)
                return ws
            ws = Workspace(self.root / f"{key}.duckdb")
            self._open[key] = ws
            while len(self._open) > self.max_open:
                _, old = self._open.popitem(last=False)
                old.close()
            return ws

    def existing_tables(self, key: str) -> list[TableInfo]:
        """只读已有工作区的表目录，不创建空库，也不挤掉正在使用的缓存连接。"""
        with self._lock:
            ws = self._open.get(key)
            if ws is not None:
                return ws.list_tables()
            path = self.root / f"{key}.duckdb"
            if not path.exists():
                return []
            historical = Workspace(path)
            try:
                return historical.list_tables()
            finally:
                historical.close()

    def delete(self, key: str) -> None:
        with self._lock:
            ws = self._open.pop(key, None)
            if ws is not None:
                ws.close()
            for suffix in (".duckdb", ".duckdb.wal"):
                path = self.root / f"{key}{suffix}"
                if path.exists():
                    path.unlink()

    def close_all(self) -> None:
        with self._lock:
            for ws in self._open.values():
                ws.close()
            self._open.clear()
