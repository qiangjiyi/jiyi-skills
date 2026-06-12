#!/usr/bin/env python3
"""删除前关闭 Codex / Antigravity。

两者删除都要求 app 关闭：Codex 删到正在打开的活跃线程会损坏状态；Antigravity 运行时会把
内存里的侧栏列表回写覆盖索引、导致删除不生效。本脚本先尝试优雅退出（让 app 自行存盘），
等不到再强制结束。设计成 skill 流程的 Step 0：先关，再扫描/删除。

仅关闭这两个 app，不动其它任何进程。打印关了哪些，供 agent 据实告知用户。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

# (展示名 = macOS 上 osascript 的 application 名 = pgrep -x 的进程名, Windows 进程名)
APPS = [
    ("Codex", "Codex.exe"),
    ("Antigravity", "Antigravity.exe"),
]


def app_is_running(mac_name: str, win_name: str) -> bool:
    """Best-effort: is the named desktop app running on this OS?
    macOS → pgrep -x NAME. Windows → tasklist. Else False.
    Miss is harmless — used only to decide whether to attempt close."""
    try:
        if sys.platform == "darwin":
            return subprocess.run(
                ["pgrep", "-x", mac_name], capture_output=True
            ).returncode == 0
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {win_name}"],
                capture_output=True, text=True,
            )
            return win_name.lower() in out.stdout.lower()
    except Exception:
        pass
    return False


def _graceful_quit(mac_name: str, win_name: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'tell application "{mac_name}" to quit'],
                           capture_output=True, timeout=5)
        elif sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/IM", win_name], capture_output=True)
    except Exception:
        pass


def _force_kill(mac_name: str, win_name: str, hard: bool) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["pkill"] + (["-9"] if hard else []) + ["-x", mac_name],
                           capture_output=True)
        elif sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/F", "/IM", win_name], capture_output=True)
    except Exception:
        pass


def _say(msg: str) -> None:
    print(msg, flush=True)  # 强制 flush：后台/管道里也能被 agent 实时读到


def close_one(disp: str, win_name: str) -> bool:
    """返回是否进行了关闭（原本就没运行返回 False）。逐步打印检测与关闭过程。"""
    if not app_is_running(disp, win_name):
        _say(f"· {disp} 未运行，跳过。")
        return False
    _say(f"⚠ 检测到 {disp} 正在运行，即将自动关闭 {disp}…")
    _graceful_quit(disp, win_name)
    for _ in range(30):  # 最多等 ~3s 优雅退出
        if not app_is_running(disp, win_name):
            _say(f"✓ 已关闭 {disp}（优雅退出）。")
            return True
        time.sleep(0.1)
    _say(f"  {disp} 未响应优雅退出，强制结束…")
    _force_kill(disp, win_name, hard=False)  # SIGTERM
    time.sleep(0.4)
    if app_is_running(disp, win_name):
        _force_kill(disp, win_name, hard=True)  # SIGKILL
        time.sleep(0.3)
    if app_is_running(disp, win_name):
        _say(f"✗ 无法关闭 {disp}，请手动退出后重试。")
        return False
    _say(f"✓ 已强制关闭 {disp}。")
    return True


def main() -> int:
    _say("检查 Codex / Antigravity 运行状态…")
    closed = [disp for disp, win in APPS if close_one(disp, win)]
    if closed:
        _say("关闭完成：" + "、".join(closed) + "。可以继续扫描/删除了。")
    else:
        _say("Codex / Antigravity 均未运行，无需关闭。")
    _say("✓ DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
