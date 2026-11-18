#!/usr/bin/env python3
"""Cursor 会话扫描与删除（IDE Agent/Composer + cursor-agent CLI）。

数据布局（2026-11 实测）：
IDE（~/Library/Application Support/Cursor/User/globalStorage/）：
- state.vscdb：composerHeaders 表（侧栏列表：composerId/workspaceId/isArchived/isSubagent/value），
  cursorDiskKV 里按会话 id 命名的键：composerData:<cid>、bubbleId:<cid>:<bid>、
  checkpointId:<cid>:*、composerConvHeaders:<cid>:*、composerTranscriptHeightTail:<cid>、
  composerRootWriterDiagnostic:<cid>、ofsContent:<cid>:*、composerDraft:<cid>。
  agentKv:blob:<sha> 是跨会话共享的内容寻址块，引用关系不透明，不碰。
- conversation-search.db：会话搜索索引（conversations + conversation_fts + candidates）。
- workspaceStorage/<workspaceId>/workspace.json：工作区 → 文件夹映射。
CLI（~/.cursor/chats/<md5(cwd)>/<chatId>/{store.db,meta.json}）。
两者的对话记录：~/.cursor/projects/<编码 cwd>/agent-transcripts/<id>/。
记忆/配置（rules、skills-cursor、mcp.json、projects/*/mcp-auth.json）永不触碰。

运行门禁：IDE 运行时缓存会话并回写 state.vscdb → 删 IDE 会话前必须退出 Cursor；
CLI 会话最近 10 分钟有更新的拒绝删除（可能正在运行）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import agent_common as c
import agent_registry

KEY = "cursor"
DOT = c.HOME / ".cursor"
USER = agent_registry.APP_SUPPORT / "Cursor" / "User"
KV_PREFIXES = ("composerData", "bubbleId", "checkpointId", "composerConvHeaders",
               "composerTranscriptHeightTail", "composerRootWriterDiagnostic",
               "ofsContent", "composerDraft")


def _paths(dot: Path, user: Path) -> dict:
    gs = user / "globalStorage"
    return {"state": gs / "state.vscdb", "search": gs / "conversation-search.db",
            "ws": user / "workspaceStorage", "chats": dot / "chats", "projects": dot / "projects"}


def _transcript_dirs(projects: Path, sid: str) -> list[Path]:
    if not projects.is_dir():
        return []
    return [d for d in projects.glob(f"*/agent-transcripts/{sid}") if d.is_dir()]


def _first_user_text(tdir: Path) -> str:
    f = tdir / f"{tdir.name}.jsonl"
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                d = json.loads(line)
                if d.get("role") != "user":
                    continue
                for part in (d.get("message") or {}).get("content") or []:
                    if part.get("type") == "text":
                        t = part.get("text", "")
                        if "<user_query>" in t:
                            t = t.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
                        return c.snippet_from_text(t)
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return ""


def _workspace_folders(ws: Path) -> dict[str, str]:
    out = {}
    if not ws.is_dir():
        return out
    for d in ws.iterdir():
        try:
            folder = json.loads((d / "workspace.json").read_text()).get("folder") or ""
        except (OSError, json.JSONDecodeError):
            continue
        if folder.startswith("file://"):
            out[d.name] = unquote(urlparse(folder).path)
    return out


def _kv_sizes(con) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for key, n in con.execute("SELECT key, length(value) FROM cursorDiskKV"):
        parts = (key or "").split(":")
        if len(parts) >= 2 and parts[0] in KV_PREFIXES:
            sizes[parts[1]] = sizes.get(parts[1], 0) + (n or 0) + len(key)
    return sizes


def _scan_ide(p: dict) -> tuple[list, str]:
    if not p["state"].exists():
        return [], ""
    con = c.ro_connect(p["state"])
    if con is None:
        return [], "IDE 数据库被锁，IDE 会话未扫描"
    folders = _workspace_folders(p["ws"])
    by_ws: dict[str, list] = {}
    try:
        if not c.table_exists(con, "composerHeaders"):
            return [], "未找到 composerHeaders 表（Cursor 版本不兼容），IDE 会话未扫描"
        sizes = _kv_sizes(con)
        for r in con.execute("SELECT composerId, workspaceId, isArchived, isSubagent, "
                             "lastUpdatedAt, createdAt, value FROM composerHeaders"):
            cid = r["composerId"]
            if not c.UUID_RE.match(cid or "") or r["isSubagent"]:
                continue  # empty-state-draft 等占位；子代理随父会话删除
            try:
                v = json.loads(r["value"] or "{}")
            except json.JSONDecodeError:
                v = {}
            tdirs = _transcript_dirs(p["projects"], cid)
            size = sizes.get(cid, 0) + sum(c.dir_size(d) for d in tdirs)
            snippet = _first_user_text(tdirs[0]) if tdirs else ""
            title = v.get("name") or snippet or ("(草稿)" if v.get("isDraft") else "(无标题)")
            ts = (r["lastUpdatedAt"] or r["createdAt"] or 0) // 1000
            by_ws.setdefault(r["workspaceId"] or "empty-window", []).append(
                c.session(cid, title, ts, size, snippet, source="IDE",
                          archived=bool(r["isArchived"]), draft=bool(v.get("isDraft"))))
    except sqlite3.Error as e:
        return [], f"读取 IDE 数据库失败：{e.__class__.__name__}"
    finally:
        con.close()
    projects = []
    for wsid, sess in by_ws.items():
        folder = folders.get(wsid)
        if wsid == "empty-window":
            label, orphan = "(无工作区窗口)", False
        elif folder:
            label, orphan = folder, not Path(folder).exists()
        else:
            label, orphan = f"工作区 {wsid}", False
        projects.append(c.project(f"ide:{wsid}", f"IDE · {label}", folder, orphan, sess))
    return projects, ""


def _scan_cli(p: dict) -> list:
    chats = p["chats"]
    if not chats.is_dir():
        return []
    now_ms = int(time.time() * 1000)
    by_cwd: dict[str, list] = {}
    for meta in chats.glob("*/*/meta.json"):
        sid = meta.parent.name
        if not c.UUID_RE.match(sid):
            continue
        try:
            m = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            m = {}
        tdirs = _transcript_dirs(p["projects"], sid)
        size = c.dir_size(meta.parent) + sum(c.dir_size(d) for d in tdirs)
        snippet = _first_user_text(tdirs[0]) if tdirs else ""
        upd = m.get("updatedAtMs") or m.get("createdAtMs") or 0
        extra = {"source": "CLI"}
        if upd and now_ms - upd < c.LIVE_WINDOW_MS:
            extra["live"] = True
        cwd = m.get("cwd") or ""
        by_cwd.setdefault(cwd, []).append(
            c.session(sid, snippet or "(CLI 会话)", upd // 1000, size, snippet, **extra))
    return [c.project(f"cli:{cwd}", f"CLI · {cwd or '(未知目录)'}", cwd,
                      bool(cwd) and not Path(cwd).exists(), sess)
            for cwd, sess in by_cwd.items()]


def scan(dot: Path = DOT, user: Path = USER) -> dict:
    agent = c.new_agent(KEY, dot)
    p = _paths(dot, user)
    if not (dot.exists() or p["state"].exists()):
        agent["installed"] = False
        return c.finalize(agent)
    ide, warn = _scan_ide(p)
    agent["projects"] = ide + _scan_cli(p)
    notes = ["删除 IDE 会话前必须退出 Cursor（CLI 会话不需要）", "IDE 会话删除为数据库硬删、不可逆"]
    if warn:
        notes.insert(0, warn)
    agent["note"] = "；".join(notes)
    return c.finalize(agent)


# ─────────────────────────── 删除 ───────────────────────────

def _expand_subagents(con, cids: set) -> set:
    out, todo = set(cids), list(cids)
    while todo:
        cid = todo.pop()
        row = con.execute("SELECT value FROM cursorDiskKV WHERE key=?", (f"composerData:{cid}",)).fetchone()
        if not row or not row[0]:
            continue
        try:
            d = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            continue
        for k in ("subComposerIds", "subagentComposerIds"):
            for sub in d.get(k) or []:
                if isinstance(sub, str) and c.UUID_RE.match(sub) and sub not in out:
                    out.add(sub)
                    todo.append(sub)
    return out


def _delete_ide(cids: set, mode: str, p: dict, removed: list, errors: list, db_rows: dict, gate: bool):
    if gate:
        agent_registry.require_closed(KEY)
    con = sqlite3.connect(p["state"], timeout=3)
    try:
        cids = _expand_subagents(con, cids)
        kv = 0
        for cid in cids:
            for pre in KV_PREFIXES:
                kv += con.execute("DELETE FROM cursorDiskKV WHERE key=? OR key LIKE ?",
                                  (f"{pre}:{cid}", f"{pre}:{cid}:%")).rowcount
        q = ",".join("?" * len(cids))
        hdr = con.execute(f"DELETE FROM composerHeaders WHERE composerId IN ({q})", list(cids)).rowcount
        con.commit()
        db_rows["state.vscdb:cursorDiskKV"] = kv
        db_rows["state.vscdb:composerHeaders"] = hdr
    finally:
        con.close()
    if p["search"].exists():
        try:
            s = sqlite3.connect(p["search"], timeout=3)
            try:
                q = ",".join("?" * len(cids))
                if c.table_exists(s, "conversations"):
                    rowids = [r[0] for r in s.execute(
                        f"SELECT fts_rowid FROM conversations WHERE id IN ({q})", list(cids))]
                    if rowids:
                        s.execute(f"DELETE FROM conversation_fts WHERE rowid IN ({','.join('?' * len(rowids))})", rowids)
                    db_rows["conversation-search.db:conversations"] = s.execute(
                        f"DELETE FROM conversations WHERE id IN ({q})", list(cids)).rowcount
                if c.table_exists(s, "conversation_search_candidates"):
                    s.execute(f"DELETE FROM conversation_search_candidates WHERE id IN ({q})", list(cids))
                s.commit()
            finally:
                s.close()
        except sqlite3.Error as e:
            errors.append(f"conversation-search.db: {e}")
    for cid in cids:
        for d in _transcript_dirs(p["projects"], cid):
            c.safe_remove(d, mode, removed, errors)


def _delete_cli(sids: set, mode: str, p: dict, removed: list, errors: list):
    now_ms = int(time.time() * 1000)
    targets = []
    for sid in sids:
        for chat in p["chats"].glob(f"*/{sid}"):
            try:
                upd = json.loads((chat / "meta.json").read_text()).get("updatedAtMs") or 0
            except (OSError, json.JSONDecodeError):
                upd = 0
            if upd and now_ms - upd < c.LIVE_WINDOW_MS:
                raise ValueError(f"CLI 会话 {sid} 最近 10 分钟内仍有活动（可能正在运行），已拒绝删除")
            targets.append(chat)
    for chat in targets:
        c.safe_remove(chat, mode, removed, errors)
        parent = chat.parent
        if parent.is_dir() and not any(x for x in parent.iterdir() if x.name not in c.JUNK_NAMES):
            c.safe_remove(parent, mode, removed, errors)
    for sid in sids:
        for d in _transcript_dirs(p["projects"], sid):
            c.safe_remove(d, mode, removed, errors)


def delete(project_id: str, ids: list, mode: str, dot: Path = DOT, user: Path = USER,
           gate: bool = True) -> dict:
    p = _paths(dot, user)
    sids = {s for s in ids if isinstance(s, str) and c.UUID_RE.match(s)}
    removed, errors, db_rows = [], [], {}
    if sids:
        if project_id.startswith("ide:"):
            _delete_ide(sids, mode, p, removed, errors, db_rows, gate)
        elif project_id.startswith("cli:"):
            _delete_cli(sids, mode, p, removed, errors)
        else:
            raise ValueError("未知的 Cursor 项目类型")
        # 收掉清空了的 agent-transcripts / 项目壳（只收空目录，mcp-auth.json 等文件天然保留）
        if p["projects"].is_dir():
            for at in p["projects"].glob("*/agent-transcripts"):
                if at.is_dir() and not any(x for x in at.iterdir() if x.name not in c.JUNK_NAMES):
                    c.safe_remove(at, mode, removed, errors)
    return {"removed": removed, "errors": errors, "db_rows": db_rows}
