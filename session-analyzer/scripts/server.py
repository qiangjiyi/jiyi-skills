#!/usr/bin/env python3
"""Serve the session report with guarded per-agent delete (macOS + Windows).

Loads a read-only scan JSON, serves the interactive report on 127.0.0.1 + a
random port + a random per-session token, and exposes POST /action to delete a
single session or a whole project for Codex / Antigravity / Claude Code.

Usage:
    server.py <scan.json>

SAFETY MODEL — read before changing:
- The server only accepts (agent, scope, project_id, session_id) tuples that
  exist in THIS scan. Anything else is rejected; the client cannot name an
  arbitrary path or id. Each agent handler only ever touches that agent's own
  data dir (see agent_delete.py).
- Bound to 127.0.0.1 only; every POST needs the session token; Host header must
  be 127.0.0.1/localhost (blocks DNS-rebinding).
- Filesystem artifacts default to Trash (reversible). Codex SQLite rows / jsonl
  index lines are hard-removed (cannot be trashed) — surfaced in the UI.
- Stop with Ctrl+C; once stopped the delete buttons go dead.
"""
import atexit
import fcntl
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_delete  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "report_template.html")
TOKEN = secrets.token_urlsafe(24)

# 单实例约束：同一时刻本机只允许一个 session-analyzer server。
# 重启时主动 kill 旧实例，避免 (1) 浏览器叠多个相同标签页 (2) 旧 token 残留
# 导致"按钮看起来还能用"的安全口子。锁文件内容是 "pid:port"。
SINGLETON_LOCK_PATH = "/tmp/session-analyzer-server.lock"

DATA = {}
TPL = ""
INDEX = {}  # agent_key -> {project_id -> {"session_ids": set, "orphan_dir": bool}}


def build_index(data: dict) -> dict:
    index = {}
    for agent in data.get("agents", []):
        if not agent.get("installed"):
            continue
        projects = {}
        for p in agent.get("projects", []):
            sids = [s["id"] for s in p.get("sessions", [])]
            orphan_dir = any(
                s.get("extra", {}).get("claude_kind") == "orphan_dir"
                for s in p.get("sessions", [])
            )
            projects[p["id"]] = {"session_ids": sids, "orphan_dir": orphan_dir}
        index[agent["key"]] = {"projects": projects}
    return index


def codex_running() -> bool:
    """Best-effort: is the Codex desktop app running? Used only to strengthen a
    warning — a miss is harmless (the static note already advises closing it)."""
    try:
        if sys.platform == "darwin":
            return subprocess.run(["pgrep", "-x", "Codex"], capture_output=True).returncode == 0
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Codex.exe"],
                                 capture_output=True, text=True).stdout
            return "Codex.exe" in out
    except Exception:
        pass
    return False


def tab_already_open(url: str) -> bool:
    """探测 Safari / Chrome 是否已开过该 URL 的标签页。
    命中就不重复 webbrowser.open，避免多个 server 启动时叠标签页。
    macOS only（其它平台直接返回 False，让 webbrowser.open 走默认路径）。"""
    if sys.platform != "darwin":
        return False
    for app in ("Safari", "Google Chrome"):
        try:
            # 显式 return "true" / "false" 字符串，避免 AppleScript 在多 tab 时返回
            # 列表、单 tab 时返回带引号字符串字面量这两种不一致的输出形式。
            if app == "Safari":
                inner = '(URL of every tab of every window)'
            else:
                inner = '(URL of tabs of windows)'
            script = (
                f'tell application "{app}" to return (({inner}) as string) contains "{url}"'
            )
            out = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0 and out.stdout.strip().lower() == "true":
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            continue
    return False


def kill_old_server(pid: int, port: int) -> None:
    """SIGTERM → 0.5s → SIGKILL。等不到就强杀。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(10):  # 最多等 0.5s
        time.sleep(0.05)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def find_existing_servers() -> list:
    """扫 ps 找其它正在跑的 session-analyzer server 进程（不含自己）。
    用来对付「旧版本残留 / lock 文件丢失」的情况——锁文件只能挡住同版本重启，
    挡不住旧 server 没锁时仍占着端口。
    """
    script = os.path.abspath(__file__)
    my_pid = os.getpid()
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return []
    hits = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or script not in line:
            continue
        try:
            pid = int(line.split(None, 1)[0])
        except ValueError:
            continue
        if pid == my_pid:
            continue
        # 只针对同 Python 启动的 server.py，避免误伤同名的其它进程
        if "server.py" not in line:
            continue
        hits.append(pid)
    return hits


def load(src: str):
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    # 运行时检测 Codex 是否在跑，命中就把警告加强到 codex 分区的固定提示里
    if codex_running():
        for a in data.get("agents", []):
            if a.get("key") == "codex" and a.get("installed"):
                a["note"] = "🔴 检测到 Codex 正在运行，删除前请务必先退出 Codex App —— " + a.get("note", "")
    return data, tpl, build_index(data)


def dispatch(agent: str, scope: str, project_id: str, session_id, mode: str) -> dict:
    a = INDEX.get(agent)
    if not a:
        raise ValueError("未知或未安装的 Agent：%s" % agent)
    proj = a["projects"].get(project_id)
    if proj is None:
        raise ValueError("项目不在本次扫描内")
    if scope == "session":
        if session_id not in proj["session_ids"]:
            raise ValueError("会话不在本次扫描内")
        ids = [session_id]
    elif scope == "project":
        ids = list(proj["session_ids"])
    else:
        raise ValueError("未知操作范围：%s" % scope)

    if agent == "claude":
        if proj["orphan_dir"]:
            return agent_delete.delete_claude_orphan_dir(project_id, mode)
        return agent_delete.delete_claude_sessions(project_id, ids, mode)
    if agent == "antigravity":
        return agent_delete.delete_antigravity_sessions(ids, mode)
    if agent == "codex":
        return agent_delete.delete_codex_threads(ids, mode)
    raise ValueError("未知 Agent：%s" % agent)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            blob = json.dumps(DATA, ensure_ascii=False)
            cfg = json.dumps({"token": TOKEN, "endpoint": "/action", "enabled": True})
            html = TPL.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", cfg)
            self._send(200, html, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/action":
            self._send(404, json.dumps({"ok": False, "error": "not found"}))
            return
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, json.dumps({"ok": False, "error": "host 不被允许"}))
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "请求格式错误"}))
            return
        if req.get("token") != TOKEN:
            self._send(403, json.dumps({"ok": False, "error": "token 校验失败"}))
            return
        mode = req.get("mode") if req.get("mode") in ("trash", "rm") else "trash"
        try:
            result = dispatch(
                req.get("agent"), req.get("scope"),
                req.get("project_id"), req.get("session_id"), mode,
            )
        except ValueError as e:
            self._send(403, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return
        result["ok"] = True
        self._send(200, json.dumps(result, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    global DATA, TPL, INDEX
    DATA, TPL, INDEX = load(sys.argv[1])

    # 单实例约束：先 ps 扫一遍同 path 的其它 server 进程，发现就 kill 掉。
    # 这比单纯 flock 更稳——旧版本残留、lock 文件丢失都不会绕过这一道。
    existing = find_existing_servers()
    if existing:
        print(f"检测到已有 session-analyzer server 在跑（PID {existing}），正在停止旧实例…")
        for old_pid in existing:
            kill_old_server(old_pid, 0)
        time.sleep(0.3)  # 等旧进程把端口让出来

    # 再抢文件锁做最后一道防线（同版本反复启时挡住自己）
    lock_fd = open(SINGLETON_LOCK_PATH, "w+")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # 同版本快速重启场景——读 lock 文件拿旧 pid 杀掉
        lock_fd.seek(0)
        old = lock_fd.read().strip()
        lock_fd.close()
        try:
            old_pid, _ = old.split(":")
            kill_old_server(int(old_pid), 0)
        except (ValueError, AttributeError):
            pass
        lock_fd = open(SINGLETON_LOCK_PATH, "w+")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/" % port
    # 拿到端口后立刻把 pid:port 写进锁文件，供下次启动时 kill 旧实例用
    lock_fd.truncate(0)
    lock_fd.write(f"{os.getpid()}:{port}")
    lock_fd.flush()

    total = sum(a.get("session_count", 0) for a in DATA.get("agents", []))
    print("会话分析报告服务已启动：" + url)
    print("共 %d 个会话，跨 %d 个 Agent。页面上可一键删除（默认移废纸篓）。" % (
        total, sum(1 for a in DATA.get("agents", []) if a.get("installed"))))
    print("用完按 Ctrl+C 停止服务（服务关掉后按钮即失效）")
    if tab_already_open(url):
        print("检测到浏览器已打开该 URL，复用现有标签页，不重复开。")
    else:
        webbrowser.open(url)

    # 锁文件清理：直接挂在 signal handler 内 + atexit 兜底。
    # 注意：ThreadingHTTPServer.serve_forever() 在 select() 阻塞时，Python 主线程
    # 的 signal handler 不会立即触发，所以单靠 srv.shutdown() 经常不能让它退出。
    # 最稳的清理路径是 signal handler 内先 unlink 再 os._exit(0)。
    def _cleanup_lock():
        try:
            os.unlink(SINGLETON_LOCK_PATH)
        except FileNotFoundError:
            pass
    atexit.register(_cleanup_lock)

    def _term(_signo, _frame):
        _cleanup_lock()
        try:
            srv.server_close()
        except Exception:
            pass
        os._exit(0)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")
    _cleanup_lock()


if __name__ == "__main__":
    main()
