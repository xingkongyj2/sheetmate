"""令牌库：真实值 ↔ 令牌，全部留在本机。

- 令牌由本机密钥算出：同一个值永远得到同一个令牌（AI 仍能分组、去重、关联），
  但没有密钥就算不出、也猜不出令牌对应的真实值（手机号只有十亿种，不加密钥一下就能穷举）。
- 对应关系存在数据目录下的 vault.db（标准库 sqlite3），还原只在本机进行。
- 密钥第一次使用时自动生成，保存在同一目录的 privacy.key（仅本机用户可读）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

from sheetmate.privacy.rules import KIND_LABELS, KIND_PREFIX, TOKEN_LABELS

MIN_KNOWN_CHARS = 4
MAX_TOKEN_ATTEMPTS = 64
TOKEN_BODY = 13  # 8 字节 → 13 个字母数字，碰撞概率可以忽略
PREFIXES = tuple(dict.fromkeys(KIND_PREFIX.values()))          # 按前缀表生成：以后加了新的类型，这里自动认得
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(%s)_([A-Z2-7]{%d})(?![A-Za-z0-9_])" % ("|".join(PREFIXES), TOKEN_BODY))
_EXAMPLE_BODY = "K7Q2MX4PZ3WRDB5HTN6YC"[:TOKEN_BODY]               # 示例用的令牌主体：格式对，但不对应任何真实值


def token_legend() -> str:
    """告诉模型「这种样子的内容是什么加密」：按现有的全部类型自动生成，再给一个示例。只说样子，不说怎么来的、怎么处理。"""
    meanings: dict[str, list[str]] = {}
    for kind, prefix in KIND_PREFIX.items():
        label = TOKEN_LABELS.get(kind) or KIND_LABELS.get(kind, kind)
        if label not in meanings.setdefault(prefix, []):
            meanings[prefix].append(label)
    lines = [f"- {prefix}_ 开头：加密后的{'、'.join(labels)}" for prefix, labels in meanings.items()]
    first = next(iter(meanings))
    example = f"{first}_{_EXAMPLE_BODY}"
    return ("【加密内容说明】数据里有些内容是加密后的样子：大写前缀 + 下划线 + "
            f"{TOKEN_BODY} 位大写字母或数字。\n" + "\n".join(lines)
            + f"\n示例：{example} 是一个加密后的{'或'.join(meanings[first])}。")


def normalize(kind: str, value: object) -> str:
    """令牌按「原样的文字」区分：同一个值永远得到同一个令牌，写法不同就是不同的值。

    刻意不做归一化（不把 138-0013-8000 和 13800138000 合成一个）：AI 写的 SQL 会在真实数据上精确匹配，
    令牌还原回去必须正好是数据里的那个写法，否则会匹配不到行；合并写法也会让还原时丢掉原来的格式。"""
    return str(value).strip()


class Vault:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # 令牌库里存着真实值，目录只允许本机当前用户进入；SQLite 的 -wal/-shm 文件也随目录一起受保护
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        self.directory = directory
        self._lock = threading.RLock()
        database = directory / "vault.db"
        if not database.exists():
            os.close(os.open(database, os.O_WRONLY | os.O_CREAT, 0o600))   # 新建时就是 600，不留「先宽后紧」的空档
        self._db = sqlite3.connect(database, timeout=30.0, check_same_thread=False, isolation_level=None)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._db.execute("PRAGMA synchronous=NORMAL")   # WAL 下的推荐设置：每个新令牌不用等磁盘同步，大文件脱敏快很多
        except sqlite3.DatabaseError:
            pass
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, prefix TEXT NOT NULL, norm TEXT NOT NULL, "
            "value TEXT NOT NULL, created REAL NOT NULL)"
        )
        # 同一个（类型, 原样的值）只能有一个令牌；也让「重启后按值找回令牌」走索引
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tokens_by_value ON tokens (prefix, norm)")
        # 已识别为敏感的列里出现过的真实值：不管它以什么格式出现在发给模型的文字里，都要替换（见 Guard._scan_known）
        self._db.execute("CREATE TABLE IF NOT EXISTS known (value TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        # 「通用」是用户手动指定的整列，里面可能是数字、编号、几个字的词：不能登记成全局的已知值，不然这些值出现在别的表里也会被换成令牌（旧版本登记过的清掉）
        self._db.execute("DELETE FROM known WHERE kind = 'custom'")
        # 脱敏模型逐个检查过的单元格：原文 → 里面的敏感片段 [[片段, 类型], …]；没有敏感片段的也记一行（表示「检查过了，没有」，
        # 这样中断了可以接着做，同样的文字在别的文件里出现也不用再问模型）
        self._db.execute("CREATE TABLE IF NOT EXISTS cells (cell TEXT PRIMARY KEY, items TEXT NOT NULL)")
        # 用户在对话里打的字里，脱敏模型摘出的敏感片段（这些片段以后出现在发给 AI 的任何文字里，都要替换）
        try:
            has_data = bool(self._db.execute("SELECT EXISTS(SELECT 1 FROM tokens) OR EXISTS(SELECT 1 FROM known)").fetchone()[0])
            self._key = self._load_key(directory / "privacy.key", has_data=has_data)
        except BaseException:
            self._db.close()
            raise
        for path in (database, database.with_name("vault.db-wal"), database.with_name("vault.db-shm")):
            try:
                os.chmod(path, 0o600)   # 旧版本留下的宽权限文件也一并收紧
            except OSError:
                pass
        self._values: dict[str, str] = {}
        self._tokens: dict[tuple[str, str], str] = {}
        self._known: dict[str, str] | None = None
        self._cell_hits: dict[str, list[tuple[str, str]]] | None = None      # 只放「有敏感片段」的单元格
        self.known_version = 0

    # ---------------------------------------------------------------- 脱敏模型逐个检查的结果

    def cells_done(self, cells: Iterable[str]) -> set[str]:
        """这些单元格里，哪些已经检查过了（不管有没有发现敏感片段）。"""
        pending, done = list(dict.fromkeys(cells)), set()
        with self._lock:
            for start in range(0, len(pending), 500):
                chunk = pending[start:start + 500]
                marks = ",".join("?" * len(chunk))
                done.update(row[0] for row in self._db.execute(f"SELECT cell FROM cells WHERE cell IN ({marks})", chunk))
        return done

    def cells_put(self, rows: Iterable[tuple[str, list[tuple[str, str]]]]) -> int:
        """记下一批单元格的检查结果，返回其中有敏感片段的有几个。"""
        rows = list(rows)            # 传进来的可能是一次性的迭代器（zip），下面要遍历两次
        batch = [(cell, json.dumps([list(item) for item in items], ensure_ascii=False)) for cell, items in rows]
        if not batch:
            return 0
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.executemany("INSERT OR REPLACE INTO cells (cell, items) VALUES (?, ?)", batch)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            hits = self.cell_hits()
            found = 0
            for cell, items in rows:
                if items:
                    hits[cell] = [tuple(item) for item in items]
                    found += 1
            if found:
                self.known_version += 1          # 处理结果的缓存带着它：有了新发现，旧缓存就不能再用
            return found

    def cell_hits(self) -> dict[str, list[tuple[str, str]]]:
        with self._lock:
            if self._cell_hits is None:
                self._cell_hits = {cell: [tuple(item) for item in json.loads(items)]
                                   for cell, items in self._db.execute("SELECT cell, items FROM cells WHERE items != '[]'")}
            return self._cell_hits

    @staticmethod
    def _load_key(path: Path, *, has_data: bool) -> bytes:
        """密钥只在「全新安装」时才生成。损坏、或丢了但令牌库里已有数据，都报错拒服：
        悄悄换一把新钥会让同一个值从此得到不同的令牌，跨轮分组和关联就悄悄失效了。"""
        recover = f"如果确认不需要还原历史对话里的令牌，可以删除 {path.parent} 这个文件夹后重启，系统会重新生成。"
        if path.exists():
            key = path.read_bytes()
            if len(key) < 32:
                raise RuntimeError(f"脱敏密钥文件 {path.name} 已损坏（长度不足）。{recover}")
            return key
        if has_data:
            raise RuntimeError(f"脱敏密钥文件 {path.name} 不见了，但令牌库里已经有数据，不能悄悄换新密钥。"
                               f"请从备份恢复它。{recover}")
        key = secrets.token_bytes(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    def _token_for(self, prefix: str, norm: str, attempt: int = 0) -> str:
        material = f"{prefix}\0{norm}" if attempt == 0 else f"{prefix}\0{norm}\0{attempt}"   # 第一次不变，旧令牌全部有效
        digest = hmac.new(self._key, material.encode("utf-8"), hashlib.sha256).digest()[:8]
        return f"{prefix}_{base64.b32encode(digest).decode('ascii').rstrip('=')[:TOKEN_BODY]}"

    def token(self, kind: str, value: object) -> str:
        """把真实值换成令牌，并记住对应关系。

        不同的值永远不共用令牌：令牌只有 64 位，万一两个不同的值算出同一个，后来的那个换一个盐重新算，
        而不是悄悄复用旧令牌（那样还原时会把别人的真实值写进结果文件）。"""
        prefix = KIND_PREFIX[kind]
        norm = normalize(kind, value)
        with self._lock:
            known = self._tokens.get((prefix, norm))
            if known is not None:
                return known
            row = self._db.execute("SELECT token FROM tokens WHERE prefix = ? AND norm = ?", (prefix, norm)).fetchone()
            if row is not None:                       # 重启之后：库里已经有这个值的令牌，沿用
                self._tokens[(prefix, norm)] = row[0]
                return row[0]
            for attempt in range(MAX_TOKEN_ATTEMPTS):
                token = self._token_for(prefix, norm, attempt)
                try:
                    self._db.execute("INSERT INTO tokens (token, prefix, norm, value, created) VALUES (?, ?, ?, ?, ?)",
                                     (token, prefix, norm, str(value).strip(), time.time()))
                except sqlite3.IntegrityError:        # 这个令牌已经属于别的值：换个盐再试
                    continue
                self._tokens[(prefix, norm)] = token
                return token
            raise RuntimeError("令牌库无法为这个值生成不冲突的令牌")

    def reveal(self, token: str) -> str | None:
        """令牌 → 真实值；不认识的令牌返回 None（原样保留，不报错）。"""
        with self._lock:
            cached = self._values.get(token)
            if cached is not None:
                return cached
            row = self._db.execute("SELECT value FROM tokens WHERE token = ?", (token,)).fetchone()
            if row is None:
                return None
            self._values[token] = row[0]
            return row[0]

    def remember(self, kind: str, values: Iterable[object]) -> int:
        """登记敏感列里的真实值，返回新增了多少个。太短的值（几个字）不登记，避免把普通词语也替换掉。"""
        rows = {(str(value).strip(), kind) for value in values if len(str(value).strip()) >= MIN_KNOWN_CHARS}
        if not rows or kind == "custom":
            return 0
        with self._lock:
            known = self._known_values()
            fresh = [row for row in rows if row[0] not in known]
            if fresh:
                # 一批值放进同一个事务：连接是自动提交模式，不这样写的话每个值都是一次独立提交，10 万个值要 3 秒多
                self._db.execute("BEGIN")
                try:
                    self._db.executemany("INSERT OR IGNORE INTO known (value, kind) VALUES (?, ?)", fresh)
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                known.update(dict(fresh))
                self.known_version += 1
            return len(fresh)

    def forget(self, values: Iterable[object]) -> int:
        """把这些值从已知敏感值里去掉（用户在维护页里确认这一列不需要脱敏时用）。返回去掉了多少个。令牌记录不动。"""
        texts = {str(value).strip() for value in values}
        with self._lock:
            known = self._known_values()
            gone = [text for text in texts if text in known]
            if gone:
                self._db.execute("BEGIN")
                try:
                    self._db.executemany("DELETE FROM known WHERE value = ?", [(text,) for text in gone])
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                for text in gone:
                    known.pop(text, None)
                self.known_version += 1
            return len(gone)

    def _known_values(self) -> dict[str, str]:
        if self._known is None:
            self._known = dict(self._db.execute("SELECT value, kind FROM known").fetchall())
        return self._known

    def known(self) -> dict[str, str]:
        with self._lock:
            return self._known_values()

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()
