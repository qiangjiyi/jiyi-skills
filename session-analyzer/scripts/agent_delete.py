#!/usr/bin/env python3
"""Per-agent deletion handlers for session-analyzer.

Each handler reproduces the *removal set* of the original tool so the new skill
stays behaviorally equivalent to:
  - codex-archived-thread-cleaner/scripts/clean_codex_archived_threads.py
  - tools/antigravity-gc.sh
  - tools/claude-session-cleanup.sh

The originals hard-`rm`; here filesystem artifacts default to Trash (reversible)
via mode="trash". SQLite rows and jsonl index lines are inherently hard-removed
(you cannot trash a DB row) — callers surface this in the UI.

All handlers are driven by explicit ids supplied by the server, which only
accepts ids present in the read-only scan. Handlers never touch anything outside
the agent's own data dir.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agyhub_summaries  # noqa: E402  (sibling module, needs path bootstrap above)

HOME = Path.home()


# ─────────────────────────── trash / hard delete ───────────────────────────

def move_to_trash(path: Path) -> None:
    if sys.platform == "darwin":
        script = 'tell application "Finder" to delete (POSIX file %s as alias)' % json.dumps(str(path))
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode == 0:
            return
        dest = HOME / ".Trash" / (path.name + "." + time.strftime("%H%M%S"))
        shutil.move(str(path), str(dest))
    elif sys.platform.startswith("win"):
        _trash_windows(path)
    else:
        raise OSError("移到废纸篓仅支持 macOS / Windows")


def _trash_windows(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = str(path.resolve()) + "\x00\x00"
    op.fFlags = 0x0040 | 0x0010 | 0x0004  # ALLOWUNDO | NOCONFIRMATION | SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        raise OSError("SHFileOperation failed (code %d)" % rc)


def hard_delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def remove_path(path: Path, mode: str, removed: list) -> None:
    """Remove one path (trash or hard). Missing path is a no-op success."""
    if not path.exists() and not path.is_symlink():
        return
    if mode == "trash":
        move_to_trash(path)
    else:
        hard_delete(path)
    removed.append(str(path))


def _rewrite_jsonl(path: Path, drop_ids: set, id_keys: tuple) -> int:
    """Drop lines whose any id_key value is in drop_ids. Returns removed count."""
    if not path.exists() or not drop_ids:
        return 0
    kept, removed = [], 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if any(obj.get(k) in drop_ids for k in id_keys):
                removed += 1
            else:
                kept.append(line)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.writelines(kept)
    os.replace(tmp, path)
    return removed


# ─────────────────────── empty-dir / orphan pruning ───────────────────────
# 删会话只删「会话本体那一份」，但 Claude 逐会话删不收空了的 projects/<dir>、
# Codex 删 rollout 后留空日期目录、各 Agent 历史删除也遗留过空壳/孤儿——这里统一收。

# 判空时忽略的系统垃圾文件：否则只剩 .DS_Store 的目录会被当成非空，"扫了还删不干净"。
_JUNK_NAMES = {".DS_Store", "Thumbs.db", ".localized"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_PID_RE = re.compile(r"^\d+$")


def prune_roots() -> dict:
    """各 Agent「会残留空子目录」的清理根。清理只在这些子树内自底向上进行，绝不删
    根本身，也绝不越界——空目录有时是程序占位，范围必须收死。"""
    cl, ag, cx = HOME / ".claude", HOME / ".gemini" / "antigravity", HOME / ".codex"
    return {
        "claude": [cl / "projects", cl / "session-env", cl / "file-history", cl / "tasks"],
        "antigravity": [ag / "brain", ag / "conversations", ag / "annotations"],
        "codex": [cx / "sessions", cx / "generated_images"],
    }


def _dir_empty(d: Path) -> bool:
    try:
        return all(c.name in _JUNK_NAMES for c in d.iterdir())
    except OSError:
        return False


def prune_empty_dirs(roots, mode: str, removed: list, keep_names=None) -> None:
    """自底向上清掉 roots 下的空目录（含只剩 .DS_Store 的），删到 root 为止，不删 root。
    keep_names 里的目录名即使为空也保留——护住活跃会话的卫星目录（session-env/
    file-history/tasks 下以活跃 sid 命名的目录）和 Claude 的 memory 持久目录。否则空目录
    清理会无差别地把活跃会话恰好为空的卫星目录一并收掉，绕过 _claude_live_sids() 保护。"""
    keep = keep_names or set()
    for root in roots:
        if not root.is_dir():
            continue
        for cur, _d, _f in os.walk(root, topdown=False):  # 先到最深层，删完叶子父目录可能也空
            p = Path(cur)
            if p != root and p.name not in keep and _dir_empty(p):
                try:
                    remove_path(p, mode, removed)
                except Exception:
                    pass


def _claude_live_sids() -> set:
    """projects 下现存会话的 sid 全集（.jsonl 文件名 + 子目录名），作为孤儿判定基准。"""
    proj = HOME / ".claude" / "projects"
    live = set()
    if proj.is_dir():
        for pdir in proj.iterdir():
            if not pdir.is_dir():
                continue
            for f in pdir.glob("*.jsonl"):
                live.add(f.stem)
            for f in pdir.iterdir():
                if f.is_dir():
                    live.add(f.name)
    return live


def prune_claude_satellites(mode: str, removed: list) -> list:
    """清掉 session-env / file-history / tasks 里「对应会话已不在 projects」的卫星孤儿。
    只动 uuid 形态的名字，删走废纸篓（可逆）；返回被清掉的 sid。"""
    root = HOME / ".claude"
    if not (root / "projects").is_dir():
        return []
    live = _claude_live_sids()
    orphans = []
    for sub in ("session-env", "file-history", "tasks"):
        d = root / sub
        if not d.is_dir():
            continue
        for item in d.iterdir():
            if not _UUID_RE.match(item.name) or item.name in live:
                continue
            try:
                remove_path(item, mode, removed)
                orphans.append(item.name)
            except Exception:
                pass
    return orphans


def _proc_name(pid: int) -> str | None:
    """pid 对应进程名；进程不存在返回 ""；无法查询（如非类 Unix、ps 不可用）返回 None。"""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()  # 空串 = 进程不存在；ps 缺失时上面已返回 None


def prune_claude_session_state(mode: str, removed: list) -> list:
    """清掉 ~/.claude/sessions/<pid>.json 里「pid 进程已不存在、或 pid 被非 Claude 进程
    复用」的陈旧运行时状态文件——某个 Claude CLI 异常退出没清掉自己的状态文件时留下的残渣。
    活着的 claude 进程对应文件一律保留（含当前正在跑的会话自身）。无法查询进程名（非类
    Unix 环境）时保守保留、绝不误删。删走废纸篓（可逆）；返回被清掉的 <pid>.json 文件名。"""
    d = HOME / ".claude" / "sessions"
    if not d.is_dir():
        return []
    stale = []
    for item in d.iterdir():
        if not (item.is_file() and item.suffix == ".json" and _PID_RE.match(item.stem)):
            continue
        name = _proc_name(int(item.stem))
        if name is None:                        # 查不了进程名 → 保守保留
            continue
        if name and "claude" in name.lower():   # 活着的 claude 进程 → 保留
            continue
        try:                                    # 进程不存在(name=="")或被复用 → 孤儿
            remove_path(item, mode, removed)
            stale.append(item.name)
        except Exception:
            pass
    return stale


# ─────────────────────────── Claude Code ───────────────────────────

def _claude_session_paths(root: Path, project_dirname: str, sid: str) -> list:
    pdir = root / "projects" / project_dirname
    return [
        pdir / f"{sid}.jsonl",
        pdir / sid,
        root / "file-history" / sid,
        root / "session-env" / sid,
        root / "tasks" / sid,
    ]


def delete_claude_sessions(project_dirname: str, sids: list, mode: str) -> dict:
    root = HOME / ".claude"
    removed, errors = [], []
    for sid in sids:
        for p in _claude_session_paths(root, project_dirname, sid):
            try:
                remove_path(p, mode, removed)
            except Exception as e:
                errors.append(f"{p}: {e}")
    history_rows = _rewrite_jsonl(root / "history.jsonl", set(sids), ("sessionId",))
    # 收尾：删完后 projects/<dir>（及 session-env 等）里空了的目录一并收掉——逐会话删
    # 到最后一个时目录就空了，这正是空目录残留的根因，不再依赖调用方传 remove_empty_dir。
    prune_empty_dirs(prune_roots()["claude"], mode, removed, _claude_live_sids() | {"memory"})
    return {"removed": removed, "history_rows": history_rows, "errors": errors}


def delete_claude_orphan_dir(project_dirname: str, mode: str) -> dict:
    root = HOME / ".claude"
    removed, errors = [], []
    try:
        remove_path(root / "projects" / project_dirname, mode, removed)
    except Exception as e:
        errors.append(str(e))
    return {"removed": removed, "errors": errors}


# ─────────────────────────── Antigravity ───────────────────────────

def delete_antigravity_sessions(uuids: list, mode: str) -> dict:
    root = HOME / ".gemini" / "antigravity"
    removed, errors = [], []
    for uuid in uuids:
        for p in (
            root / "conversations" / f"{uuid}.pb",
            root / "annotations" / f"{uuid}.pbtxt",
            root / "brain" / uuid,
        ):
            try:
                remove_path(p, mode, removed)
            except Exception as e:
                errors.append(f"{p}: {e}")
    # 新版侧栏列表存在共享索引 agyhub_summaries_proto.pb 里，删卫星文件不会让条目消失，
    # 必须就地从索引剔除对应记录（单文件多会话共享，只能硬改、不能整文件入废纸篓）。
    index_pb = root / "agyhub_summaries_proto.pb"
    try:
        idx_removed = agyhub_summaries.remove_from_index(index_pb, set(uuids))
    except Exception as e:
        errors.append(f"{index_pb}: {e}")
        idx_removed = []
    prune_empty_dirs(prune_roots()["antigravity"], mode, removed)  # 收尾：清删空了的卫星目录
    return {"removed": removed, "errors": errors, "index_removed": idx_removed}


# ─────────────────────────── Codex ───────────────────────────

# (db filename, [(table, column), ...]) — ported from clean_codex_archived_threads.py
_CODEX_DB_TARGETS = [
    ("state_5.sqlite", [
        ("thread_spawn_edges", "parent_thread_id"),
        ("thread_spawn_edges", "child_thread_id"),
        ("agent_job_items", "assigned_thread_id"),
        ("threads", "id"),
    ]),
    ("logs_2.sqlite", [("logs", "thread_id")]),
    ("goals_1.sqlite", [("thread_goals", "thread_id")]),
    ("memories_1.sqlite", [("stage1_outputs", "thread_id")]),
    ("sqlite/codex-dev.db", [("automation_runs", "thread_id"), ("inbox_items", "thread_id")]),
]


def delete_codex_threads(ids: list, mode: str = "rm") -> dict:
    """Delete given Codex threads + related files + DB rows + jsonl index lines.

    Codex is hard-deleted in full (the `mode` arg is accepted for interface
    symmetry but ignored): a thread is defined by its state_5 DB row, so once the
    row + jsonl index are hard-removed the session can never be restored — moving
    the satellite files to Trash would only leave orphan files that recover
    nothing. This also matches the original clean_codex_archived_threads.py,
    which always rm'd. projectless ~/Documents/Codex workspaces are removed only
    when not shared by a non-target thread (same guard as the original).
    """
    file_mode = "rm"  # 见 docstring：Codex 文件级软删无意义，统一硬删
    root = HOME / ".codex"
    state_db = root / "state_5.sqlite"
    removed, errors, db_rows = [], [], {}
    ids = list(dict.fromkeys(ids))
    if not ids or not state_db.exists():
        return {"removed": removed, "errors": ["no codex state db" if not state_db.exists() else "no ids"], "db_rows": db_rows}

    con = sqlite3.connect(state_db)
    con.row_factory = sqlite3.Row
    try:
        qmarks = ",".join("?" * len(ids))
        targets = con.execute(
            f"SELECT id, cwd, rollout_path FROM threads WHERE id IN ({qmarks})", ids
        ).fetchall()
        target_ids = {t["id"] for t in targets}
        non_target_cwds = {
            os.path.realpath(os.path.expanduser(r["cwd"]))
            for r in con.execute("SELECT id, cwd FROM threads")
            if r["id"] not in target_ids and r["cwd"]
        }
    finally:
        con.close()

    # 1) file artifacts (hard, see docstring)
    docs_codex = (HOME / "Documents" / "Codex").resolve()
    planned_workspaces = set()
    for t in targets:
        tid = t["id"]
        if t["rollout_path"]:
            _safe_remove(Path(t["rollout_path"]).expanduser(), file_mode, removed, errors)
        _safe_remove(root / "generated_images" / tid, file_mode, removed, errors)
        _safe_remove(root / "browser" / "sessions" / f"{tid}.toml", file_mode, removed, errors)
        snaps = root / "shell_snapshots"
        if snaps.exists():
            for p in snaps.glob(f"{tid}.*.sh"):
                _safe_remove(p, file_mode, removed, errors)
        ws = _projectless_workspace(t["cwd"], docs_codex, non_target_cwds)
        if ws and str(ws) not in planned_workspaces:
            planned_workspaces.add(str(ws))
            _safe_remove(ws, file_mode, removed, errors)

    # 2) jsonl index lines (hard)
    jl1 = _rewrite_jsonl(root / "session_index.jsonl", target_ids, ("id",))
    jl2 = _rewrite_jsonl(root / "history.jsonl", target_ids, ("session_id", "id"))
    db_rows["session_index.jsonl"] = jl1
    db_rows["history.jsonl"] = jl2

    # 3) SQLite rows (hard)
    for db_name, table_cols in _CODEX_DB_TARGETS:
        db_path = root / db_name
        if not db_path.exists():
            continue
        c = sqlite3.connect(db_path)
        try:
            if db_name == "state_5.sqlite":
                c.execute("PRAGMA foreign_keys = ON")
            for table, col in table_cols:
                if not _table_exists(c, table):
                    continue
                qmarks = ",".join("?" * len(ids))
                cur = c.execute(f"DELETE FROM {table} WHERE {col} IN ({qmarks})", ids)
                db_rows[f"{db_name}:{table}.{col}"] = cur.rowcount
            c.commit()
        except Exception as e:
            errors.append(f"{db_name}: {e}")
        finally:
            c.close()
    # 收尾：删 rollout 后 sessions/YYYY/MM/DD 可能空，generated_images/<tid> 也已整删——
    # 自底向上收掉空日期目录（codex 文件级软删无意义，空目录同样硬删，与上文一致）。
    prune_empty_dirs(prune_roots()["codex"], file_mode, removed)
    return {"removed": removed, "errors": errors, "db_rows": db_rows}


def _safe_remove(path: Path, mode: str, removed: list, errors: list) -> None:
    try:
        remove_path(path, mode, removed)
    except Exception as e:
        errors.append(f"{path}: {e}")


def _projectless_workspace(cwd: str, docs_codex: Path, non_target_cwds: set) -> Path | None:
    if not cwd:
        return None
    p = Path(cwd).expanduser()
    try:
        resolved = p.resolve()
        rel = resolved.relative_to(docs_codex)
    except (ValueError, OSError):
        return None
    if str(resolved) in non_target_cwds:
        return None
    if not rel.parts or not p.is_dir():
        return None
    return p


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None
