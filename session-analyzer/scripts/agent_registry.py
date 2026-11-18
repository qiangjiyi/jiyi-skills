#!/usr/bin/env python3
"""本机 AI Agent 注册表：探测 / 运行门禁 / 记忆保护 / 宿主判定的唯一事实来源。

每个 Agent 一条 AgentSpec。scan.py 用它决定扫哪些、给报告注入元数据（图标、是否硬删、
是否云端型、记忆保护清单）；close_agents.py 用它决定关哪些 app；server.py 用它做删除前
的运行门禁；宿主判定（当前 skill 跑在哪个 Agent 里）用于提示「换个 Agent 跑更合适」，
并保证 close_agents 永远不会把宿主自己杀掉。

新增 Agent：在 AGENTS 里加一条 spec，再写对应 agents_<key>.py（scan()/delete()）即可。
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
APP_SUPPORT = HOME / "Library" / "Application Support"


@dataclass(frozen=True)
class App:
    """一个需要在清理前退出的桌面 App。"""
    display: str          # 展示名
    quit_name: str        # osascript `tell application "<quit_name>" to quit`
    bundle_marker: str    # 主进程可执行路径片段（pgrep -f 匹配），如 "/WorkBuddy.app/Contents/MacOS/"
    win_image: str = ""   # Windows 进程名


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    icon: str
    detect: tuple[Path, ...]              # 任一存在即视为已安装
    kind: str = "local"                   # local：本地可逐会话删除；cloud：会话在云端，只能清本地缓存
    hard_delete: bool = False             # 删除包含数据库行/索引硬改（不可逆）
    apps: tuple[App, ...] = ()            # 清理前必须退出的 App（运行中会回写数据）
    host_markers: tuple[str, ...] = ()    # 进程祖先链里出现这些路径片段 = 当前宿主是它
    host_fit: str = "avoid"               # recommended | ok | avoid：在它里面运行本 skill 是否合适
    host_reason: str = ""
    memory: tuple[str, ...] = ()          # 永不触碰的记忆/身份文件（展示给用户，删除链路不进入这些路径）
    close_note: str = ""                  # 关闭策略说明（展示用）


CHATGPT = App("ChatGPT", "ChatGPT", "/ChatGPT.app/Contents/MacOS/", "ChatGPT.exe")
CODEX_LEGACY = App("Codex", "Codex", "/Codex.app/Contents/MacOS/", "Codex.exe")
ANTIGRAVITY = App("Antigravity", "Antigravity", "/Antigravity.app/Contents/MacOS/", "Antigravity.exe")
WORKBUDDY = App("WorkBuddy", "WorkBuddy", "/WorkBuddy.app/Contents/MacOS/", "WorkBuddy.exe")
CURSOR = App("Cursor", "Cursor", "/Cursor.app/Contents/MacOS/", "Cursor.exe")
DOUBAO = App("豆包", "Doubao", "/Doubao.app/Contents/MacOS/", "Doubao.exe")
GROK = App("Grok Bot", "Grok Bot", "/Grok Bot.app/Contents/MacOS/", "Grok Bot.exe")

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        key="codex", name="Codex", icon="🟦", hard_delete=True,
        detect=(HOME / ".codex",),
        apps=(CHATGPT, CODEX_LEGACY),
        host_markers=("/ChatGPT.app/", "/Codex.app/", "/codex-cli/"),
        host_fit="avoid",
        host_reason="清理 Codex 必须先退出 ChatGPT，会连同当前对话一起退出",
        memory=("~/.codex/AGENTS.md", "~/.codex/memories/", "~/.codex/config.toml"),
        close_note="ChatGPT 运行时会回写侧栏状态文件，删除前必须退出",
    ),
    AgentSpec(
        key="antigravity", name="Antigravity", icon="🛰️", hard_delete=False,
        detect=(HOME / ".gemini" / "antigravity",),
        apps=(ANTIGRAVITY,),
        host_markers=("/Antigravity.app/",),
        host_fit="avoid",
        host_reason="清理前必须退出 Antigravity，会连同当前对话一起退出",
        memory=("~/.gemini/GEMINI.md",),
        close_note="运行时会用内存里的侧栏列表覆盖索引，删除前必须退出",
    ),
    AgentSpec(
        key="claude", name="Claude Code", icon="🟧",
        detect=(HOME / ".claude",),
        host_markers=("/Claude.app/", "/.local/bin/claude", "/.local/share/claude/", "/claude-code/"),
        host_fit="ok",
        host_reason="会话是独立 jsonl 文件，不用退出；当前会话本身会被保护",
        memory=("~/.claude/CLAUDE.md", "~/.claude/projects/*/memory/", "~/.claude/skills/"),
        close_note="不需要关闭",
    ),
    AgentSpec(
        key="zcode", name="ZCode", icon="🟩", hard_delete=True,
        detect=(HOME / ".zcode",),
        host_markers=("/ZCode.app/", "zcode-cli", "/.local/bin/zcode"),
        host_fit="recommended",
        host_reason="不用退出任何 App，10 分钟内活跃的会话（含当前会话）会自动拒绝删除",
        close_note="不需要关闭（最近 10 分钟内活跃的会话拒绝删除）",
    ),
    AgentSpec(
        key="workbuddy", name="WorkBuddy", icon="🐧", hard_delete=True,
        detect=(HOME / ".workbuddy",),
        apps=(WORKBUDDY,),
        host_markers=("/WorkBuddy.app/",),
        host_fit="avoid",
        host_reason="清理前必须退出 WorkBuddy，会连同当前对话一起退出",
        memory=("~/.workbuddy/memory/", "~/.workbuddy/SOUL.md", "~/.workbuddy/USER.md",
                "~/.workbuddy/IDENTITY.md", "~/.workbuddy/BOOTSTRAP.md",
                "~/WorkBuddy/*/.workbuddy/memory/"),
        close_note="运行时会回写 workbuddy.db 和侧栏快照，删除前必须退出",
    ),
    AgentSpec(
        key="cursor", name="Cursor", icon="⬛", hard_delete=True,
        detect=(HOME / ".cursor", APP_SUPPORT / "Cursor" / "User" / "globalStorage" / "state.vscdb"),
        apps=(CURSOR,),
        host_markers=("/Cursor.app/", "cursor-agent", "/.local/bin/agent"),
        host_fit="avoid",
        host_reason="清理 IDE 会话必须先退出 Cursor；在 cursor-agent CLI 里运行时 IDE 会话可删、CLI 会话 10 分钟内活跃的会拒绝",
        memory=("~/.cursor/rules/", "~/.cursor/skills-cursor/", "~/.cursor/mcp.json"),
        close_note="IDE 运行时缓存会话并回写 state.vscdb，删除 IDE 会话前必须退出；CLI 会话不需要",
    ),
    AgentSpec(
        key="gemini", name="Gemini CLI", icon="✨",
        detect=(HOME / ".gemini" / "tmp",),
        host_markers=("/gemini-cli/", "/bin/gemini"),
        host_fit="ok",
        host_reason="会话是独立 json 文件，不用退出",
        memory=("~/.gemini/GEMINI.md", "~/.gemini/settings.json"),
        close_note="不需要关闭",
    ),
    AgentSpec(
        key="doubao", name="豆包", icon="🫘", kind="cloud",
        detect=(HOME / ".doubao", APP_SUPPORT / "Doubao"),
        apps=(DOUBAO,),
        host_markers=("/Doubao.app/",),
        host_fit="avoid",
        host_reason="清理本地缓存前必须退出豆包",
        close_note="会话在云端；清理本地缓存前必须退出",
    ),
    AgentSpec(
        key="grok", name="Grok Bot", icon="🤖", kind="cloud",
        detect=(HOME / ".grokbot", APP_SUPPORT / "Grok Bot"),
        apps=(GROK,),
        host_markers=("/Grok Bot.app/",),
        host_fit="avoid",
        host_reason="清理本地缓存前必须退出 Grok Bot",
        close_note="会话在云端；清理本地缓存前必须退出",
    ),
)

BY_KEY = {a.key: a for a in AGENTS}
RECOMMENDED_HOST = "zcode"


def is_installed(spec: AgentSpec) -> bool:
    return any(p.exists() for p in spec.detect)


# ─────────────────────────── 进程 / 宿主 ───────────────────────────

def _ps_table() -> dict[int, tuple[int, str]] | None:
    """pid -> (ppid, 完整命令行)。拿不到返回 None（调用方保守处理）。"""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    table = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
        except ValueError:
            continue
    return table


def ancestor_commands(pid: int | None = None) -> list[str]:
    """当前进程（含自身）到 launchd 的命令行链。"""
    table = _ps_table()
    if not table:
        return []
    cur = pid or os.getpid()
    chain, seen = [], set()
    while cur and cur not in seen and cur in table:
        seen.add(cur)
        ppid, cmd = table[cur]
        chain.append(cmd)
        cur = ppid
    return chain


def detect_host(chain: list[str] | None = None) -> str | None:
    """从进程祖先链判断当前 skill 跑在哪个 Agent 里。离自己最近的命中优先。"""
    chain = ancestor_commands() if chain is None else chain
    for cmd in chain:
        for spec in AGENTS:
            if any(m in cmd for m in spec.host_markers):
                return spec.key
    return None


def host_info(chain: list[str] | None = None) -> dict:
    key = detect_host(chain)
    spec = BY_KEY.get(key) if key else None
    rec = BY_KEY[RECOMMENDED_HOST]
    return {
        "key": key,
        "name": spec.name if spec else "未知（终端 / 其他）",
        "fit": spec.host_fit if spec else "ok",
        "reason": spec.host_reason if spec else "未识别宿主，按普通终端处理",
        "recommended": rec.key,
        "recommended_name": rec.name,
    }


def app_running(app: App, table: dict | None = None) -> bool | None:
    """主进程是否在跑。True/False；查不到进程表返回 None（门禁应保守拒绝）。"""
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {app.win_image}"],
                                 capture_output=True, text=True).stdout
            return app.win_image.lower() in out.lower()
        except Exception:
            return None
    table = _ps_table() if table is None else table
    if table is None:
        return None
    return any(app.bundle_marker in cmd for _pp, cmd in table.values())


def running_apps(key: str) -> list[str] | None:
    """该 Agent 需要退出却仍在运行的 App 展示名；进程表不可用返回 None。"""
    spec = BY_KEY[key]
    if not spec.apps:
        return []
    table = None if sys.platform.startswith("win") else _ps_table()
    if table is None and not sys.platform.startswith("win"):
        return None
    out = []
    for app in spec.apps:
        r = app_running(app, table)
        if r is None:
            return None
        if r:
            out.append(app.display)
    return out


def require_closed(key: str) -> None:
    """删除前门禁：App 仍在运行（或无法判定）就拒绝。"""
    r = running_apps(key)
    if r is None:
        raise ValueError(f"无法读取进程列表，保守拒绝删除 {BY_KEY[key].name} 数据")
    if r:
        raise ValueError(f"{'、'.join(r)} 仍在运行，请先退出后再删除（运行中会回写数据，删了也会被写回）")


def meta_for(key: str) -> dict:
    """注入到扫描 JSON 每个 agent 的元数据（报告渲染用）。"""
    s = BY_KEY[key]
    return {
        "icon": s.icon,
        "kind": s.kind,
        "hard_delete": s.hard_delete,
        "must_close": [a.display for a in s.apps],
        "close_note": s.close_note,
        "memory": list(s.memory),
        "host_fit": s.host_fit,
    }


def refresh_running(data: dict) -> dict:
    """给报告数据里每个 Agent 标注当前仍在运行、需要退出的 App（页面每次加载实时重算）。"""
    table = None if sys.platform.startswith("win") else _ps_table()
    for a in data.get("agents", []):
        spec = BY_KEY.get(a.get("key"))
        if not spec or not spec.apps or not a.get("installed"):
            a["running_apps"] = []
            continue
        if table is None and not sys.platform.startswith("win"):
            a["running_apps"] = None
            continue
        a["running_apps"] = [app.display for app in spec.apps if app_running(app, table)]
    return data
