#!/usr/bin/env python3
"""新增 Agent 插件（agents_*.py）共用的小工具：体积、只读 SQLite、JSON 原子改写、扫描结构。

只服务插件；scan.py 里原有四个 Agent 的实现保持不动。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import agent_registry
from agent_delete import remove_path  # noqa: F401  (re-export for plugins)

HOME = Path.home()
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
LIVE_WINDOW_MS = 10 * 60 * 1000
JUNK_NAMES = {".DS_Store", "Thumbs.db", ".localized"}


def dir_size(path: Path) -> int:
    """递归字节数，不跟随符号链接；不存在返回 0。"""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    total, stack = 0, [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def mtime_of(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def ro_connect(db: Path):
    """只读连接；mode=ro 失败回退 immutable=1；都失败返回 None。"""
    for uri in (f"file:{db}?mode=ro", f"file:{db}?mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return con
        except sqlite3.Error:
            continue
    return None


def table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def write_json_atomic(path: Path, obj, like_text: str | None = None) -> None:
    """同目录临时文件 + os.replace。尽量保持原文件缩进风格（紧凑 / 缩进 2）。"""
    indent = 2 if (like_text or "").lstrip().startswith(("{\n", "[\n")) else None
    seps = None if indent else (",", ":")
    text = json.dumps(obj, ensure_ascii=False, indent=indent, separators=seps)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def safe_remove(path: Path, mode: str, removed: list, errors: list) -> None:
    try:
        remove_path(path, mode, removed)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{path}: {e}")


def path_within(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
        return True
    except ValueError:
        return False


def session(sid, title, mtime, size, snippet="", **extra) -> dict:
    return {"id": sid, "title": title or "(无标题)", "snippet": snippet or "",
            "mtime": int(mtime or 0), "size": int(size or 0), "extra": extra}


def project(pid, label, real_path, orphan, sessions, **more) -> dict:
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    p = {"id": pid, "label": label, "real_path": real_path, "orphan": bool(orphan),
         "session_count": len(sessions), "size": sum(s["size"] for s in sessions),
         "sessions": sessions}
    p.update(more)
    return p


def new_agent(key: str, root: Path, note: str = "") -> dict:
    spec = agent_registry.BY_KEY[key]
    return {"key": key, "name": spec.name, "root": str(root),
            "installed": agent_registry.is_installed(spec), "note": note, "projects": []}


def finalize(agent: dict) -> dict:
    agent["projects"].sort(key=lambda p: p["size"], reverse=True)
    agent["project_count"] = len(agent["projects"])
    agent["session_count"] = sum(p["session_count"] for p in agent["projects"])
    agent["total_size"] = sum(p["size"] for p in agent["projects"])
    agent["orphan_session_count"] = sum(len(p["sessions"]) for p in agent["projects"] if p["orphan"])
    return agent


def snippet_from_text(text: str, limit: int = 160) -> str:
    text = re.sub(r"<[^>]{1,80}>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]
