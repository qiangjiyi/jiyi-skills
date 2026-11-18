#!/usr/bin/env python3
"""WorkBuddy（腾讯，CodeBuddy 内核）会话扫描与删除。

数据布局（~/.workbuddy，2026-11 实测）：
- 会话索引：workbuddy.db 的 sessions 行（cwd/title/状态），session_usage、
  session_turn_interruptions 按 session_id 关联。
- 对话正文：projects/<编码 cwd>/<sid>.jsonl，同目录 <sid>/（tool-results）、
  <sid>.file-rollback.ndjson、<sid>.meta.json。
- 逐会话卫星：file-history/<sid>/、artifact-index/<sid>.json（只删索引，不删它指向的产物
  文件——那些多半在用户自己的项目目录里）、file-tree-manifests/<sid>.json、workspace/sessions/<sid>/。
- UI 索引：<userId>/sidebar-list-snapshot.json 的 items、app/sessions.json 的 sessions、
  storage/**/conversationContextUsage.json 的 key。
- 临时工作目录：没选项目时 WorkBuddy 自动建 ~/WorkBuddy/<YYYY-MM-DD-HH-MM-SS>/，会话删除后
  若已无其他会话使用就一并清理，但其中 .workbuddy/memory/ 有内容时保留。
- 记忆：memory/、SOUL.md、USER.md、IDENTITY.md、BOOTSTRAP.md —— 删除链路永不进入。

运行门禁：WorkBuddy App 运行时会回写数据库和侧栏快照，删除前必须已退出。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import agent_common as c
import agent_registry

KEY = "workbuddy"
ROOT = c.HOME / ".workbuddy"
TEMP_WORKDIR_PARENT = c.HOME / "WorkBuddy"
TEMP_WORKDIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")
SATELLITE_DIRS = ("file-history", "workspace/sessions")
SATELLITE_FILES = ("artifact-index/{sid}.json", "file-tree-manifests/{sid}.json")
PROJECT_FILE_SUFFIXES = (".jsonl", ".file-rollback.ndjson", ".meta.json", "")


def is_temp_workdir(cwd: str | None) -> bool:
    if not cwd:
        return False
    p = Path(cwd)
    return p.parent == TEMP_WORKDIR_PARENT and bool(TEMP_WORKDIR_RE.match(p.name))


def _session_paths(root: Path, sid: str) -> list[Path]:
    out = []
    proj = root / "projects"
    if proj.is_dir():
        for enc in proj.iterdir():
            if enc.is_dir():
                out += [enc / (sid + suf) for suf in PROJECT_FILE_SUFFIXES]
    out += [root / d / sid for d in SATELLITE_DIRS]
    out += [root / f.format(sid=sid) for f in SATELLITE_FILES]
    return [p for p in out if p.exists() or p.is_symlink()]


def _jsonl_index(root: Path) -> dict[str, tuple[Path, str | None]]:
    """sid -> (jsonl 路径, 首条记录里的 cwd)。"""
    idx = {}
    proj = root / "projects"
    if not proj.is_dir():
        return idx
    for enc in proj.iterdir():
        if not enc.is_dir():
            continue
        for f in enc.glob("*.jsonl"):
            sid = f.stem
            if not c.UUID_RE.match(sid):
                continue
            cwd = None
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for _ in range(5):
                        line = fh.readline()
                        if not line:
                            break
                        try:
                            cwd = json.loads(line).get("cwd")
                        except (json.JSONDecodeError, AttributeError):
                            continue
                        if cwd:
                            break
            except OSError:
                pass
            idx[sid] = (f, cwd)
    return idx


def _db_rows(root: Path) -> tuple[dict[str, dict], int]:
    """本地会话行 + 云端会话行数（云端行只计数，不展示）。"""
    db = root / "workbuddy.db"
    if not db.exists():
        return {}, 0
    con = c.ro_connect(db)
    if con is None:
        return {}, 0
    rows, cloud = {}, 0
    try:
        if not c.table_exists(con, "sessions"):
            return {}, 0
        for r in con.execute("SELECT * FROM sessions"):
            r = dict(r)
            if (r.get("transport") or "local") != "local":
                cloud += 1
                continue
            rows[r["id"]] = r
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return rows, cloud


def scan(root: Path = ROOT) -> dict:
    agent = c.new_agent(KEY, root)
    if not root.exists():
        agent["installed"] = False
        return c.finalize(agent)
    rows, cloud = _db_rows(root)
    jsonl = _jsonl_index(root)
    now_ms = int(time.time() * 1000)
    by_cwd: dict[str, list] = {}
    for sid in sorted(set(rows) | set(jsonl)):
        if not c.UUID_RE.match(sid):
            continue
        r = rows.get(sid, {})
        jpath, jcwd = jsonl.get(sid, (None, None))
        cwd = r.get("cwd") or jcwd or ""
        size = sum(c.dir_size(p) for p in _session_paths(root, sid))
        last_ms = r.get("last_activity_at") or r.get("updated_at") or 0
        mtime = int(last_ms / 1000) if last_ms else (c.mtime_of(jpath) if jpath else 0)
        extra = {"cwd": cwd, "status": r.get("status"), "temp_workdir": is_temp_workdir(cwd)}
        if not r:
            extra["db_missing"] = True
        if last_ms and now_ms - last_ms < c.LIVE_WINDOW_MS:
            extra["live"] = True
        title = r.get("custom_title") or r.get("title") or (jpath.stem if jpath else sid)
        by_cwd.setdefault(cwd, []).append(c.session(sid, title, mtime, size, title, **extra))
    for cwd, sess in by_cwd.items():
        orphan = bool(cwd) and not Path(cwd).exists()
        label = cwd or "(未知工作目录)"
        if is_temp_workdir(cwd):
            label += "  ·  临时工作目录"
        agent["projects"].append(c.project(cwd or "__unknown__", label, cwd, orphan, sess))
    notes = ["删除前必须退出 WorkBuddy App", "记忆（memory/、SOUL.md 等）不会被删除"]
    if cloud:
        notes.append(f"另有 {cloud} 个云端会话未列出（在 App 内删除）")
    agent["note"] = "；".join(notes)
    return c.finalize(agent)


# ─────────────────────────── 删除 ───────────────────────────

def _memory_has_content(d: Path) -> bool:
    if not d.is_dir():
        return False
    for p in d.rglob("*"):
        if p.is_file() and p.name not in c.JUNK_NAMES:
            return True
    return False


def clean_temp_workdir(wd: Path, mode: str, removed: list, errors: list) -> None:
    """清理临时工作目录，保留非空的 .workbuddy/memory/。"""
    if not is_temp_workdir(str(wd)) or not wd.is_dir():
        return
    for child in list(wd.iterdir()):
        if child.name == ".workbuddy" and child.is_dir() and _memory_has_content(child / "memory"):
            for sub in list(child.iterdir()):
                if sub.name != "memory":
                    c.safe_remove(sub, mode, removed, errors)
            continue
        c.safe_remove(child, mode, removed, errors)
    try:
        if not any(p for p in wd.iterdir() if p.name not in c.JUNK_NAMES):
            c.safe_remove(wd, mode, removed, errors)
    except OSError:
        pass


def _prune_json_indexes(root: Path, sids: set, errors: list) -> dict:
    counts = {}

    def rewrite(path: Path, fn):
        try:
            text = path.read_text(encoding="utf-8")
            obj = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return
        n = fn(obj)
        if n:
            try:
                c.write_json_atomic(path, obj, text)
                counts[str(path.relative_to(root))] = n
            except OSError as e:
                errors.append(f"{path}: {e}")

    def drop_list(obj, list_key, id_key):
        items = obj.get(list_key) if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return 0
        keep = [i for i in items if not (isinstance(i, dict) and i.get(id_key) in sids)]
        n = len(items) - len(keep)
        obj[list_key] = keep
        return n

    def drop_keys(obj):
        if not isinstance(obj, dict):
            return 0
        hit = [k for k in obj if k in sids]
        for k in hit:
            del obj[k]
        return len(hit)

    for snap in root.glob("*/sidebar-list-snapshot.json"):
        rewrite(snap, lambda o: drop_list(o, "items", "id"))
    app_sessions = root / "app" / "sessions.json"
    if app_sessions.exists():
        rewrite(app_sessions, lambda o: drop_list(o, "sessions", "conversationId"))
    storage = root / "storage"
    if storage.is_dir():
        for f in storage.rglob("conversationContextUsage.json"):
            rewrite(f, drop_keys)
    return counts


def delete(ids: list, mode: str, root: Path = ROOT, gate: bool = True) -> dict:
    if gate:
        agent_registry.require_closed(KEY)
    sids = {s for s in ids if isinstance(s, str) and c.UUID_RE.match(s)}
    removed, errors, db_rows = [], [], {}
    if not sids:
        return {"removed": removed, "errors": errors, "db_rows": db_rows}

    rows, _ = _db_rows(root)
    jsonl = _jsonl_index(root)
    cwds = {s: (rows.get(s, {}).get("cwd") or jsonl.get(s, (None, None))[1]) for s in sids}

    for sid in sorted(sids):
        for p in _session_paths(root, sid):
            c.safe_remove(p, mode, removed, errors)

    db = root / "workbuddy.db"
    if db.exists():
        try:
            con = sqlite3.connect(db, timeout=3)
            try:
                q = ",".join("?" * len(sids))
                for table, col in (("session_turn_interruptions", "session_id"),
                                   ("session_usage", "session_id"), ("sessions", "id")):
                    if c.table_exists(con, table):
                        cur = con.execute(f"DELETE FROM {table} WHERE {col} IN ({q})", list(sids))
                        db_rows[f"workbuddy.db:{table}"] = cur.rowcount
                con.commit()
            finally:
                con.close()
        except sqlite3.Error as e:
            errors.append(f"{db}: {e}")

    db_rows.update(_prune_json_indexes(root, sids, errors))

    remaining, _ = _db_rows(root)
    still_used = {r.get("cwd") for r in remaining.values()}
    for cwd in {v for v in cwds.values() if v}:
        if is_temp_workdir(cwd) and cwd not in still_used:
            clean_temp_workdir(Path(cwd), mode, removed, errors)

    proj = root / "projects"
    if proj.is_dir():
        for enc in proj.iterdir():
            if enc.is_dir() and not any(p for p in enc.iterdir() if p.name not in c.JUNK_NAMES):
                c.safe_remove(enc, mode, removed, errors)
    return {"removed": removed, "errors": errors, "db_rows": db_rows}


def live_sids(root: Path = ROOT) -> set:
    rows, _ = _db_rows(root)
    return set(rows) | set(_jsonl_index(root))


def prune_orphans(mode: str, removed: list, root: Path = ROOT) -> list:
    """兜底：清掉对应会话已不存在的卫星（file-history / artifact-index / ...）。
    App 运行中或进程表不可用时跳过（返回空）。"""
    if not root.exists() or agent_registry.running_apps(KEY) != []:
        return []
    live = live_sids(root)
    if not live and not (root / "workbuddy.db").exists():
        return []
    hits = []
    for d in SATELLITE_DIRS:
        base = root / d
        if base.is_dir():
            hits += [p for p in base.iterdir() if c.UUID_RE.match(p.name) and p.name not in live]
    for pattern in SATELLITE_FILES:
        base = root / Path(pattern).parent
        if base.is_dir():
            hits += [p for p in base.glob("*.json") if c.UUID_RE.match(p.stem) and p.stem not in live]
    errors: list = []
    for p in hits:
        c.safe_remove(p, mode, removed, errors)
    return [str(p) for p in hits]
