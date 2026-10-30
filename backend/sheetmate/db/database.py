"""SQLite 连接与会话管理（SQLAlchemy 2.0）。

本地优先：单文件数据库 data/app.db，零部署；量大以后可以换 PostgreSQL，上层代码不用改。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from sheetmate.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


settings.ensure_dirs()
engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """启用外键约束 + WAL（读写并发更好）。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db() -> None:
    """建表（幂等），并给已有的表补上新版本新增的列（本地升级时不用手动迁移）。"""
    from sheetmate.db import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_missing_columns(conn)


def _add_missing_columns(conn) -> list[str]:
    """轻量迁移：模型里有、数据库里没有的列，用 ALTER TABLE ADD COLUMN 补上（SQLite 只支持加列，正好够用）。

    新列一律允许为空；模型里写了标量默认值的，同时作为数据库默认值，已有的行会得到这个值。
    """
    inspector = inspect(conn)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=conn.dialect)}'
            default = column.default.arg if column.default is not None and column.default.is_scalar else None
            if isinstance(default, bool):
                ddl += f" DEFAULT {int(default)}"
            elif isinstance(default, (int, float)):
                ddl += f" DEFAULT {default}"
            elif isinstance(default, str):
                ddl += " DEFAULT '" + default.replace("'", "''") + "'"
            conn.exec_driver_sql(ddl)
            added.append(f"{table.name}.{column.name}")
    return added


@contextmanager
def session_scope() -> Iterator[Session]:
    """服务层使用的事务上下文：成功提交，异常回滚。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
