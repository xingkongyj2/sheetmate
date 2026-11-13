"""整理已保存流程里的表名：去掉「依赖_哈希_」前缀和序号，只有真的重名才加数字。

在项目根目录运行：
    backend/.venv/bin/python backend/scripts/clean_recipe_table_names.py            # 只预览，不改任何数据
    backend/.venv/bin/python backend/scripts/clean_recipe_table_names.py --apply    # 实际整理

--apply 会：
  - 先把数据库备份成 data/app.db.bak-<时间>；
  - 每个需要整理的流程另存为一个新版本（备注「规范表名」），历史版本和历史运行记录都不动；
  - 改名后跑一遍结构检查，引用对不上、脚本直接写了旧名字的流程会原样保留。
新生成的流程已经是干净的名字，这个脚本只用来处理以前保存的流程。

请先停止后端服务；服务运行时脚本会拒绝执行（确实要执行可加 --force）。
数据目录与后端一致：默认 backend/data，可用环境变量 SHEETMATE_DATA_DIR 指定。
"""

from __future__ import annotations

import argparse
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 支持从任意工作目录直接运行，确保可以导入项目代码。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sheetmate.core.config.settings import settings
from sheetmate.db.session import session_scope
from sheetmate.recipe.definition.compiler import clean_dependency_names, dependency_name_map
from sheetmate.services.workflows import service as recipe_service

NOTE = "规范表名：去掉「依赖_」前缀"


def _server_running() -> bool:
    host = "127.0.0.1" if settings.host in {"0.0.0.0", "::", ""} else settings.host
    try:
        with socket.create_connection((host, settings.port), timeout=0.5):
            return True
    except OSError:
        return False


def _backup() -> Path:
    source = settings.data_dir / "app.db"
    target = source.with_name(f"app.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
    with sqlite3.connect(source) as origin, sqlite3.connect(target) as copy:
        origin.backup(copy)  # 即使开着 WAL 也能得到一致的副本
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="实际整理（默认只预览）")
    parser.add_argument("--force", action="store_true", help="后端服务正在运行时也继续")
    args = parser.parse_args()
    if _server_running() and not args.force:
        print(f"后端服务正在 {settings.host}:{settings.port} 运行。请先停止，或加 --force。")
        return 1

    backup = _backup() if args.apply else None
    if backup:
        print(f"已备份数据库：{backup}\n")
    changed = skipped = 0
    with session_scope() as db:
        for record in recipe_service.latest_records(db):
            recipe = recipe_service.record_to_recipe(record)
            names = dependency_name_map(recipe)
            if not names:
                continue
            title = f"「{record.name}」v{record.version}（{record.recipe_id}）"
            library = recipe_service.library_for(db, record.customer_id)
            cleaned = clean_dependency_names(recipe, library)
            if cleaned is recipe:
                skipped += 1
                print(f"跳过 {title}：改名会影响脚本或引用，保持原样")
                continue
            changed += 1
            print(f"{'整理' if args.apply else '将整理'} {title}：")
            for old, new in names.items():
                print(f"    {old}  →  {new}")
            if args.apply:
                cleaned.metadata["version_note"] = NOTE
                saved = recipe_service.save_recipe(db, cleaned)
                print(f"    已保存为 v{saved.version}")
    print(f"\n{'已整理' if args.apply else '可整理'} {changed} 个流程，跳过 {skipped} 个。")
    if changed and not args.apply:
        print("确认无误后加 --apply 实际整理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
