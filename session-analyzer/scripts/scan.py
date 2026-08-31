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
import stat
import sys
import tempfile
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import agyhub_summaries
import codex_ui_state

HOME = Path.home()

# 归类 Antigravity 项目时，这些根下的路径视为"Agent 自身数据"，不算项目信号
AGENT_DATA_PREFIXES = (
    str(HOME / ".gemini"),
    str(HOME / ".codex"),
    str(HOME / ".claude"),
    str(HOME / ".zcode"),
    str(HOME / "Library"),
)

# ZCode：会话 id 前缀（sess_<uuid> / sess_subagent_agent_<uuid>）
ZCODE_SESS_RE = re.compile(r"^sess_[0-9a-f-]{8,}$")
# ZCode：最近 10 分钟内仍有更新的会话视为「可能正在运行」，删除时会被拒绝
ZCODE_LIVE_WINDOW_MS = 10 * 60 * 1000


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
        "note": "删除 Codex 会话前请先关闭 ChatGPT App（2026-07-10 起独立 Codex App 已并入 ChatGPT），避免删到正在打开的会话导致状态异常。",
        "projects": [],
    }
    if not agent["installed"]:
        return agent

    # immutable=1：ChatGPT App（内嵌 Codex）运行时库处于 WAL 模式、有活跃 -wal 文件，纯 mode=ro
    # 会因无法建立 -shm/-wal 而报 "unable to open database file"。immutable 让只读
    # 读取不依赖这些辅助文件，扫描无需先关 ChatGPT App。
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
    # Codex Electron 侧栏状态文件（只读解析）：侧栏列表不读 DB，删了 DB 行侧栏仍会
    # 留幽灵条目。这里给存活会话打 UI 残留标记，并把「UI 有 id 但 threads 表已无」的
    # 纯幽灵单列计数；实际剪除只发生在删除链路（agent_delete / codex_ui_state）。
    ui_data = None
    try:
        ui_raw, _ = codex_ui_state.load_state_raw(root / codex_ui_state.STATE_NAME)
        ui_data = codex_ui_state.parse_state(ui_raw)
    except (OSError, codex_ui_state.CleanupRejected):
        ui_data = None  # 文件不存在 / 结构异常：跳过标记，不连累扫描
    residue = codex_ui_state.residue_ids(ui_data) if ui_data is not None else None
    if ui_data is None:
        agent["note"] += "（侧栏状态文件不可解析，本次未标注 codex_ui_residue / 幽灵条目）"
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
        # sessions/YYYY/MM/DD/rollout-*-<tid>.jsonl — separate from DB rollout_path
        sess_dir = root / "sessions"
        if sess_dir.exists():
            for p in sess_dir.rglob(f"rollout-*-{tid}.jsonl"):
                size += dir_size(p)

        title = (r["title"] or "").strip()
        snippet = (r["first_user_message"] or r["preview"] or "").strip().replace("\n", " ")
        extra = {"source": r["source"], "archived": int(r["archived"] or 0)}
        if residue is not None and tid in residue:
            extra["codex_ui_residue"] = True  # 侧栏状态文件仍挂着这条会话（删除链路会一并剪除）
        by_cwd.setdefault(cwd, []).append({
            "id": tid,
            "title": title or "(无标题)",
            "snippet": snippet[:80],
            "mtime": int(r["updated_at"] or 0),
            "size": size,
            "extra": extra,
        })

    if ui_data is not None:
        live_ids = {r["id"] for r in rows}
        ghosts = codex_ui_state.ghost_entries(ui_data, live_ids)
        # agent 层计数 + 明细（HTML 报告分区展示，一键清理走 server → agent_delete）
        agent["ghost_ui_entry_count"] = len(ghosts)
        agent["ghost_ui_entries"] = ghosts

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


# 用户家目录的上级前缀：macOS 是 /Users，Linux 是 /home
HOME_PREFIX = str(HOME.parent)
# 排除反斜杠并限长：transcript 里 PATH 等环境变量可能以字面 "\n" 拼接多段路径，
# 不排除反斜杠会把整串吞成一个超长路径，随后 is_dir() 触发 ENAMETOOLONG。
PATH_RE = re.compile(re.escape(HOME_PREFIX) + r"/[^\s\"'<>,;:)\]\\]{1,1024}")

# Claude project dirs that correspond to Multica workspace tasks.
# Encoded path: /Users/<user>/multica_workspaces/<ws_id>/<task_prefix>/workdir
# becomes: -Users-<user>-multica-workspaces-<ws_id>-<task_prefix>-workdir
MULTICA_CLAUDE_PATTERN = re.compile(
    r"multica-workspaces-"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-"
    r"([0-9a-f]{8})-workdir$"
)


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

    # 构建 annotation 存在集合：annotations/*.pbtxt 是 UI 是否显示的可靠信号。
    # 索引里有但 annotation 不存在 → 索引残留（UI 里看不到）。
    annot_uuids: set[str] = set()
    if annot_dir.exists():
        for a in annot_dir.glob("*.pbtxt"):
            if _is_uuid(a.stem):
                annot_uuids.add(a.stem)

    stale_index_group = "(索引残留：侧栏已不可见)"

    # 1) 侧栏索引（新版权威数据源）。按 workspace 分组，对齐 app 里的项目语义。
    #    索引里有但 annotation 没有的，归到"索引残留"分组，不跟正常会话混在一起。
    for e in agyhub_summaries.list_entries(index_pb):
        live_uuids.add(e["id"])
        has_annot = e["id"] in annot_uuids
        project = (e["workspace"] or "(未分组对话)") if has_annot else stale_index_group
        # 索引条目自身字节 + 仍在的 conversation/brain/annotation 文件体积
        # 新版 Antigravity 用 SQLite .db 存对话正文（旧版是 .pb），两个都算。
        size = (e["size"] + dir_size(conv_dir / f"{e['id']}.pb")
                + dir_size(conv_dir / f"{e['id']}.db")
                + dir_size(annot_dir / f"{e['id']}.pbtxt") + dir_size(brain_dir / e["id"]))
        grouped.setdefault(project, []).append({
            "id": e["id"],
            "title": e["title"],
            "snippet": "",
            "mtime": e["mtime"] or mtime_of(conv_dir / f"{e['id']}.pb"),
            "size": size,
            "extra": {"in_index": True, "index_stale": not has_annot},
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

    # 2b) 新版 .db 对话文件中不在索引里的残留也补进来（当作孤儿/残留）
    for db in sorted(conv_dir.glob("*.db"), key=mtime_of, reverse=True):
        uuid = db.stem
        if uuid in live_uuids:
            continue
        live_uuids.add(uuid)
        brain = brain_dir / uuid
        annot = annot_dir / f"{uuid}.pbtxt"
        size = dir_size(db) + dir_size(annot) + dir_size(brain)
        # .db 文件没有现成的标题/摘要接口，标为"残留对话数据库"
        grouped.setdefault("(未分组对话)", []).append({
            "id": uuid,
            "title": "(残留对话 DB：索引中不存在)",
            "snippet": "",
            "mtime": mtime_of(db),
            "size": size,
            "extra": {"orphan_kind": "db_not_in_index"},
        })

    for project, sessions in grouped.items():
        real = project if project.startswith("/") else ""
        orphan = bool(real) and not Path(real).exists()
        # 索引残留分组整体视为孤儿项目
        if project == stale_index_group:
            orphan = True
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

TEMP_SKILL_RUNTIME_RE = re.compile(
    r"/_skill-runtime/releases/[0-9a-fA-F]{64}/runner/?$"
)


def _claude_jsonl_cwd(jsonl: Path) -> str | None:
    counts: Counter[str] = Counter()
    try:
        with jsonl.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = d.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    counts[cwd.strip()] += 1
    except OSError:
        return None
    if not counts:
        return None
    return min(counts, key=lambda path: (-counts[path], path))


def _path_exists(path: str) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def _choose_claude_project_path(paths: list[str]) -> str:
    counts = Counter(path for path in paths if path)
    if not counts:
        return ""
    return min(
        counts,
        key=lambda path: (not _path_exists(path), -counts[path], path),
    )


def _claude_orphan_reason(paths: list[str], orphan: bool) -> str | None:
    if not orphan:
        return None
    known = [path for path in paths if path]
    if known and all(TEMP_SKILL_RUNTIME_RE.search(path) for path in known):
        return "temporary_skill_runtime"
    return "missing_workdir"


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

    # sessionId -> history 记录的项目路径；session JSONL 自带 cwd 时优先使用
    sid_to_project = _claude_history_projects(history)

    for pdir in sorted(projects_dir.iterdir()):
        if not pdir.is_dir():
            continue
        jsonls = sorted(pdir.glob("*.jsonl"), key=mtime_of, reverse=True)
        if not jsonls:
            # 0-jsonl 的目录可能是「真实项目还在，只是没会话文件」(empty dir)，
            # 也可能是「真实项目已被删，目录是遗留残渣」(orphan dir)。
            # 用 _resolve_claude_path() 试着解码真实路径，能解且存在 → 空目录；
            # 解不出 / 不存在 → 才算孤儿。
            resolved = _resolve_claude_path(pdir.name)
            is_orphan = not resolved or not Path(resolved).exists()
            label_path = resolved or _decode_claude(pdir.name)
            label_suffix = "（空/孤儿目录）" if is_orphan else "（空目录）"
            title_suffix = "（无会话文件的孤儿项目目录）" if is_orphan else "（无会话文件的项目目录）"
            project = _make_project(
                pid=pdir.name, label=label_path + label_suffix,
                real_path=resolved or "", orphan=is_orphan,
                sessions=[{
                    "id": pdir.name, "title": title_suffix,
                    "snippet": "", "mtime": mtime_of(pdir), "size": dir_size(pdir),
                    "extra": {"claude_kind": "orphan_dir"},
                }],
            )
            project["orphan_reason"] = "missing_workdir" if is_orphan else None
            agent["projects"].append(project)
            continue

        sessions = []
        project_paths = []
        fallback_path = _resolve_claude_path(pdir.name)
        for f in jsonls:
            sid = f.stem
            session_path = _claude_jsonl_cwd(f) or sid_to_project.get(sid, "") or fallback_path
            if session_path:
                project_paths.append(session_path)
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
                "extra": {"claude_kind": "session", "cwd": session_path},
            })
        real_path = _choose_claude_project_path(project_paths) or fallback_path
        orphan = bool(project_paths) and not any(_path_exists(path) for path in project_paths)
        project = _make_project(
            pid=pdir.name, label=real_path or pdir.name,
            real_path=real_path, orphan=orphan, sessions=sessions,
        )
        project["orphan_reason"] = _claude_orphan_reason(project_paths, orphan)
        agent["projects"].append(project)
    _enrich_multica_sessions(agent)
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
    """Return a short human label for a Claude Code jsonl session.

    Reads **line by line**, returning at the first usable text — the fixed
    8 KB prefix used to clip sessions whose first user message sat behind
    hundreds of KB of metadata (queue-operation / attachment / mode rows
    from SDK-driven sessions like Coze bots). A 1 MiB hard cap protects
    against runaway reads on pathological sessions that never emit a
    user/assistant row.

    Skips:

    - ``type=system`` rows (Claude inserts a ``subtype=local_command`` row
      right after every slash command, with top-level ``content`` literally
      ``<local-command-stdout></local-command-stdout>`` — that string is
      useless as a title and, if picked up first, makes every slash-command
      session look identical).
    - ``type`` in ``{queue-operation, attachment, mode, permission-mode,
      file-history-snapshot}`` — lifecycle/metadata rows with no usable text.
    - ``isMeta=true`` rows (synthetic system caveats like the
      ``<local-command-caveat>`` injected before slash command output).
    - Slash-command ``user`` rows whose ``message.content`` is the
      ``<command-name>/xxx</command-name>`` shell. For those we synthesize
      ``Slash: /xxx`` instead of dumping the XML tags into the title.

    Handles both string content (older Claude Code) and the modern
    ``message.content`` array of ``{type: "text", text: "..."}`` blocks
    — without the array form, every SDK-driven session (e.g. Coze bots)
    shows as ``(无摘要)``.

    Sidecar metadata (``aiTitle`` / ``lastPrompt``) is captured as it
    passes, but doesn't trigger early-return: a real user/assistant
    message, if present, is preferred over the sidecar.

    Falls back to the sidecar when no usable text is found within the cap.
    """
    SLASH_NAME_RE = re.compile(r"<command-name>\s*(/\S+?)\s*</command-name>")
    SLASH_NAME_OPEN_RE = re.compile(r"<command-name>\s*(/\S+)")
    # Lifecycle / metadata rows: no usable label, skip up front.
    SKIP_TYPES = frozenset({
        "queue-operation", "attachment", "mode",
        "permission-mode", "file-history-snapshot",
    })
    # Hard cap on how far into the jsonl we scan for a usable label.
    # 1 MiB comfortably covers SDK-driven sessions that bury the first
    # user message behind hundreds of KB of metadata.
    MAX_SCAN_BYTES = 1 << 20  # 1 MiB

    def _msg_content(d: dict) -> str:
        """Content may live at top-level (system/assistant) or under message
        (user entries in modern Claude Code). The modern API stores it as a
        list of blocks (text / tool_use / image / ...); we concatenate the
        text blocks."""
        def _flatten(content) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        t = blk.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                return "\n".join(parts)
            return ""

        flat = _flatten(d.get("content"))
        if flat:
            return flat
        m = d.get("message")
        if isinstance(m, dict):
            return _flatten(m.get("content"))
        return ""

    ai_title = ""
    last_prompt = ""
    bytes_read = 0
    try:
        with jsonl.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                bytes_read += len(line.encode("utf-8", errors="ignore"))
                if bytes_read > MAX_SCAN_BYTES:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Sidecar metadata rows (ai-title / last-prompt) carry a usable label
                # even for sessions that have no user/assistant message rows at all.
                # Capture as we pass, but don't early-return: a real message wins.
                if not ai_title and d.get("aiTitle"):
                    ai_title = str(d["aiTitle"]).replace("\n", " ").strip()
                if not last_prompt and d.get("lastPrompt"):
                    last_prompt = str(d["lastPrompt"]).replace("\n", " ").strip()

                # Skip system rows (local_command, etc.) and meta rows (caveats).
                if d.get("type") == "system":
                    continue
                if d.get("isMeta"):
                    continue
                if d.get("type") in SKIP_TYPES:
                    continue

                c = _msg_content(d)
                if not c.strip():
                    continue

                # Slash-command session: extract the command name and label it cleanly.
                m = SLASH_NAME_RE.search(c) or SLASH_NAME_OPEN_RE.search(c)
                if m:
                    return f"Slash: {m.group(1)}"

                return c.replace("\n", " ").strip()[:80]
    except OSError:
        return ""
    return (ai_title or last_prompt)[:80]


def _decode_claude(name: str) -> str:
    # 编码把 / 和 _ 都换成 -，不可逆；仅作展示回退
    return "/" + name.lstrip("-").replace("-", "/")


def _resolve_claude_path(encoded_name: str) -> str:
    """Decode Claude project dir name to real path with heuristic correction.

    Claude encodes both '/' and '_' as '-', making naive decoding lossy:
    ``jiyi-skills`` → ``jiyi/skills`` instead of ``jiyi-skills``.
    This function first tries the naive decode; if the path doesn't exist,
    it uses a greedy left-to-right algorithm that merges adjacent segments
    with '-' or '_' until an existing directory is found.
    """
    naive = _decode_claude(encoded_name)
    if Path(naive).exists():
        return naive

    segments = encoded_name.lstrip("-").split("-")
    resolved = _greedy_resolve_path(segments)
    return resolved or naive


def _greedy_resolve_path(segments: list[str]) -> str | None:
    """Build a filesystem path from encoded segments, greedily matching
    existing directories from left to right.

    At each position, try the single segment as a dir name.  If it
    doesn't exist, progressively merge with the next segment(s) using
    '-' or '_' as the joiner, until an existing directory is found.
    Falls back to ``None`` when resolution fails.
    """
    path = Path("/")
    i = 0
    while i < len(segments):
        found = False
        merged = segments[i]
        for j in range(i, len(segments)):
            if j > i:
                merged += "-" + segments[j]
            # '-', '_' and space are all encoded to '-' in the dir name, so
            # try each as the original separator when rebuilding the path.
            # Also try a leading-'.': Claude encodes a hidden dir like `.aamp`
            # as `--aamp` (the '.' becomes '-'), so the dot is lost and the
            # bare segment fails to match until we re-prefix it.
            #
            # Order matters: try the `.{X}` forms FIRST. On case-insensitive
            # filesystems (macOS APFS), the bare `X` can short-circuit to a
            # case-different sibling (e.g. `.coze` vs an unrelated `Coze/`)
            # before the resolver reaches the actual hidden-directory match.
            base_names = (merged, merged.replace("-", "_"), merged.replace("-", " "))
            for name in (*(f".{n}" for n in base_names), *base_names):
                test = path / name
                try:
                    if test.is_dir():
                        path = test
                        i = j + 1
                        found = True
                        break
                except OSError:
                    continue
            if found:
                break
        if not found:
            return None
    return str(path)


def _fetch_multica_task_status_map() -> dict[str, str]:
    """Fetch task→issue status mapping from the Multica API.

    Reads ~/.multica/config.json for auth, then:
    1. GET /api/issues → build issue_id → status map
    2. GET /api/agents → list all agents
    3. GET /api/agents/{id}/tasks → map each task prefix to its issue status

    Returns: {task_prefix_8char: issue_status}
    - Issue in terminal state (done/cancelled) → task is safe to clean
    - Issue NOT terminal (backlog/todo/in_progress/in_review/blocked) → task is active

    Returns empty dict if config is missing or API is unreachable
    (scan degrades gracefully to local-only gc_meta fallback).
    """
    config_path = HOME / ".multica" / "config.json"
    if not config_path.exists():
        return {}

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    token = config.get("token", "")
    workspace_id = config.get("workspace_id", "")
    server_url = config.get("server_url", "").rstrip("/")
    if not token or not workspace_id or not server_url:
        return {}

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": workspace_id,
    }

    def _api_get(path: str) -> list[dict]:
        url = f"{server_url}{path}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
            print(f"[multica-api] GET {path} failed: {e}", file=sys.stderr)
            return []
        if isinstance(data, list):
            return data
        # issues endpoint returns {"issues": [...], "total": N}
        for key in ("issues", "agents", "data", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []

    # Step 1: list all issues → build issue_id → status map
    issues = _api_get("/api/issues")
    issue_status: dict[str, str] = {}
    for issue in issues:
        iid = issue.get("id", "")
        s = issue.get("status", "")
        if iid and s:
            issue_status[iid] = s
    if not issue_status:
        print("[multica-api] No issues fetched, skipping task mapping", file=sys.stderr)
        return {}

    # Step 2: list all agents
    agents = _api_get("/api/agents")
    if not agents:
        print("[multica-api] No agents fetched", file=sys.stderr)
        return {}

    # Step 3: for each agent, fetch tasks → map task prefix → issue status
    # For tasks WITH issue_id: use issue status (done/cancelled = cleanable)
    # For tasks WITHOUT issue_id (autopilot/chat): use task's own status
    #   (completed/failed/cancelled = cleanable)
    TASK_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
    status_map: dict[str, str] = {}
    for agent in agents:
        agent_id = agent.get("id", "")
        if not agent_id:
            continue
        tasks = _api_get(f"/api/agents/{agent_id}/tasks")
        for t in tasks:
            tid = t.get("id", "")
            iid = t.get("issue_id", "")
            tstatus = t.get("status", "")
            if not tid:
                continue
            prefix = tid[:8]
            if iid:
                # Task belongs to an issue → use issue status
                status_map[prefix] = issue_status.get(iid, "unknown")
            elif tstatus:
                # No issue (autopilot/chat) → use task's own status,
                # normalized to issue-style: terminal task states → "done"
                if tstatus in TASK_TERMINAL_STATES:
                    status_map[prefix] = "done"  # treat as cleanable
                else:
                    status_map[prefix] = "in_progress"  # treat as active

    return status_map


def _enrich_multica_sessions(agent: dict) -> None:
    """Identify Claude sessions backed by Multica workspace tasks.
    Enriches project/session dicts with completion status and extra cleanup
    paths (workspace task dir + Claude CLI Node.js cache).

    Uses the Multica API as the primary source for issue status.
    An issue NOT in a terminal state (done/cancelled) means its tasks
    may still be needed — those sessions are marked active and excluded
    from bulk clean. Falls back to local .gc_meta.json when the API is
    unavailable.
    """
    # Fetch authoritative status map from Multica API
    # Returns: task_prefix_8char → issue_status
    api_status_map = _fetch_multica_task_status_map()
    api_available = bool(api_status_map)
    print(f"[multica] API available: {api_available}, mapped {len(api_status_map)} task prefixes", file=sys.stderr)

    multica_ws_root = HOME / "multica_workspaces"
    cli_cache_root = HOME / "Library" / "Caches" / "claude-cli-nodejs"
    cleanable_count = 0
    active_count = 0

    # Issue terminal states = safe to clean; everything else = do NOT bulk-clean
    ISSUE_TERMINAL_STATES = frozenset({"done", "cancelled"})

    for project in agent["projects"]:
        m = MULTICA_CLAUDE_PATTERN.search(project["id"])
        if not m:
            continue
        workspace_id = m.group(1)
        task_prefix = m.group(2)

        # Find matching task dir in workspace (short prefix → full UUID)
        ws_dir = multica_ws_root / workspace_id
        task_dir = None
        task_id_full = None
        if ws_dir.is_dir():
            for d in ws_dir.iterdir():
                if d.is_dir() and d.name.startswith(task_prefix):
                    task_dir = d
                    task_id_full = d.name
                    break

        # Determine task cleanability: based on ISSUE status (not task status)
        # Issue is done/cancelled → safe to clean; otherwise → active (protected)
        multica_status = "cleanable"  # safe default for unknown
        task_kind = None

        if api_available:
            # Authoritative: check issue status via API
            issue_status = api_status_map.get(task_prefix)
            if issue_status and issue_status not in ISSUE_TERMINAL_STATES:
                multica_status = "active"
        elif task_dir:
            # Fallback: read local .gc_meta.json
            gc_meta = task_dir / ".gc_meta.json"
            if gc_meta.exists():
                try:
                    with gc_meta.open(encoding="utf-8") as f:
                        meta = json.load(f)
                    if not meta.get("completed_at"):
                        multica_status = "active"
                    task_kind = meta.get("kind")
                except (json.JSONDecodeError, OSError):
                    pass
            else:
                # No gc_meta = likely still running or orphaned
                multica_status = "active"

        # Calculate extra sizes (workspace task dir + CLI cache)
        ws_size = dir_size(task_dir) if task_dir else 0
        cache_dir = cli_cache_root / project["id"]
        cache_size = dir_size(cache_dir)
        extra_size = ws_size + cache_size

        # Enrich project
        project["multica_extra_size"] = extra_size
        project["multica_status"] = multica_status
        project["multica_workspace_path"] = str(task_dir) if task_dir else None
        project["multica_cache_path"] = str(cache_dir) if cache_dir.exists() else None
        project["size"] += extra_size

        # Fix display and orphan status:
        # _decode_claude() replaces ALL '-' with '/', mangling UUID hyphens
        # and underscores → reconstruct the correct path from regex components.
        # Orphan should be based on whether the workspace task dir exists,
        # not whether the (possibly mangled) real_path exists.
        if task_dir:
            project["label"] = f"~/multica_workspaces/{workspace_id}/{task_id_full}"
            project["real_path"] = str(task_dir)
            project["orphan"] = False  # task dir exists → not orphaned
            project["orphan_reason"] = None
        else:
            project["label"] = f"~/multica_workspaces/{workspace_id}/{task_prefix}…（任务目录已清理）"
            project["orphan"] = True  # task dir gone → truly orphaned
            project["orphan_reason"] = "missing_workdir"

        # Enrich sessions
        for session in project["sessions"]:
            session["extra"]["multica"] = {
                "workspace_id": workspace_id,
                "task_id": task_id_full,
                "task_prefix": task_prefix,
                "status": multica_status,
                "task_kind": task_kind,
            }

        # Count non-orphan sessions by status
        # (orphan sessions are already tracked in orphan_session_count;
        #  the orphan bulk clean also handles Multica extras via dispatch)
        if not project.get("orphan"):
            if multica_status == "cleanable":
                cleanable_count += project["session_count"]
            else:
                active_count += project["session_count"]

    agent["multica_cleanable_count"] = cleanable_count
    agent["multica_active_count"] = active_count


# ─────────────────────────── ZCode ───────────────────────────

def _zcode_ro_connect(db: Path):
    """只读连接 ZCode 的 SQLite。优先 mode=ro（能读 WAL 新数据）；被占用时回退
    immutable=1（不依赖 -shm/-wal）；都失败返回 None 由调用方标注跳过。"""
    for uri in (f"file:{db}?mode=ro", f"file:{db}?mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return con
        except sqlite3.Error:
            continue
    return None


def _zcode_task_index(root_v2: Path) -> dict[str, dict]:
    """v2/tasks-index.sqlite 的 tasks 表 → {task_id: {status, model, workspace}}。
    表缺失 / 读不了 → 空 dict（该信息仅用于展示增强，扫描照常进行）。"""
    db = root_v2 / "tasks-index.sqlite"
    if not db.exists():
        return {}
    con = _zcode_ro_connect(db)
    if con is None:
        return {}
    out: dict[str, dict] = {}
    try:
        rows = con.execute(
            "SELECT task_id, task_status, model, workspace_key FROM tasks "
            "WHERE deleted = 0"
        ).fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    for r in rows:
        out[r["task_id"]] = {
            "status": r["task_status"], "model": r["model"],
            "workspace": r["workspace_key"],
        }
    return out


def scan_zcode() -> dict:
    """扫描 ZCode（~/.zcode）的会话数据。

    数据模型（2026-08 调研）：
    - 会话权威来源 = cli/db/db.sqlite 的 session 表：id = sess_*（子代理为
      sess_subagent_agent_*，transcript 存在父会话 agents/<父id>/ 下），path =
      真实工作目录，task_type 区分 interactive / subagent_child，时间为毫秒。
    - 对话正文存共享库的 message/part/session_entry（session 行删除时级联）。
    - 任务索引 = v2/tasks-index.sqlite 的 tasks 行，task_id 与会话 id 一一对应。
    - 逐会话卫星 = cli/{artifacts,exec,image-cache,agents}/sess_<id>/ 与
      rollout/model-io-sess_<id>.jsonl。
    - 不归属单会话的数据（log/、exec/shell-snapshots、v2/checkpoints 等）不扫。
    """
    root = HOME / ".zcode" / "cli"
    root_v2 = HOME / ".zcode" / "v2"
    state_db = root / "db" / "db.sqlite"
    agent = {
        "key": "zcode",
        "name": "ZCode",
        "root": str(root),
        "installed": state_db.exists(),
        "note": (
            "ZCode 会话正文存于共享 SQLite（db.sqlite 与 tasks-index.sqlite），删除会话即硬删数据库行，"
            "不可逆、不进废纸篓；卫星文件（artifacts/exec/image-cache/agents/rollout）默认移废纸篓。"
            "删除数据库行不会让 db.sqlite 文件本身缩小。最近 10 分钟内仍有活动的会话会被拒绝删除，"
            "以防删到正在运行的会话。"
        ),
        "projects": [],
    }
    if not agent["installed"]:
        return agent

    con = _zcode_ro_connect(state_db)
    if con is None:
        agent["note"] += "（本次扫描数据库暂不可读，可稍后重试）"
        return agent
    try:
        rows = con.execute(
            "SELECT id, path, title, task_type, parent_id, "
            "time_created, time_updated, time_archived FROM session "
            "ORDER BY time_updated DESC"
        ).fetchall()
        # 会话正文在共享库内占用的字节（删行只是逻辑释放，文件不缩小）
        db_bytes: dict[str, int] = {}
        for sid, nbytes in con.execute(
            "SELECT session_id, SUM(LENGTH(data)) FROM ("
            " SELECT session_id, data FROM session_entry"
            " UNION ALL SELECT session_id, data FROM message"
            " UNION ALL SELECT session_id, data FROM part"
            ") GROUP BY session_id"
        ):
            db_bytes[sid] = nbytes or 0
    except sqlite3.OperationalError as e:
        agent["note"] += f"（本次扫描数据库暂不可读：{e}，可稍后重试）"
        return agent
    finally:
        con.close()

    tasks = _zcode_task_index(root_v2)
    now_ms = int(time.time() * 1000)
    live_ids = {r["id"] for r in rows}

    by_path: dict[str, list] = {}
    for r in rows:
        sid = r["id"]
        path = (r["path"] or "").strip()
        size = db_bytes.get(sid, 0)
        for sub in ("artifacts", "exec", "image-cache", "agents"):
            size += dir_size(root / sub / sid)
        size += dir_size(root / "rollout" / f"model-io-{sid}.jsonl")
        t = tasks.get(sid, {})
        extra = {
            "task_type": r["task_type"],
            "parent_id": r["parent_id"],
            "archived": 1 if r["time_archived"] else 0,
        }
        if t:
            extra["task_status"] = t["status"]
            extra["model"] = t["model"]
        if now_ms - int(r["time_updated"] or 0) < ZCODE_LIVE_WINDOW_MS:
            extra["zcode_live"] = True
        by_path.setdefault(path, []).append({
            "id": sid,
            "title": (r["title"] or "").strip() or "(无标题)",
            "snippet": "",
            "mtime": int(r["time_updated"] or 0) // 1000,
            "size": size,
            "extra": extra,
        })

    for path, sessions in by_path.items():
        orphan = bool(path) and not _path_exists(path)
        agent["projects"].append(_make_project(
            pid=path or "(projectless)",
            label=path or "(无工作目录)",
            real_path=path,
            orphan=orphan or not path,
            sessions=sessions,
        ))

    # 孤儿残留：数据库无会话记录的卫星目录 / rollout 日志 / 任务索引行
    orphan_items = []
    seen: set[str] = set()
    for sub in ("artifacts", "exec", "image-cache", "agents"):
        d = root / sub
        if not d.is_dir():
            continue
        for item in d.iterdir():
            if item.name in live_ids or item.name in seen or not ZCODE_SESS_RE.match(item.name):
                continue
            seen.add(item.name)
            orphan_items.append({
                "id": item.name,
                "title": f"(孤儿残留：{sub}/ 目录，数据库无此会话)",
                "snippet": "", "mtime": mtime_of(item),
                "size": dir_size(item), "extra": {"orphan_kind": "satellite"},
            })
    rollout_dir = root / "rollout"
    if rollout_dir.is_dir():
        for f in rollout_dir.glob("model-io-sess_*.jsonl"):
            sid = f.name[len("model-io-"):-len(".jsonl")]
            if sid in live_ids or sid in seen:
                continue
            seen.add(sid)
            orphan_items.append({
                "id": sid,
                "title": "(孤儿残留：模型 I/O 日志，数据库无此会话)",
                "snippet": "", "mtime": mtime_of(f),
                "size": dir_size(f), "extra": {"orphan_kind": "rollout"},
            })
    for tid, t in tasks.items():
        if tid in live_ids:
            continue
        orphan_items.append({
            "id": f"task:{t['workspace']}|{tid}",
            "title": "(孤儿残留：任务索引行，数据库无此会话)",
            "snippet": "", "mtime": 0,
            "size": 0, "extra": {"orphan_kind": "task_index"},
        })
    if orphan_items:
        agent["projects"].append(_make_project(
            pid="(zcode-orphans)", label="(孤儿残留：无数据库会话的文件/任务索引)",
            real_path="", orphan=True, sessions=orphan_items,
        ))
    _finalize_agent(agent)
    return agent


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


def _write_json_out(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(name)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    # 支持 --json-out <path>：JSON 直接写文件，避免 stdout/stderr 混叠导致
    # 下游解析失败（bash 重定向、run.sh 包装等场景都可能混进去）。
    # 未指定时仍输出到 stdout（兼容旧用法）。
    json_out_path: str | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--json-out" and i + 1 < len(args):
            json_out_path = args[i + 1]
            i += 2
        else:
            i += 1

    start = time.time()
    agents = [scan_codex(), scan_antigravity(), scan_claude(), scan_zcode()]
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

    # 写 JSON：优先写文件（最稳健），否则写 stdout
    json_text = json.dumps(out, ensure_ascii=False)
    if json_out_path:
        _write_json_out(Path(json_out_path), json_text)
        print(f"[scan] JSON 已写入: {json_out_path}", file=sys.stderr)
    else:
        sys.stdout.write(json_text)
        sys.stdout.flush()

    # 空态快通道：JSON 写完后，再多打一行友好提示到 stderr，
    # 让 agent 看到一眼就能决定"无需起服务"。
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
