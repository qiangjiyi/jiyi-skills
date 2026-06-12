#!/usr/bin/env python3
"""Read-only scan of Codex / Antigravity / Claude Code session data.

Walks each agent's local data dirs, groups sessions into projects, sizes every
level, flags orphans (whose working dir no longer exists), and emits one JSON
blob consumed by server.py / the report template.

SCAN IS READ-ONLY. Only os.scandir / stat / read-only SQLite SELECT / reading
jsonl. It never writes, moves, or deletes anything. Deletion lives in server.py.

Usage:
    scan.py > /tmp/session_scan.json
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import agyhub_summaries

HOME = Path.home()

# 归类 Antigravity 项目时，这些根下的路径视为"Agent 自身数据"，不算项目信号
AGENT_DATA_PREFIXES = (
    str(HOME / ".gemini"),
    str(HOME / ".codex"),
    str(HOME / ".claude"),
    str(HOME / "Library"),
)


def dir_size(path: Path) -> int:
    """Recursive byte size via scandir. Missing path -> 0. Never follows symlinks."""
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir():
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
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


# ─────────────────────────── Codex ───────────────────────────

def scan_codex() -> dict:
    root = HOME / ".codex"
    state_db = root / "state_5.sqlite"
    agent = {
        "key": "codex",
        "name": "Codex",
        "root": str(root),
        "installed": state_db.exists(),
        "note": "删除 Codex 会话前请先关闭 Codex App，避免删到正在打开的会话导致状态异常。",
        "projects": [],
    }
    if not agent["installed"]:
        return agent

    # immutable=1：Codex App 运行时库处于 WAL 模式、有活跃 -wal 文件，纯 mode=ro
    # 会因无法建立 -shm/-wal 而报 "unable to open database file"。immutable 让只读
    # 读取不依赖这些辅助文件，扫描无需先关 Codex App。
    uri = f"file:{state_db}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id, source, archived, title, cwd, rollout_path, updated_at, "
                "first_user_message, preview FROM threads ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        # 库被独占/正在写等导致瞬时读不了：标注后跳过，不连累其它 Agent 的扫描。
        agent["note"] += f"（本次扫描数据库暂不可读：{e}，可关闭 Codex App 后重试）"
        return agent

    by_cwd: dict[str, list] = {}
    for r in rows:
        tid = r["id"]
        cwd = r["cwd"] or ""
        size = 0
        rollout = Path(r["rollout_path"]).expanduser() if r["rollout_path"] else None
        if rollout:
            size += dir_size(rollout)
        size += dir_size(root / "generated_images" / tid)
        size += dir_size(root / "browser" / "sessions" / f"{tid}.toml")
        snaps = root / "shell_snapshots"
        if snaps.exists():
            for p in snaps.glob(f"{tid}.*.sh"):
                size += dir_size(p)

        title = (r["title"] or "").strip()
        snippet = (r["first_user_message"] or r["preview"] or "").strip().replace("\n", " ")
        by_cwd.setdefault(cwd, []).append({
            "id": tid,
            "title": title or "(无标题)",
            "snippet": snippet[:80],
            "mtime": int(r["updated_at"] or 0),
            "size": size,
            "extra": {"source": r["source"], "archived": int(r["archived"] or 0)},
        })

    for cwd, sessions in by_cwd.items():
        real = Path(cwd).expanduser() if cwd else None
        orphan = bool(cwd) and not (real and real.exists())
        agent["projects"].append(_make_project(
            pid=cwd or "(projectless)",
            label=cwd or "(无工作目录)",
            real_path=cwd,
            orphan=orphan or not cwd,
            sessions=sessions,
        ))
    _finalize_agent(agent)
    return agent


# ─────────────────────────── Antigravity ───────────────────────────

# 用户家目录的上级前缀：macOS 是 /Users，Linux 是 /home
HOME_PREFIX = str(HOME.parent)
# 排除反斜杠并限长：transcript 里 PATH 等环境变量可能以字面 "\n" 拼接多段路径，
# 不排除反斜杠会把整串吞成一个超长路径，随后 is_dir() 触发 ENAMETOOLONG。
PATH_RE = re.compile(re.escape(HOME_PREFIX) + r"/[^\s\"'<>,;:)\]\\]{1,1024}")


def _antigravity_project(transcript: Path) -> str | None:
    """Vote the most common external project root referenced in a transcript."""
    if not transcript.exists():
        return None
    roots: Counter[str] = Counter()
    try:
        text = transcript.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for m in PATH_RE.findall(text):
        m = unquote(m)
        if any(m.startswith(pre) for pre in AGENT_DATA_PREFIXES):
            continue
        roots[_project_root_of(m)] += 1
    if not roots:
        return None
    return roots.most_common(1)[0][0]


def _project_root_of(path: str) -> str:
    """Collapse a file path to a recognizable project root."""
    p = Path(path)
    parts = p.parts
    # <home-parent>/<user>/Projects/{active,labs,archive}/<name> -> keep through <name>
    try:
        i = parts.index("Projects")
        if i + 2 < len(parts):
            return str(Path(*parts[: i + 3]))
    except ValueError:
        pass
    # is_dir() 对异常长/非法路径会抛 ENAMETOOLONG 等 OSError，兜底退回父目录字符串。
    try:
        return str(p if p.is_dir() else p.parent)
    except OSError:
        return str(p.parent)


def scan_antigravity() -> dict:
    root = HOME / ".gemini" / "antigravity"
    conv_dir = root / "conversations"
    brain_dir = root / "brain"
    annot_dir = root / "annotations"
    index_pb = root / "agyhub_summaries_proto.pb"
    agent = {
        "key": "antigravity",
        "name": "Antigravity",
        "root": str(root),
        # 新版把侧栏列表迁进了 agyhub_summaries_proto.pb；任一存在即视为已安装
        "installed": index_pb.exists() or conv_dir.exists(),
        "note": "",
        "projects": [],
    }
    if not agent["installed"]:
        return agent

    grouped: dict[str, list] = {}
    live_uuids: set[str] = set()

    # 1) 侧栏索引（新版权威数据源）。按 workspace 分组，对齐 app 里的项目语义。
    for e in agyhub_summaries.list_entries(index_pb):
        live_uuids.add(e["id"])
        project = e["workspace"] or "(未分组对话)"
        # 索引条目自身字节 + 仍在的 conversation/brain/annotation 文件体积
        size = (e["size"] + dir_size(conv_dir / f"{e['id']}.pb")
                + dir_size(annot_dir / f"{e['id']}.pbtxt") + dir_size(brain_dir / e["id"]))
        grouped.setdefault(project, []).append({
            "id": e["id"],
            "title": e["title"],
            "snippet": "",
            "mtime": e["mtime"] or mtime_of(conv_dir / f"{e['id']}.pb"),
            "size": size,
            "extra": {"in_index": True},
        })

    # 2) 兼容旧版/未迁移：conversations/*.pb 中不在索引里的，补进来
    for pb in sorted(conv_dir.glob("*.pb"), key=mtime_of, reverse=True):
        uuid = pb.stem
        if uuid in live_uuids:
            continue
        live_uuids.add(uuid)
        brain = brain_dir / uuid
        annot = annot_dir / f"{uuid}.pbtxt"
        transcript = brain / ".system_generated" / "logs" / "transcript.jsonl"
        size = dir_size(pb) + dir_size(annot) + dir_size(brain)
        snippet = _antigravity_snippet(transcript)
        project = _antigravity_project(transcript) or "(未分组对话)"
        grouped.setdefault(project, []).append({
            "id": uuid,
            "title": snippet or "(空白对话)",
            "snippet": snippet,
            "mtime": mtime_of(pb),
            "size": size,
            "extra": {},
        })

    for project, sessions in grouped.items():
        real = project if project.startswith("/") else ""
        orphan = bool(real) and not Path(real).exists()
        agent["projects"].append(_make_project(
            pid=project, label=project, real_path=real,
            orphan=orphan, sessions=sessions,
        ))

    # 孤儿：brain/<uuid> 或 annotations/<uuid>.pbtxt 没有对应 conversation。
    # 取并集（与原脚本的孤儿清理一致：brain 目录 + 游离 annotation 都要清）。
    orphan_uuids: dict[str, list] = {}
    if brain_dir.exists():
        for b in brain_dir.iterdir():
            if b.is_dir() and _is_uuid(b.name) and b.name not in live_uuids:
                orphan_uuids.setdefault(b.name, []).append("brain")
    if annot_dir.exists():
        for a in annot_dir.glob("*.pbtxt"):
            if _is_uuid(a.stem) and a.stem not in live_uuids:
                orphan_uuids.setdefault(a.stem, []).append("annotation")
    orphan_items = []
    for uuid, kinds in orphan_uuids.items():
        size = dir_size(brain_dir / uuid) + dir_size(annot_dir / f"{uuid}.pbtxt")
        orphan_items.append({
            "id": uuid, "title": "(孤儿残留：%s，无对应对话)" % "+".join(kinds),
            "snippet": "", "mtime": mtime_of(brain_dir / uuid) or mtime_of(annot_dir / f"{uuid}.pbtxt"),
            "size": size, "extra": {"orphan_kind": "+".join(kinds)},
        })
    if orphan_items:
        agent["projects"].append(_make_project(
            pid="(orphans)", label="(孤儿残留：无对应对话的 brain 目录 / 游离 annotation)",
            real_path="", orphan=True, sessions=orphan_items,
        ))
    _finalize_agent(agent)
    return agent


def _antigravity_snippet(transcript: Path) -> str:
    if not transcript.exists():
        return ""
    try:
        with transcript.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "USER_INPUT":
                    c = re.sub(r"<[^>]+>", "", d.get("content", "")).replace("\n", " ").strip()
                    if c:
                        return c[:80]
    except OSError:
        return ""
    return ""


# ─────────────────────────── Claude Code ───────────────────────────

def scan_claude() -> dict:
    root = HOME / ".claude"
    projects_dir = root / "projects"
    history = root / "history.jsonl"
    agent = {
        "key": "claude",
        "name": "Claude Code",
        "root": str(root),
        "installed": projects_dir.exists(),
        "note": "",
        "projects": [],
    }
    if not agent["installed"]:
        return agent

    # sessionId -> 真实项目路径（history.jsonl 最权威，目录名编码不可逆）
    sid_to_project = _claude_history_projects(history)

    for pdir in sorted(projects_dir.iterdir()):
        if not pdir.is_dir():
            continue
        jsonls = sorted(pdir.glob("*.jsonl"), key=mtime_of, reverse=True)
        if not jsonls:
            # 0-jsonl 的孤儿目录：原脚本会整目录 rm -rf
            agent["projects"].append(_make_project(
                pid=pdir.name, label=_decode_claude(pdir.name) + "（空/孤儿目录）",
                real_path="", orphan=True,
                sessions=[{
                    "id": pdir.name, "title": "(无会话文件的孤儿项目目录)",
                    "snippet": "", "mtime": mtime_of(pdir), "size": dir_size(pdir),
                    "extra": {"claude_kind": "orphan_dir"},
                }],
            ))
            continue

        sessions = []
        real_path = ""
        for f in jsonls:
            sid = f.stem
            real_path = real_path or sid_to_project.get(sid, "")
            size = dir_size(f) + dir_size(pdir / sid)
            size += dir_size(root / "file-history" / sid)
            size += dir_size(root / "session-env" / sid)
            size += dir_size(root / "tasks" / sid)
            snip = _claude_snippet(f)
            sessions.append({
                "id": sid,
                "title": snip or "(无摘要)",
                "snippet": snip,
                "mtime": mtime_of(f),
                "size": size,
                "extra": {"claude_kind": "session"},
            })
        real_path = real_path or _decode_claude(pdir.name)
        orphan = bool(real_path) and not Path(real_path).exists()
        agent["projects"].append(_make_project(
            pid=pdir.name, label=real_path or pdir.name,
            real_path=real_path, orphan=orphan, sessions=sessions,
        ))
    _finalize_agent(agent)
    return agent


def _claude_history_projects(history: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not history.exists():
        return mapping
    try:
        with history.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid, proj = d.get("sessionId"), d.get("project")
                if sid and proj and sid not in mapping:
                    mapping[sid] = proj
    except OSError:
        pass
    return mapping


def _claude_snippet(jsonl: Path) -> str:
    """Read at most the first 8 KB — the first user-message `content` is almost
    always within the first 1-3 lines (~few KB). Falls back to empty for the
    rare case where the first content is past that prefix."""
    try:
        with jsonl.open(encoding="utf-8", errors="ignore") as f:
            chunk = f.read(8192)
    except OSError:
        return ""
    for line in chunk.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        c = d.get("content")
        if isinstance(c, str) and c.strip():
            return c.replace("\n", " ").strip()[:80]
    return ""


def _decode_claude(name: str) -> str:
    # 编码把 / 和 _ 都换成 -，不可逆；仅作展示回退
    return "/" + name.lstrip("-").replace("-", "/")


# ─────────────────────────── shared ───────────────────────────

def _is_uuid(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s))


def _make_project(pid, label, real_path, orphan, sessions) -> dict:
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return {
        "id": pid,
        "label": label,
        "real_path": real_path,
        "orphan": orphan,
        "session_count": len(sessions),
        "size": sum(s["size"] for s in sessions),
        "sessions": sessions,
    }


def _finalize_agent(agent: dict) -> None:
    agent["projects"].sort(key=lambda p: p["size"], reverse=True)
    agent["project_count"] = len(agent["projects"])
    agent["session_count"] = sum(p["session_count"] for p in agent["projects"])
    agent["total_size"] = sum(p["size"] for p in agent["projects"])
    agent["orphan_session_count"] = sum(
        len(p["sessions"]) for p in agent["projects"] if p["orphan"]
    )


def main() -> int:
    start = time.time()
    agents = [scan_codex(), scan_antigravity(), scan_claude()]
    out = {
        "generated_at": int(start),
        "scan_seconds": round(time.time() - start, 1),
        "home": str(HOME),
        "agents": agents,
    }
    # 自检：写到 stderr，不污染 JSON。
    # agent 看一眼就能判断"扫到 0 = 真干净"vs"扫到 0 = 坏了"。
    total_sessions = sum(a["session_count"] for a in agents)
    total_orphans = sum(a["orphan_session_count"] for a in agents)
    total_size = sum(a["total_size"] for a in agents)
    print(
        f"[scan] agents={sum(1 for a in agents if a['installed'])} "
        f"sessions={total_sessions} orphans={total_orphans} "
        f"size={total_size} in {out['scan_seconds']}s",
        file=sys.stderr,
    )
    # 空态快通道：JSON 写到 stdout 后，再多打一行友好提示，
    # 让 agent 看到一眼就能决定"无需起服务"。
    json.dump(out, sys.stdout, ensure_ascii=False)
    if total_sessions == 0 and total_orphans == 0:
        print("", file=sys.stderr)  # 分隔上一行
        print(
            "✓ 本机 AI 会话状态干净（0 会话 / 0 孤儿），无需清理。",
            file=sys.stderr,
        )
    print("✓ DONE", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
