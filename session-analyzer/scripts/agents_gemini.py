#!/usr/bin/env python3
"""Gemini CLI 会话扫描与删除。

布局（gemini-cli 公开实现）：~/.gemini/tmp/<项目 hash 或 slug>/chats/session-*.json，
同级 logs.json / checkpoint-*.json / shell_history 属于项目级运行数据。可选的
~/.gemini/projects.json 记录「真实路径 → slug」。与 Antigravity 共用 ~/.gemini，但只碰 tmp/。
记忆 GEMINI.md、settings.json 永不触碰。本机未安装时自动跳过；无需关闭任何进程。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import agent_common as c

KEY = "gemini"
ROOT = c.HOME / ".gemini"
SESSION_RE = re.compile(r"^session-[\w.-]{1,200}$")


def _project_names(root: Path) -> dict[str, str]:
    try:
        d = json.loads((root / "projects.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    projects = d.get("projects") if isinstance(d, dict) else None
    return {v: k for k, v in projects.items()} if isinstance(projects, dict) else {}


def _session_snippet(f: Path) -> str:
    try:
        d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    snippet = ""
    for m in d.get("messages") or []:
        if isinstance(m, dict) and m.get("type") == "user":
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
            snippet = c.snippet_from_text(str(content or ""))
            break
    return snippet


def scan(root: Path = ROOT) -> dict:
    tmp = root / "tmp"
    agent = c.new_agent(KEY, tmp)
    if not tmp.is_dir():
        agent["installed"] = False
        return c.finalize(agent)
    names = _project_names(root)
    for pdir in sorted(tmp.iterdir()):
        chats = pdir / "chats"
        if not chats.is_dir():
            continue
        sessions = []
        for f in chats.glob("session-*.json"):
            if not SESSION_RE.match(f.stem):
                continue
            snippet = _session_snippet(f)
            sessions.append(c.session(f.stem, snippet or f.stem, c.mtime_of(f), c.dir_size(f), snippet))
        if not sessions:
            continue
        real = names.get(pdir.name)
        agent["projects"].append(c.project(
            pdir.name, real or f"tmp/{pdir.name}", real,
            bool(real) and not Path(real).exists(), sessions))
    agent["note"] = "记忆 GEMINI.md 不会被删除；不需要关闭 Gemini CLI"
    return c.finalize(agent)


def delete(project_id: str, ids: list, mode: str, root: Path = ROOT) -> dict:
    removed, errors = [], []
    chats = root / "tmp" / project_id / "chats"
    if not re.fullmatch(r"[\w.-]{1,200}", project_id or "") or not chats.is_dir():
        raise ValueError("项目不存在")
    for sid in ids:
        if isinstance(sid, str) and SESSION_RE.match(sid):
            c.safe_remove(chats / f"{sid}.json", mode, removed, errors)
    return {"removed": removed, "errors": errors}
