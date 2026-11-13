"""一键清空所有客户和全部对话（不可恢复，直接执行，不会再询问确认）。

在项目根目录运行：
    backend/.venv/bin/python backend/scripts/clear_customers_and_conversations.py
也可以在 PyCharm 中直接运行本文件。

会清掉：
  数据库  客户、对话、消息、Agent 执行记录、文件记录、流程（含历史版本）、公式、
          运行记录、客户 / 对话范围的记忆与字段映射、客户负责关系
  磁盘    uploads、outputs、workspaces、runs、log 目录里的全部内容，
          以及 scripts 目录里 AI 生成的脚本（scr_*）

会保留：
  用户账号、模型设置（settings.json）、secret.key、用户级记忆、*.bak-* 备份文件

注意：
  - 请先停止后端服务；服务运行时脚本会拒绝执行（确实要执行可加 --force）。
  - 数据目录与后端一致：默认 backend/data，可用环境变量 SHEETMATE_DATA_DIR 指定。
  - 启动后端时若 SEED_DEMO=1 且示例客户不存在，会重新生成「顺达物流（示例）」。
"""

from __future__ import annotations

import shutil
import socket
import sys
from pathlib import Path

# 支持从任意工作目录直接运行，确保可以导入项目代码。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, inspect, text

from sheetmate.core.config.settings import settings
from sheetmate.db.models import (
    AgentExecution,
    AgentToolOutput,
    AgentToolReceipt,
    Conversation,
    Customer,
    FieldMapping,
    FileConversation,
    FileCustomer,
    FileRecipe,
    FileRecord,
    FormulaRecord,
    Memory,
    Message,
    RecipeFieldMapping,
    RecipeProfile,
    RecipeRecord,
    Run,
    UserCustomer,
)
from sheetmate.db.session import engine


def _server_running() -> bool:
    """后端服务是否正在监听配置的端口。"""
    host = "127.0.0.1" if settings.host in {"0.0.0.0", "::", ""} else settings.host
    try:
        with socket.create_connection((host, settings.port), timeout=0.5):
            return True
    except OSError:
        return False


def _clear_database() -> dict[str, int]:
    """按外键依赖从子表到父表删除，返回各表删除的行数。"""
    # (表名, 删除条件)；条件为 None 表示整表清空。
    targets = [
        (AgentToolReceipt, None),
        (AgentToolOutput, None),
        (AgentExecution, None),
        (Message, None),
        (FileRecipe, None),
        (FileConversation, None),
        (FileCustomer, None),
        (FileRecord, None),
        (Run, None),
        (RecipeRecord, None),
        (RecipeProfile, None),
        (FormulaRecord, None),
        (FieldMapping, None),
        (RecipeFieldMapping, None),
        # 用户级记忆（既没有客户也没有对话）保留。
        (Memory, (Memory.customer_id.is_not(None)) | (Memory.conversation_id.is_not(None))),
        (Conversation, None),
        (UserCustomer, None),
        (Customer, None),
    ]
    counts: dict[str, int] = {}
    existing = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for model, condition in targets:
            table = model.__table__
            if table.name not in existing:
                continue
            statement = delete(table) if condition is None else delete(table).where(condition)
            counts[table.name] = conn.execute(statement).rowcount
    # 回收空间：数据库文件不会因为删行自动变小。
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.execute(text("VACUUM"))
    return counts


def _clear_directory(path: Path) -> int:
    """清空目录里的全部内容，目录本身保留；返回删除的条目数。"""
    if not path.is_dir():
        return 0
    count = 0
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        else:
            shutil.rmtree(child, ignore_errors=True)
        count += 1
    return count


def _clear_scripts() -> int:
    """删除全部 AI 生成的脚本（scr_*.json / scr_*.py）。

    脚本只被消息、流程和运行记录引用，这些都已清空，留下来只是无人引用的垃圾文件。
    """
    folder = settings.data_dir / "scripts"
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("scr_*"):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def main() -> int:
    force = "--force" in sys.argv[1:]
    if _server_running() and not force:
        print(f"检测到后端服务正在运行（{settings.host}:{settings.port}）。请先停止服务再执行，避免数据库和工作区被占用。")
        print("确认服务没有在使用这份数据时，可加 --force 强制执行。")
        return 1

    print(f"数据目录：{settings.data_dir}")
    if settings.db_path.is_file():
        counts = _clear_database()
        removed = "，".join(f"{name} {n}" for name, n in counts.items() if n)
        print(f"数据库已清空：{removed}" if removed else "数据库里没有客户或对话数据。")
    else:
        print(f"未找到数据库 {settings.db_path}，跳过数据库清理。")

    for label, path in (("上传文件", settings.uploads_dir), ("输出文件", settings.outputs_dir),
                        ("会话工作区", settings.workspaces_dir), ("运行工作区", settings.runs_dir),
                        ("对话日志", settings.logs_dir)):
        print(f"{label}：清理 {_clear_directory(path)} 项（{path.name}/）")
    print(f"脚本文件：清理 {_clear_scripts()} 个（scripts/）")

    print("完成：客户和全部对话已清空。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已取消。")
        raise SystemExit(130)
