from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_ui_state  # noqa: E402

SPEC = importlib.util.spec_from_file_location("session_analyzer_agent_delete", SCRIPTS / "agent_delete.py")
agent_delete = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(agent_delete)


# 固定 uuid：G*=幽灵（不在 threads 表）、A*=存活、N*=非 thread 的干扰 uuid
G1 = "019e0001-1111-7111-8111-000000000001"
G2 = "019e0002-2222-7222-8222-000000000002"
A1 = "019e000a-aaaa-7aaa-8aaa-00000000000a"
N1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"  # 干扰：结构上像 thread id，但属于别的 key
N2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def dumps(obj) -> bytes:
    """Electron JSON.stringify 等价的紧凑序列化（本机实测与真实状态文件 round-trip 恒等）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fixture_state() -> dict:
    """覆盖：幽灵（四个白名单 key 各处）、存活、非 thread 干扰 uuid（只出现在非白名单
    key——对齐真实文件里约 197 个干扰 uuid 的分布）、非白名单 key、缺失 key 变体。
    注意 G1/G2 也被刻意放进非白名单 key（thread-project-assignments /
    thread-descriptions-v1）：剪除只许动白名单，非白名单里的同 id 条目必须原样保留。"""
    return {
        "projectless-thread-ids": [G1, A1, "not-a-uuid", 42, None],
        "pinned-thread-ids": [G2],
        "thread-workspace-root-hints": {G1: "/tmp/ws", A1: "/tmp/alive"},
        "thread-titles": {"titles": {G1: "幽灵标题", A1: "存活标题"}, "order": [G1, A1, G2]},
        # 非白名单：即使含幽灵/干扰 uuid 也绝不能被碰
        "thread-project-assignments": {N1: "p1", G1: "p2"},
        "electron-saved-workspace-roots": ["/Users/x/SomeProject"],
        "thread-descriptions-v1": {N2: "desc", G2: "desc2"},
        "unrelated": {"keep": [1, 2, 3]},
    }


LIVE = {A1}  # threads 表只有 A1 存活 → G1/G2 是幽灵，N1/N2 与 threads 判定无关


class NamespaceTest(unittest.TestCase):
    def test_namespace_collection_is_defensive(self):
        found = codex_ui_state.namespace_thread_ids(fixture_state())
        four = {"projectless-thread-ids", "thread-workspace-root-hints",
                "thread-titles.titles", "thread-titles.order"}
        self.assertEqual(found[G1], four)
        self.assertEqual(found[G2], {"pinned-thread-ids", "thread-titles.order"})
        self.assertEqual(found[A1], four)
        # 非 uuid 哨兵也会被收集进命名空间，但 ghost 判定有形态守卫（见下）
        self.assertEqual(found["not-a-uuid"], {"projectless-thread-ids"})
        # 干扰 uuid 不在白名单 key 里，不参与收集
        self.assertNotIn(N1, found)
        self.assertNotIn(N2, found)

    def test_uuid_shape_guard_blocks_non_uuid_ghosts(self):
        data = {"projectless-thread-ids": ["not-a-uuid", G1]}
        ghosts = codex_ui_state.ghost_entries(data, set())
        self.assertEqual([g["id"] for g in ghosts], [G1])

    def test_missing_and_malformed_keys_are_skipped(self):
        data = {
            "thread-titles": "not-a-dict",                 # 整个 key 类型变体
            "thread-titles.order": None,                    # 干扰项：不是真实结构
            "pinned-thread-ids": {"not": "a list"},         # 类型变体
            "thread-workspace-root-hints": [G1],            # 该是 dict 却是 list
            "projectless-thread-ids": [G1],
        }
        found = codex_ui_state.namespace_thread_ids(data)
        self.assertEqual(found, {G1: {"projectless-thread-ids"}})

    def test_thread_titles_missing_entirely(self):
        # 2026-08-31 实测：真实文件可能没有 thread-titles key
        data = {"projectless-thread-ids": [G1]}
        self.assertEqual(codex_ui_state.namespace_thread_ids(data),
                         {G1: {"projectless-thread-ids"}})
        self.assertIsNone(codex_ui_state.title_of(data, G1))

    def test_ghost_entries_excludes_live(self):
        ghosts = codex_ui_state.ghost_entries(fixture_state(), LIVE)
        ids = {g["id"] for g in ghosts}
        self.assertEqual(ids, {G1, G2})
        g1 = next(g for g in ghosts if g["id"] == G1)
        self.assertEqual(g1["title"], "幽灵标题")


class PruneInMemoryTest(unittest.TestCase):
    def test_prune_removes_only_dropped(self):
        data = fixture_state()
        removed = codex_ui_state.prune(data, {G1, G2})
        # 命中计数
        self.assertEqual(removed, {
            "projectless-thread-ids": 1,
            "pinned-thread-ids": 1,
            "thread-workspace-root-hints": 1,
            "thread-titles.titles": 1,
            "thread-titles.order": 2,
        })
        # 白名单内：G 全清、A 与非 uuid 哨兵保留
        self.assertEqual(data["projectless-thread-ids"], [A1, "not-a-uuid", 42, None])
        self.assertEqual(data["pinned-thread-ids"], [])
        self.assertEqual(data["thread-workspace-root-hints"], {A1: "/tmp/alive"})
        self.assertEqual(data["thread-titles"], {"titles": {A1: "存活标题"}, "order": [A1]})
        # 白名单外：同 id 的条目也一个字节都不动
        self.assertEqual(data["thread-project-assignments"], {N1: "p1", G1: "p2"})
        self.assertEqual(data["thread-descriptions-v1"], {N2: "desc", G2: "desc2"})
        self.assertEqual(data["unrelated"], {"keep": [1, 2, 3]})
        self.assertEqual(data["electron-saved-workspace-roots"], ["/Users/x/SomeProject"])

    def test_prune_is_idempotent(self):
        data = fixture_state()
        codex_ui_state.prune(data, {G1, G2})
        again = codex_ui_state.prune(data, {G1, G2})
        self.assertEqual(again, {})

    def test_prune_with_malformed_keys_does_not_raise(self):
        data = {"projectless-thread-ids": "bad", "thread-titles": None}
        self.assertEqual(codex_ui_state.prune(data, {G1}), {})


class CanonicalTest(unittest.TestCase):
    def test_round_trip_identity(self):
        raw = dumps(fixture_state())
        self.assertTrue(codex_ui_state.is_canonical(raw))
        data = codex_ui_state.parse_state(raw)
        self.assertEqual(codex_ui_state.canonical_bytes(data), raw)

    def test_pretty_json_is_not_canonical(self):
        raw = json.dumps(fixture_state(), ensure_ascii=False, indent=2).encode("utf-8")
        self.assertFalse(codex_ui_state.is_canonical(raw))

    def test_duplicate_keys_rejected(self):
        raw = b'{"a":1,"a":2}'
        with self.assertRaises(codex_ui_state.CleanupRejected):
            codex_ui_state.parse_state(raw)

    def test_top_level_non_dict_rejected(self):
        with self.assertRaises(codex_ui_state.CleanupRejected):
            codex_ui_state.parse_state(b"[1,2]")


def write_state(tmp: Path, raw: bytes, mode: int = 0o644) -> Path:
    p = tmp / ".codex-global-state.json"
    p.write_bytes(raw)
    os.chmod(p, mode)
    return p


@unittest.mock.patch.object(codex_ui_state, "app_running", return_value=False)
class PruneFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.backup = self.tmp / codex_ui_state.BACKUP_SUFFIX.replace(".", "", 0) if False else \
            self.tmp / (".codex-global-state.json" + codex_ui_state.BACKUP_SUFFIX)

    def raw_state(self) -> bytes:
        return dumps(fixture_state())

    def test_prunes_exactly_and_preserves_rest_byte_for_byte(self, _m):
        p = write_state(self.tmp, self.raw_state())
        result = codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(result["removed_total"], 6)
        self.assertTrue(result["backup_created"])
        after = p.read_bytes()
        # 逐字节期望：手写的「同一序列化规范下去掉 G1/G2 条目」的字节串，
        # 不经过被测代码（防循环验证）。key 顺序 = fixture 插入顺序的残留。
        expected = (
            '{"projectless-thread-ids":["%s","not-a-uuid",42,null],'
            '"pinned-thread-ids":[],'
            '"thread-workspace-root-hints":{"%s":"/tmp/alive"},'
            '"thread-titles":{"titles":{"%s":"存活标题"},"order":["%s"]},'
            '"thread-project-assignments":{"%s":"p1","%s":"p2"},'
            '"electron-saved-workspace-roots":["/Users/x/SomeProject"],'
            '"thread-descriptions-v1":{"%s":"desc","%s":"desc2"},'
            '"unrelated":{"keep":[1,2,3]}}'
        ) % (A1, A1, A1, A1, N1, G1, N2, G2)
        self.assertEqual(after, expected.encode("utf-8"))
        # 抽查解析后内容
        obj = json.loads(after)
        self.assertEqual(obj["thread-project-assignments"], {N1: "p1", G1: "p2"})
        self.assertEqual(obj["unrelated"], {"keep": [1, 2, 3]})

    def test_backup_is_0600_single_copy_of_original(self, _m):
        p = write_state(self.tmp, self.raw_state())
        codex_ui_state.prune_ui_state_file(p, {G1, G2})
        b = self.tmp / (".codex-global-state.json.session-analyzer.bak")
        self.assertTrue(b.exists())
        self.assertEqual(b.read_bytes(), self.raw_state())  # 备份 = 改前原文
        self.assertEqual(b.stat().st_mode & 0o777, 0o600)

    def test_file_mode_preserved(self, _m):
        p = write_state(self.tmp, self.raw_state(), mode=0o644)
        codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(p.stat().st_mode & 0o777, 0o644)

    def test_second_run_is_noop(self, _m):
        p = write_state(self.tmp, self.raw_state())
        first = codex_ui_state.prune_ui_state_file(p, {G1, G2})
        snapshot = p.read_bytes()
        second = codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertGreater(first["removed_total"], 0)
        self.assertEqual(second["removed_total"], 0)
        self.assertFalse(second["backup_created"])  # no-op 不产生新备份
        self.assertEqual(p.read_bytes(), snapshot)

    def test_refuses_when_app_running(self, _m):
        p = write_state(self.tmp, self.raw_state())
        with unittest.mock.patch.object(codex_ui_state, "app_running", return_value=True):
            with self.assertRaises(codex_ui_state.CleanupRejected) as cm:
                codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(cm.exception.category, "app_running")
        self.assertEqual(p.read_bytes(), self.raw_state())  # 文件未被改动

    def test_refuses_when_detection_unavailable(self, _m):
        p = write_state(self.tmp, self.raw_state())
        with unittest.mock.patch.object(codex_ui_state, "app_running", return_value=None):
            with self.assertRaises(codex_ui_state.CleanupRejected) as cm:
                codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(cm.exception.category, "app_running")
        self.assertEqual(p.read_bytes(), self.raw_state())

    def test_refuses_non_canonical_file(self, _m):
        pretty = json.dumps(fixture_state(), ensure_ascii=False, indent=2).encode("utf-8")
        p = write_state(self.tmp, pretty)
        with self.assertRaises(codex_ui_state.CleanupRejected) as cm:
            codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(cm.exception.category, "ui_state_not_canonical")
        self.assertEqual(p.read_bytes(), pretty)

    def test_refuses_on_concurrent_change_without_overwriting_backup(self, _m):
        p = write_state(self.tmp, self.raw_state())
        # 预置一份旧备份：冲突路径绝不能覆盖它
        old_backup = self.tmp / ".codex-global-state.json.session-analyzer.bak"
        old_backup.write_bytes(b"OLD-BACKUP")
        # 伪造「读取时 stat」来自另一个文件（size/mtime 都不同）→ 写前重读必然不一致
        other = self.tmp / "other-file.bin"
        other.write_bytes(b"x" * 3)
        stale_stat = other.stat()
        real_load = codex_ui_state.load_state_raw

        def fake_load(path):
            raw, _st = real_load(path)
            return raw, stale_stat

        with unittest.mock.patch.object(codex_ui_state, "load_state_raw", fake_load):
            with self.assertRaises(codex_ui_state.CleanupRejected) as cm:
                codex_ui_state.prune_ui_state_file(p, {G1, G2})
        self.assertEqual(cm.exception.category, "concurrent_change")
        self.assertEqual(p.read_bytes(), self.raw_state())          # 原文件未动
        self.assertEqual(old_backup.read_bytes(), b"OLD-BACKUP")    # 既有备份未被覆盖

    def test_missing_file_is_rejected_as_missing(self, _m):
        with self.assertRaises(codex_ui_state.CleanupRejected) as cm:
            codex_ui_state.prune_ui_state_file(self.tmp / "nope.json", {G1})
        self.assertEqual(cm.exception.category, "ui_state_missing")


class AppRunningTest(unittest.TestCase):
    def run_pgrep(self, results):
        def fake_run(cmd, **_kw):
            key = tuple(cmd)
            return mock.Mock(returncode=results[key])
        with mock.patch.object(codex_ui_state.subprocess, "run", side_effect=fake_run):
            return codex_ui_state.app_running()

    @unittest.skipIf(sys.platform != "darwin", "pgrep 门禁只在 macOS 生效")
    def test_true_when_chatgpt_running(self):
        results = {("pgrep", "-x", "ChatGPT"): 0,
                   ("pgrep", "-f", r"Codex\.app/Contents/MacOS"): 1}
        self.assertTrue(self.run_pgrep(results) is True)

    @unittest.skipIf(sys.platform != "darwin", "pgrep 门禁只在 macOS 生效")
    def test_false_when_both_quit(self):
        results = {("pgrep", "-x", "ChatGPT"): 1,
                   ("pgrep", "-f", r"Codex\.app/Contents/MacOS"): 1}
        self.assertTrue(self.run_pgrep(results) is False)

    @unittest.skipIf(sys.platform != "darwin", "pgrep 门禁只在 macOS 生效")
    def test_none_when_pgrep_broken(self):
        results = {("pgrep", "-x", "ChatGPT"): 1,
                   ("pgrep", "-f", r"Codex\.app/Contents/MacOS"): 3}  # 致命错误
        self.assertTrue(self.run_pgrep(results) is None)

    @unittest.skipIf(sys.platform != "darwin", "pgrep 门禁只在 macOS 生效")
    def test_none_when_pgrep_raises(self):
        with mock.patch.object(codex_ui_state.subprocess, "run",
                               side_effect=OSError("no pgrep")):
            self.assertIsNone(codex_ui_state.app_running())


class GhostCleanupIntegrationTest(unittest.TestCase):
    """cleanup_codex_ui_ghosts 端到端（隔离 HOME + 真实 sqlite + 真实文件写）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True)
        # threads 表：只有 A1 存活
        db = codex_dir / "state_5.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO threads (id) VALUES (?)", [(A1,)])
        con.commit()
        con.close()
        # UI 状态文件：G1/G2 幽灵 + A1 残留 + 干扰 uuid
        self.state_file = codex_dir / ".codex-global-state.json"
        self.state_file.write_bytes(dumps(fixture_state()))
        patches = [
            mock.patch.object(agent_delete, "HOME", self.home),
            mock.patch.object(codex_ui_state, "app_running", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_cleans_ghosts_keeps_alive(self):
        result = agent_delete.cleanup_codex_ui_ghosts()
        self.assertTrue(result["ok"])
        self.assertEqual(result["ghosts_found"], 2)
        self.assertEqual(result["removed_total"], 6)
        obj = json.loads(self.state_file.read_bytes())
        # 幽灵条目全清
        self.assertNotIn(G1, obj["projectless-thread-ids"])
        self.assertNotIn(G2, obj["pinned-thread-ids"])
        self.assertNotIn(G1, obj["thread-workspace-root-hints"])
        self.assertNotIn(G1, obj["thread-titles"]["titles"])
        self.assertNotIn(G2, obj["thread-titles"]["order"])
        # 存活会话的侧栏条目保留
        self.assertIn(A1, obj["projectless-thread-ids"])
        self.assertEqual(obj["thread-titles"]["titles"], {A1: "存活标题"})

    def test_idempotent_second_run(self):
        agent_delete.cleanup_codex_ui_ghosts()
        before = self.state_file.read_bytes()
        second = agent_delete.cleanup_codex_ui_ghosts()
        self.assertEqual(second["ghosts_found"], 0)
        self.assertEqual(second["removed_total"], 0)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_refuses_when_app_running(self):
        with mock.patch.object(codex_ui_state, "app_running", return_value=True):
            with self.assertRaises(ValueError):
                agent_delete.cleanup_codex_ui_ghosts()

    def test_refuses_when_db_unreadable(self):
        # 把 threads 表锁死：模拟仍有进程持有写锁 → fail closed，文件不动
        before = self.state_file.read_bytes()
        con = sqlite3.connect(self.home / ".codex" / "state_5.sqlite")
        con.execute("BEGIN EXCLUSIVE")
        con.execute("CREATE TABLE IF NOT EXISTS keepalive (x INT)")  # 持有写事务
        try:
            with self.assertRaises(ValueError):
                agent_delete.cleanup_codex_ui_ghosts()
        finally:
            con.rollback()
            con.close()
        self.assertEqual(self.state_file.read_bytes(), before)


class DeleteChainUITest(unittest.TestCase):
    """delete_codex_threads 的 UI 收尾：成功剪除 / 门禁拒绝时降级记录且不阻断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True)
        db = codex_dir / "state_5.sqlite"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT, archived INT, title TEXT,"
            " cwd TEXT, rollout_path TEXT, updated_at INT, first_user_message TEXT, preview TEXT)")
        con.execute(
            "INSERT INTO threads (id) VALUES (?)", (G1,))  # 待删会话
        con.commit()
        con.close()
        self.state_file = codex_dir / ".codex-global-state.json"
        self.state_file.write_bytes(dumps(fixture_state()))
        self.patchers = [
            mock.patch.object(agent_delete, "HOME", self.home),
            mock.patch.object(codex_ui_state, "app_running", return_value=False),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_delete_prunes_ui_state_in_same_operation(self):
        result = agent_delete.delete_codex_threads([G1])
        self.assertEqual(result["db_rows"].get("state_5.sqlite:threads.id"), 1)
        ui = result["ui_state"]
        self.assertNotIn("skipped", ui)
        self.assertGreater(ui["removed_total"], 0)
        obj = json.loads(self.state_file.read_bytes())
        self.assertNotIn(G1, obj["projectless-thread-ids"])
        self.assertNotIn(G1, obj["thread-titles"]["titles"])

    def test_delete_skips_ui_when_app_running(self):
        with mock.patch.object(codex_ui_state, "app_running", return_value=True):
            result = agent_delete.delete_codex_threads([G1])
        # 主删除照常完成，UI 收尾降级为 skip 记录
        self.assertEqual(result["db_rows"].get("state_5.sqlite:threads.id"), 1)
        self.assertEqual(result["ui_state"], {"skipped": "app_running"})
        self.assertEqual(self.state_file.read_bytes(), dumps(fixture_state()))

    def test_delete_ui_skip_when_state_file_missing(self):
        self.state_file.unlink()
        result = agent_delete.delete_codex_threads([G1])
        self.assertEqual(result["ui_state"], {"skipped": "ui_state_missing"})


if __name__ == "__main__":
    unittest.main()
