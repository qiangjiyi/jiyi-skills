"""新增 Agent 插件（注册表 / WorkBuddy / Cursor / Gemini CLI / 云端型）回归测试。全部基于临时目录夹具。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_registry as reg  # noqa: E402
import agents_cloud  # noqa: E402
import agents_cursor  # noqa: E402
import agents_gemini  # noqa: E402
import agents_workbuddy as wb  # noqa: E402

SID_A = "11111111-1111-4111-8111-111111111111"
SID_B = "22222222-2222-4222-8222-222222222222"
SUB = "33333333-3333-4333-8333-333333333333"


class RegistryTests(unittest.TestCase):
    def test_host_detection_nearest_wins(self):
        chain = ["python3 scan.py", "/bin/zsh -c x",
                 "/Applications/ZCode.app/Contents/Frameworks/zcode-cli", "/sbin/launchd"]
        self.assertEqual(reg.detect_host(chain), "zcode")
        self.assertEqual(reg.host_info(chain)["fit"], "recommended")

    def test_codex_inside_chatgpt_is_avoid(self):
        chain = ["bash", "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"]
        info = reg.host_info(chain)
        self.assertEqual(info["key"], "codex")
        self.assertEqual(info["fit"], "avoid")

    def test_unknown_host(self):
        self.assertIsNone(reg.detect_host(["/bin/zsh", "/sbin/launchd"]))
        self.assertEqual(reg.host_info(["/bin/zsh"])["fit"], "ok")

    def test_require_closed_rejects_running_and_unknown(self):
        with mock.patch.object(reg, "running_apps", return_value=["WorkBuddy"]):
            with self.assertRaises(ValueError):
                reg.require_closed("workbuddy")
        with mock.patch.object(reg, "running_apps", return_value=None):
            with self.assertRaises(ValueError):
                reg.require_closed("workbuddy")
        with mock.patch.object(reg, "running_apps", return_value=[]):
            reg.require_closed("workbuddy")

    def test_app_running_matches_bundle_marker(self):
        table = {1: (0, "/Applications/WorkBuddy.app/Contents/MacOS/Electron")}
        self.assertTrue(reg.app_running(reg.WORKBUDDY, table))
        self.assertFalse(reg.app_running(reg.CURSOR, table))

    def test_every_agent_has_meta(self):
        for spec in reg.AGENTS:
            m = reg.meta_for(spec.key)
            self.assertIn(m["kind"], ("local", "cloud"))
            self.assertIn(spec.host_fit, ("recommended", "ok", "avoid"))


def _make_workbuddy(root: Path, home: Path) -> Path:
    tmpwd = home / "WorkBuddy" / "2026-11-07-19-07-09"
    (tmpwd / ".workbuddy" / "memory").mkdir(parents=True)
    (tmpwd / ".workbuddy" / "memory" / "notes.md").write_text("keep me")
    (tmpwd / "output.txt").write_text("artifact")
    (tmpwd / ".workbuddy" / "cache.json").write_text("{}")
    enc = root / "projects" / "Users-x-WorkBuddy-2026-11-07-19-07-09"
    enc.mkdir(parents=True)
    for sid, cwd in ((SID_A, str(tmpwd)), (SID_B, "/nonexistent/proj")):
        (enc / f"{sid}.jsonl").write_text(json.dumps({"type": "message", "cwd": cwd}) + "\n")
        (enc / f"{sid}.file-rollback.ndjson").write_text("")
        (enc / sid / "tool-results").mkdir(parents=True)
        (root / "file-history" / sid).mkdir(parents=True)
        (root / "workspace" / "sessions" / sid).mkdir(parents=True)
    (root / "artifact-index").mkdir()
    (root / "artifact-index" / f"{SID_A}.json").write_text("{}")
    (root / "memory").mkdir()
    (root / "memory" / "u_memory.md").write_text("global memory")
    (root / "SOUL.md").write_text("soul")
    uid = root / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    uid.mkdir()
    (uid / "sidebar-list-snapshot.json").write_text(json.dumps(
        {"version": 3, "items": [{"id": SID_A}, {"id": SID_B}]}))
    (root / "app").mkdir()
    (root / "app" / "sessions.json").write_text(json.dumps(
        {"sessions": [{"conversationId": SID_A}, {"conversationId": SID_B}]}, indent=2))
    usage = root / "storage" / "user-x" / "scoped" / "abc"
    usage.mkdir(parents=True)
    (usage / "conversationContextUsage.json").write_text(json.dumps({SID_A: {}, SID_B: {}}))
    con = sqlite3.connect(root / "workbuddy.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, custom_title TEXT,"
                " status TEXT, created_at INT, updated_at INT, last_activity_at INT, deleted_at INT,"
                " transport TEXT DEFAULT 'local')")
    con.execute("CREATE TABLE session_usage (session_id TEXT PRIMARY KEY, used INT)")
    con.execute("CREATE TABLE session_turn_interruptions (session_id TEXT, request_id TEXT)")
    old = int(time.time() * 1000) - 3600_000
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (SID_A, str(tmpwd), "临时任务", None, "completed", old, old, old, None, "local"))
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (SID_B, "/nonexistent/proj", "另一个", None, "completed", old, old, old, None, "local"))
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("cloud-1", "/x", "云端", None, "completed", old, old, old, -1, "cloud"))
    con.execute("INSERT INTO session_usage VALUES (?,1)", (SID_A,))
    con.commit()
    con.close()
    return tmpwd


class WorkBuddyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = self.home / ".workbuddy"
        self.root.mkdir()
        self.p = mock.patch.object(wb, "TEMP_WORKDIR_PARENT", self.home / "WorkBuddy")
        self.p.start()
        self.tmpwd = _make_workbuddy(self.root, self.home)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_scan_groups_by_cwd_and_skips_cloud(self):
        a = wb.scan(self.root)
        self.assertEqual(a["session_count"], 2)
        self.assertIn("1 个云端会话", a["note"])
        orphan = [p for p in a["projects"] if p["orphan"]]
        self.assertEqual([p["id"] for p in orphan], ["/nonexistent/proj"])
        temp = [s for p in a["projects"] for s in p["sessions"] if s["extra"]["temp_workdir"]]
        self.assertEqual([s["id"] for s in temp], [SID_A])

    def test_delete_full_removal_set_keeps_memory(self):
        r = wb.delete([SID_A], "rm", self.root, gate=False)
        self.assertEqual(r["errors"], [])
        self.assertFalse(list(self.root.glob(f"projects/*/{SID_A}*")))
        self.assertFalse((self.root / "file-history" / SID_A).exists())
        self.assertFalse((self.root / "artifact-index" / f"{SID_A}.json").exists())
        self.assertTrue((self.root / "file-history" / SID_B).exists())
        con = sqlite3.connect(self.root / "workbuddy.db")
        ids = {x[0] for x in con.execute("SELECT id FROM sessions")}
        self.assertEqual(ids, {SID_B, "cloud-1"})
        self.assertEqual(con.execute("SELECT count(*) FROM session_usage").fetchone()[0], 0)
        con.close()
        snap = json.loads(next(self.root.glob("*/sidebar-list-snapshot.json")).read_text())
        self.assertEqual([i["id"] for i in snap["items"]], [SID_B])
        app = json.loads((self.root / "app" / "sessions.json").read_text())
        self.assertEqual([s["conversationId"] for s in app["sessions"]], [SID_B])
        usage = json.loads(next(self.root.rglob("conversationContextUsage.json")).read_text())
        self.assertEqual(list(usage), [SID_B])
        # 临时工作目录：产物清掉，非空 memory 保留
        self.assertFalse((self.tmpwd / "output.txt").exists())
        self.assertFalse((self.tmpwd / ".workbuddy" / "cache.json").exists())
        self.assertEqual((self.tmpwd / ".workbuddy" / "memory" / "notes.md").read_text(), "keep me")
        # 全局记忆永不触碰
        self.assertTrue((self.root / "memory" / "u_memory.md").exists())
        self.assertTrue((self.root / "SOUL.md").exists())

    def test_temp_workdir_without_memory_is_removed(self):
        (self.tmpwd / ".workbuddy" / "memory" / "notes.md").unlink()
        wb.delete([SID_A], "rm", self.root, gate=False)
        self.assertFalse(self.tmpwd.exists())

    def test_rejects_non_uuid_and_gate(self):
        r = wb.delete(["../../etc"], "rm", self.root, gate=False)
        self.assertEqual(r["removed"], [])
        with mock.patch.object(reg, "running_apps", return_value=["WorkBuddy"]):
            with self.assertRaises(ValueError):
                wb.delete([SID_A], "rm", self.root)
        self.assertTrue((self.root / "file-history" / SID_A).exists())

    def test_prune_orphans_only_dead_satellites(self):
        dead = "44444444-4444-4444-8444-444444444444"
        (self.root / "file-history" / dead).mkdir()
        removed: list = []
        with mock.patch.object(reg, "running_apps", return_value=[]):
            hits = wb.prune_orphans("rm", removed, self.root)
        self.assertEqual(hits, [str(self.root / "file-history" / dead)])
        self.assertTrue((self.root / "file-history" / SID_A).exists())


def _make_cursor(dot: Path, user: Path):
    gs = user / "globalStorage"
    gs.mkdir(parents=True)
    con = sqlite3.connect(gs / "state.vscdb")
    con.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    con.execute("CREATE TABLE composerHeaders (composerId TEXT PRIMARY KEY, workspaceId TEXT,"
                " createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER,"
                " recency INTEGER, checkpointAt INTEGER, subagentTypeName TEXT, value TEXT)")
    for cid, sub, name in ((SID_A, 0, "主会话"), (SUB, 1, "子代理"), (SID_B, 0, "保留"),
                           ("empty-state-draft", 0, "")):
        con.execute("INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid, "ws1", 1, 2, 0, sub, 0, 0, None, json.dumps({"name": name})))
    con.execute("INSERT INTO cursorDiskKV VALUES (?,?)",
                (f"composerData:{SID_A}", json.dumps({"subagentComposerIds": [SUB]})))
    for k in (f"bubbleId:{SID_A}:b1", f"checkpointId:{SID_A}:c1", f"composerData:{SUB}",
              f"bubbleId:{SUB}:b2", f"composerData:{SID_B}", f"bubbleId:{SID_B}:b3",
              "agentKv:blob:abc", f"composerConvHeaders:{SID_A}:000000"):
        con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", (k, "x" * 10))
    con.commit()
    con.close()
    s = sqlite3.connect(gs / "conversation-search.db")
    s.execute("CREATE VIRTUAL TABLE conversation_fts USING fts5(title, body, branches)")
    s.execute("CREATE TABLE conversations (fts_rowid INTEGER PRIMARY KEY, id TEXT, title TEXT)")
    s.execute("CREATE TABLE conversation_search_candidates (id TEXT PRIMARY KEY, updated_at INT)")
    for rowid, cid in ((1, SID_A), (2, SID_B)):
        s.execute("INSERT INTO conversation_fts(rowid, title, body, branches) VALUES (?,?,?,?)", (rowid, cid, "", ""))
        s.execute("INSERT INTO conversations VALUES (?,?,?)", (rowid, cid, cid))
        s.execute("INSERT INTO conversation_search_candidates VALUES (?,1)", (cid,))
    s.commit()
    s.close()
    ws = user / "workspaceStorage" / "ws1"
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text(json.dumps({"folder": "file:///nonexistent/folder"}))
    t = dot / "projects" / "enc" / "agent-transcripts" / SID_A
    t.mkdir(parents=True)
    (t / f"{SID_A}.jsonl").write_text(json.dumps(
        {"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>你好 cursor</user_query>"}]}}) + "\n")
    (dot / "projects" / "enc" / "mcp-auth.json").write_text("{}")
    chat = dot / "chats" / "hash1" / SID_B
    chat.mkdir(parents=True)
    (chat / "store.db").write_text("")
    (chat / "meta.json").write_text(json.dumps({"cwd": "/nonexistent/cli", "updatedAtMs": 1000}))


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.dot, self.user = base / ".cursor", base / "User"
        _make_cursor(self.dot, self.user)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_ide_and_cli(self):
        a = agents_cursor.scan(self.dot, self.user)
        ide = next(p for p in a["projects"] if p["id"] == "ide:ws1")
        self.assertEqual({s["id"] for s in ide["sessions"]}, {SID_A, SID_B})  # 子代理 / 占位不单列
        self.assertTrue(ide["orphan"])
        cli = next(p for p in a["projects"] if p["id"].startswith("cli:"))
        self.assertEqual([s["id"] for s in cli["sessions"]], [SID_B])
        snip = next(s for s in ide["sessions"] if s["id"] == SID_A)["snippet"]
        self.assertEqual(snip, "你好 cursor")

    def test_delete_ide_cascades_subagent_and_index(self):
        r = agents_cursor.delete("ide:ws1", [SID_A], "rm", self.dot, self.user, gate=False)
        self.assertEqual(r["errors"], [])
        con = sqlite3.connect(self.user / "globalStorage" / "state.vscdb")
        keys = {k for (k,) in con.execute("SELECT key FROM cursorDiskKV")}
        self.assertEqual(keys, {f"composerData:{SID_B}", f"bubbleId:{SID_B}:b3", "agentKv:blob:abc"})
        hdr = {k for (k,) in con.execute("SELECT composerId FROM composerHeaders")}
        self.assertEqual(hdr, {SID_B, "empty-state-draft"})
        con.close()
        s = sqlite3.connect(self.user / "globalStorage" / "conversation-search.db")
        self.assertEqual([x[0] for x in s.execute("SELECT id FROM conversations")], [SID_B])
        self.assertEqual(s.execute("SELECT count(*) FROM conversation_fts").fetchone()[0], 1)
        s.close()
        self.assertFalse((self.dot / "projects" / "enc" / "agent-transcripts").exists())
        self.assertTrue((self.dot / "projects" / "enc" / "mcp-auth.json").exists())

    def test_ide_gate(self):
        with mock.patch.object(reg, "running_apps", return_value=["Cursor"]):
            with self.assertRaises(ValueError):
                agents_cursor.delete("ide:ws1", [SID_A], "rm", self.dot, self.user)

    def test_delete_cli_and_live_guard(self):
        agents_cursor.delete("cli:/nonexistent/cli", [SID_B], "rm", self.dot, self.user)
        self.assertFalse((self.dot / "chats" / "hash1").exists())
        chat = self.dot / "chats" / "h2" / SID_A
        chat.mkdir(parents=True)
        (chat / "meta.json").write_text(json.dumps({"updatedAtMs": int(time.time() * 1000)}))
        with self.assertRaises(ValueError):
            agents_cursor.delete("cli:x", [SID_A], "rm", self.dot, self.user)
        self.assertTrue(chat.exists())


class GeminiTests(unittest.TestCase):
    def test_scan_and_delete(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            chats = root / "tmp" / "abc123" / "chats"
            chats.mkdir(parents=True)
            (chats / "session-2026-11-01-x1.json").write_text(json.dumps(
                {"messages": [{"type": "user", "content": "hello gemini"}]}))
            (root / "projects.json").write_text(json.dumps({"projects": {"/nonexistent/g": "abc123"}}))
            (root / "GEMINI.md").write_text("memory")
            a = agents_gemini.scan(root)
            self.assertEqual(a["session_count"], 1)
            self.assertTrue(a["projects"][0]["orphan"])
            self.assertEqual(a["projects"][0]["sessions"][0]["title"], "hello gemini")
            agents_gemini.delete("abc123", ["session-2026-11-01-x1", "../../GEMINI"], "rm", root)
            self.assertFalse(list(chats.iterdir()))
            self.assertTrue((root / "GEMINI.md").exists())
            with self.assertRaises(ValueError):
                agents_gemini.delete("../..", ["x"], "rm", root)


class CloudTests(unittest.TestCase):
    def test_doubao_targets_and_delete(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            wd = home / ".doubao" / "chats" / "2026-06-27" / "new-chat"
            wd.mkdir(parents=True)
            (wd / "a.txt").write_text("x")
            log = home / "Library" / "Application Support" / "Doubao" / "sdk_storage" / "log"
            log.mkdir(parents=True)
            (log / "x.log").write_text("log")
            im = log.parent / "uid123" / "im.db"
            im.parent.mkdir()
            im.write_text("encrypted")
            ids = {x["id"] for x in agents_cloud._targets("doubao", home)}
            self.assertEqual(ids, {"chats:2026-06-27/new-chat", "cache:log"})
            agents_cloud.delete("doubao", ["cache:log", "chats:2026-06-27/new-chat", "/etc/passwd"],
                                "rm", home, gate=False)
            self.assertTrue(log.is_dir())          # 清空内容、保留目录
            self.assertFalse(any(log.iterdir()))
            self.assertFalse(wd.exists())
            self.assertFalse((home / ".doubao" / "chats" / "2026-06-27").exists())
            self.assertTrue(im.exists())            # 加密消息库不碰

    def test_cloud_gate(self):
        with mock.patch.object(reg, "running_apps", return_value=["Grok Bot"]):
            with self.assertRaises(ValueError):
                agents_cloud.delete("grok", ["cache:attach"], "rm")


if __name__ == "__main__":
    unittest.main()


class ServerDispatchTests(unittest.TestCase):
    def test_new_agents_routed_and_index_enforced(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sa_server_dispatch", SCRIPTS / "server.py")
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        data = {"agents": [
            {"key": "workbuddy", "installed": True, "projects": [
                {"id": "/p", "sessions": [{"id": SID_A, "extra": {}}]}]},
            {"key": "cursor", "installed": True, "projects": [
                {"id": "ide:ws1", "sessions": [{"id": SID_B, "extra": {}}]}]},
            {"key": "doubao", "installed": True, "projects": [
                {"id": "cache", "sessions": [{"id": "cache:log", "extra": {}}]}]},
        ]}
        server.INDEX = server.build_index(data)
        with mock.patch.object(server.agents_workbuddy, "delete", return_value={"removed": []}) as m:
            server.dispatch("workbuddy", "project", "/p", None, "trash")
            m.assert_called_once_with([SID_A], "trash")
        with mock.patch.object(server.agents_cursor, "delete", return_value={"removed": []}) as m:
            server.dispatch("cursor", "session", "ide:ws1", SID_B, "trash")
            m.assert_called_once_with("ide:ws1", [SID_B], "trash")
        with mock.patch.object(server.agents_cloud, "delete", return_value={"removed": []}) as m:
            server.dispatch("doubao", "session", "cache", "cache:log", "trash")
            m.assert_called_once_with("doubao", ["cache:log"], "trash")
        with self.assertRaises(ValueError):
            server.dispatch("cursor", "session", "ide:ws1", SID_A, "trash")  # 不在扫描内
        with self.assertRaises(ValueError):
            server.dispatch("gemini", "session", "x", "y", "trash")          # 未安装/未扫描
