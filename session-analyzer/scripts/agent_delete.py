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


def delete_claude_sessions(project_dirname: str, sids: list, mode: str, *, remove_empty_dir=False) -> dict:
    root = HOME / ".claude"
    removed, errors = [], []
    for sid in sids:
        for p in _claude_session_paths(root, project_dirname, sid):
            try:
                remove_path(p, mode, removed)
            except Exception as e:
                errors.append(f"{p}: {e}")
    history_rows = _rewrite_jsonl(root / "history.jsonl", set(sids), ("sessionId",))
    if remove_empty_dir:
        pdir = root / "projects" / project_dirname
        if pdir.exists():
            try:
                remove_path(pdir, mode, removed)
            except Exception as e:
                errors.append(f"{pdir}: {e}")
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
