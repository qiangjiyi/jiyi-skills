#!/usr/bin/env python3
"""删除前关闭「运行中会回写会话数据」的 Agent App（清单来自 agent_registry）。

当前需要关闭：ChatGPT（内嵌 Codex，含遗留独立 Codex App）、Antigravity、WorkBuddy、Cursor、
豆包、Grok Bot——只关已安装的。Claude Code / ZCode / Gemini CLI 不需要关。

铁律：
- **永不关闭宿主。** 当前 skill 跑在哪个 Agent 里（进程祖先链判定），那个 App 一律跳过，
  并提示「它的会话这次删不了，建议在 ZCode 里运行」。
- 先 osascript 优雅退出（让 App 自行存盘）；等不到再 pkill。Cursor 不强杀（编辑器可能有未保存
  内容），优雅退出失败就请用户手动退出。
- 只关这些 App，不动其它进程。逐步打印过程，供 agent 如实转述。

用法：close_agents.py [--only codex,cursor] [--skip doubao,grok]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

import agent_registry as reg

NO_FORCE_KILL = {"Cursor"}


def _say(msg: str) -> None:
    print(msg, flush=True)


def _graceful_quit(app: reg.App) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'tell application "{app.quit_name}" to quit'],
                           capture_output=True, timeout=5)
        elif sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/IM", app.win_image], capture_output=True)
    except Exception:
        pass


def _force_kill(app: reg.App, hard: bool) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["pkill"] + (["-9"] if hard else []) + ["-f", app.bundle_marker],
                           capture_output=True)
        elif sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/F", "/IM", app.win_image], capture_output=True)
    except Exception:
        pass


def close_one(app: reg.App) -> str:
    """返回 closed / not_running / failed。"""
    if not reg.app_running(app):
        _say(f"· {app.display} 未运行，跳过。")
        return "not_running"
    _say(f"⚠ 检测到 {app.display} 正在运行，即将自动关闭 {app.display}…")
    _graceful_quit(app)
    for _ in range(50):  # 最多等 ~5s 优雅退出
        if not reg.app_running(app):
            _say(f"✓ 已关闭 {app.display}（优雅退出）。")
            return "closed"
        time.sleep(0.1)
    if app.display in NO_FORCE_KILL:
        _say(f"✗ {app.display} 未响应退出（可能有未保存内容），请手动退出后再删除它的会话。")
        return "failed"
    _say(f"  {app.display} 未响应优雅退出，强制结束…")
    _force_kill(app, hard=False)
    time.sleep(0.5)
    if reg.app_running(app):
        _force_kill(app, hard=True)
        time.sleep(0.3)
    if reg.app_running(app):
        _say(f"✗ 无法关闭 {app.display}，请手动退出后重试。")
        return "failed"
    _say(f"✓ 已强制关闭 {app.display}。")
    return "closed"


def main() -> int:
    ap = argparse.ArgumentParser(description="关闭运行中会回写会话数据的 Agent App")
    ap.add_argument("--only", default="", help="只处理这些 agent key（逗号分隔）")
    ap.add_argument("--skip", default="", help="跳过这些 agent key（逗号分隔）")
    args = ap.parse_args()
    only = {k for k in args.only.split(",") if k}
    skip = {k for k in args.skip.split(",") if k}

    chain = reg.ancestor_commands()
    host = reg.host_info(chain)
    _say(f"当前宿主：{host['name']}。")
    if host["fit"] == "avoid":
        _say(f"⚠ 不建议在 {host['name']} 里运行本 skill：{host['reason']}。"
             f"推荐改在 {host['recommended_name']} 里运行。")

    closed, failed, kept = [], [], []
    for spec in reg.AGENTS:
        if not spec.apps or not reg.is_installed(spec):
            continue
        if (only and spec.key not in only) or spec.key in skip:
            continue
        for app in spec.apps:
            if spec.key == host["key"] or any(app.bundle_marker in cmd for cmd in chain):
                if reg.app_running(app):
                    _say(f"· {app.display} 是当前宿主，不关闭；本次 {spec.name} 的会话删除会被拒绝。")
                    kept.append(app.display)
                continue
            r = close_one(app)
            if r == "closed":
                closed.append(app.display)
            elif r == "failed":
                failed.append(app.display)
    if closed:
        _say("关闭完成：" + "、".join(closed) + "。")
    if not closed and not failed:
        _say("需要关闭的 App 均未运行（或为宿主），无需关闭。")
    if failed:
        _say("未能关闭：" + "、".join(failed) + "（对应 Agent 的删除会被拒绝）。")
    _say("✓ DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
