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
N1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"  # 干扰：thread-project-assignments value 里的 projectId
N2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"  # 干扰：chatgpt-sidebar-state-v1 的 host uuid
N3 = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"  # 干扰：client-thread-bindings-v1 的 client uuid


def dumps(obj) -> bytes:
    """Electron JSON.stringify 等价的紧凑序列化（本机实测与真实状态文件 round-trip 恒等）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fixture_state() -> dict:
    """覆盖两代命名空间（2026-08 旧布局 + 2026-09 新布局），并锁定确证排除结构的边界：
    prompt-history（用户提示词缓存）、chatgpt-sidebar-state-v1（host uuid 为 key）、
    client-thread-bindings-v1（client uuid 为 key）、名字不含 thread 的 atom-state 子 key、
    thread-project-assignments 的 value（projectId）——即使其中的条目属于幽灵/被删
    thread，也一个字节都不能动。"""
    return {
        # ── 2026-08 旧布局 ──
        "projectless-thread-ids": [G1, A1, "not-a-uuid", 42, None],
        "pinned-thread-ids": [G2],
        "thread-workspace-root-hints": {G1: "/tmp/ws", A1: "/tmp/alive"},
        "thread-titles": {"titles": {G1: "幽灵标题", A1: "存活标题"}, "order": [G1, A1, G2]},
        # ── 2026-09 新布局：顶层 dict（key = thread id）──
        "thread-writable-roots": {G2: ["/tmp/g2"], A1: ["/tmp/alive"]},
        "thread-projectless-output-directories": {G1: "/tmp/out1", G2: "/tmp/out2",
                                                  "not-a-dir-key": "/tmp/x"},
        "thread-project-assignments": {
            G1: {"projectKind": "local", "projectId": "local-abc"},
            A1: {"projectKind": "remote", "projectId": N1},
        },
        # ── 2026-09 新布局：electron-persisted-atom-state 子结构 ──
        "electron-persisted-atom-state": {
            "thread-descriptions-v1": {G1: "幽灵描述", A1: "存活描述"},
            "heartbeat-thread-permissions-by-id": {G2: {"ask": True}, A1: {"ask": False}},
            "thread-reference-capability:" + G1: "agentic",
            "thread-reference-capability:" + A1: "agentic",
            # 确证排除的结构
            "prompt-history": {"global": ["global-prompt"], G2: ["per-thread-prompt"]},
            "chatgpt-sidebar-state-v1": {N2: {"pinnedProjects": [], "projects": []}},
            "client-thread-bindings-v1": {"client-new-thread:" + N3: G2},
            "composer-prompt-drafts-v1": {G2: "draft"},
        },
        # ── 与 thread 无关的顶层 key ──
        "electron-saved-workspace-roots": ["/Users/x/SomeProject"],
        "unrelated": {"keep": [1, 2, 3]},
    }


LIVE = {A1}  # threads 表只有 A1 存活 → G1/G2 是幽灵，N1/N2/N3 与 threads 判定无关

G1_KEYS = {"projectless-thread-ids", "thread-workspace-root-hints",
           "thread-titles.titles", "thread-titles.order",
           "thread-projectless-output-directories", "thread-project-assignments",
           "electron-persisted-atom-state.thread-descriptions-v1",
           "electron-persisted-atom-state.thread-reference-capability"}
G2_KEYS = {"pinned-thread-ids", "thread-titles.order",
           "thread-writable-roots", "thread-projectless-output-directories",
           "electron-persisted-atom-state.heartbeat-thread-permissions-by-id"}
A1_KEYS = {"projectless-thread-ids", "thread-workspace-root-hints",
           "thread-titles.titles", "thread-titles.order",
           "thread-writable-roots", "thread-project-assignments",
           "electron-persisted-atom-state.thread-descriptions-v1",
           "electron-persisted-atom-state.heartbeat-thread-permissions-by-id",
           "electron-persisted-atom-state.thread-reference-capability"}


class NamespaceTest(unittest.TestCase):
    def test_namespace_collection_is_defensive(self):
        found = codex_ui_state.namespace_thread_ids(fixture_state())
        self.assertEqual(found[G1], G1_KEYS)
        self.assertEqual(found[G2], G2_KEYS)
        self.assertEqual(found[A1], A1_KEYS)
        # 非 uuid 哨兵也会被收集进命名空间，但 ghost 判定有形态守卫（见下）
        self.assertEqual(found["not-a-uuid"], {"projectless-thread-ids"})
        self.assertEqual(found["not-a-dir-key"], {"thread-projectless-output-directories"})
        # 干扰 uuid 不在白名单 key 里，不参与收集
        for n in (N1, N2, N3):
            self.assertNotIn(n, found)

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
            "thread-writable-roots": "not-a-dict",          # 新布局类型变体
            "thread-project-assignments": [G1],             # 该是 dict 却是 list
            "electron-persisted-atom-state": "not-a-dict",  # atom-state 整体类型变体
            "projectless-thread-ids": [G1],
        }
        found = codex_ui_state.namespace_thread_ids(data)
        self.assertEqual(found, {G1: {"projectless-thread-ids"}})

    def test_atom_state_subkey_type_variants_are_skipped(self):
        data = {"projectless-thread-ids": [G1],
                "electron-persisted-atom-state": {
            "thread-descriptions-v1": "not-a-dict",
            "heartbeat-thread-permissions-by-id": [G2],
            "thread-reference-capability": G1,          # 无冒号前缀拼接，不是 tid key
            "thread-reference-capability:": G1,         # 前缀后为空
        }}
        found = codex_ui_state.namespace_thread_ids(data)
        self.assertEqual(found, {G1: {"projectless-thread-ids"}})

    def test_thread_titles_missing_entirely(self):
        # 2026-08-31 实测：真实文件可能没有 thread-titles key
        data = {"projectless-thread-ids": [G1]}
        self.assertEqual(codex_ui_state.namespace_thread_ids(data),
                         {G1: {"projectless-thread-ids"}})
        self.assertIsNone(codex_ui_state.title_of(data, G1))

    def test_title_of_falls_back_to_atom_state_descriptions(self):
        # 2026-09 布局：thread-titles 缺失时，描述缓存可用作幽灵标签
        data = {"electron-persisted-atom-state": {"thread-descriptions-v1": {G1: "幽灵描述"}}}
        self.assertEqual(codex_ui_state.title_of(data, G1), "幽灵描述")

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
        # 命中计数（两代命名空间合计）
        self.assertEqual(removed, {
            "projectless-thread-ids": 1,
            "pinned-thread-ids": 1,
            "thread-workspace-root-hints": 1,
            "thread-titles.titles": 1,
            "thread-titles.order": 2,
            "thread-writable-roots": 1,
            "thread-projectless-output-directories": 2,
            "thread-project-assignments": 1,
            "electron-persisted-atom-state.thread-descriptions-v1": 1,
            "electron-persisted-atom-state.heartbeat-thread-permissions-by-id": 1,
            "electron-persisted-atom-state.thread-reference-capability": 1,
        })
        # 旧布局：G 全清、A 与非 uuid 哨兵保留
        self.assertEqual(data["projectless-thread-ids"], [A1, "not-a-uuid", 42, None])
        self.assertEqual(data["pinned-thread-ids"], [])
        self.assertEqual(data["thread-workspace-root-hints"], {A1: "/tmp/alive"})
        self.assertEqual(data["thread-titles"], {"titles": {A1: "存活标题"}, "order": [A1]})
        # 新布局：G 全清、A 保留
        self.assertEqual(data["thread-writable-roots"], {A1: ["/tmp/alive"]})
        self.assertEqual(data["thread-projectless-output-directories"], {"not-a-dir-key": "/tmp/x"})
        self.assertEqual(data["thread-project-assignments"],
                         {A1: {"projectKind": "remote", "projectId": N1}})
        eps = data["electron-persisted-atom-state"]
        self.assertEqual(eps["thread-descriptions-v1"], {A1: "存活描述"})
        self.assertEqual(eps["heartbeat-thread-permissions-by-id"], {A1: {"ask": False}})
        self.assertEqual(sorted(eps.keys()),
                         sorted(["thread-descriptions-v1", "heartbeat-thread-permissions-by-id",
                                 "thread-reference-capability:" + A1, "prompt-history",
                                 "chatgpt-sidebar-state-v1", "client-thread-bindings-v1",
                                 "composer-prompt-drafts-v1"]))
        # 确证排除的结构：即使含幽灵/被删 thread 的条目也一个字节不动
        self.assertEqual(eps["prompt-history"], {"global": ["global-prompt"], G2: ["per-thread-prompt"]})
        self.assertEqual(eps["chatgpt-sidebar-state-v1"], {N2: {"pinnedProjects": [], "projects": []}})
        self.assertEqual(eps["client-thread-bindings-v1"], {"client-new-thread:" + N3: G2})
        self.assertEqual(eps["composer-prompt-drafts-v1"], {G2: "draft"})
        self.assertEqual(data["unrelated"], {"keep": [1, 2, 3]})
        self.assertEqual(data["electron-saved-workspace-roots"], ["/Users/x/SomeProject"])

    def test_prune_prefix_key_requires_full_tid_match(self):
        # 前缀 key 只允许全 id 精确匹配：前缀相同的截断 id 不许被剪
        partial = "thread-reference-capability:" + G1[:13]
        data = {"electron-persisted-atom-state": {
            "thread-reference-capability:" + G1: "agentic", partial: "agentic"}}
        removed = codex_ui_state.prune(data, {G1})
        self.assertEqual(removed, {"electron-persisted-atom-state.thread-reference-capability": 1})
        self.assertEqual(list(data["electron-persisted-atom-state"]), [partial])

    def test_prune_is_idempotent(self):
        data = fixture_state()
        codex_ui_state.prune(data, {G1, G2})
        again = codex_ui_state.prune(data, {G1, G2})
        self.assertEqual(again, {})

    def test_prune_with_malformed_keys_does_not_raise(self):
        data = {"projectless-thread-ids": "bad", "thread-titles": None,
                "electron-persisted-atom-state": {"thread-descriptions-v1": 7}}
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
        self.assertEqual(result["removed_total"], 13)
        self.assertTrue(result["backup_created"])
        after = p.read_bytes()
        # 逐字节期望：手写的「同一序列化规范下去掉 G1/G2 条目」的字节串，
        # 不经过被测代码（防循环验证）。key 顺序 = fixture 插入顺序的残留。
        expected = (
            '{"projectless-thread-ids":["%s","not-a-uuid",42,null],'
            '"pinned-thread-ids":[],'
            '"thread-workspace-root-hints":{"%s":"/tmp/alive"},'
            '"thread-titles":{"titles":{"%s":"存活标题"},"order":["%s"]},'
            '"thread-writable-roots":{"%s":["/tmp/alive"]},'
            '"thread-projectless-output-directories":{"not-a-dir-key":"/tmp/x"},'
            '"thread-project-assignments":{"%s":{"projectKind":"remote","projectId":"%s"}},'
            '"electron-persisted-atom-state":{'
            '"thread-descriptions-v1":{"%s":"存活描述"},'
            '"heartbeat-thread-permissions-by-id":{"%s":{"ask":false}},'
            '"thread-reference-capability:%s":"agentic",'
            '"prompt-history":{"global":["global-prompt"],"%s":["per-thread-prompt"]},'
            '"chatgpt-sidebar-state-v1":{"%s":{"pinnedProjects":[],"projects":[]}},'
            '"client-thread-bindings-v1":{"client-new-thread:%s":"%s"},'
            '"composer-prompt-drafts-v1":{"%s":"draft"}},'
            '"electron-saved-workspace-roots":["/Users/x/SomeProject"],'
            '"unrelated":{"keep":[1,2,3]}}'
        ) % (A1, A1, A1, A1, A1, A1, N1, A1, A1, A1, G2, N2, N3, G2, G2)
        self.assertEqual(after, expected.encode("utf-8"))
        # 抽查解析后内容：确证排除的结构原样保留
        obj = json.loads(after)
        eps = obj["electron-persisted-atom-state"]
        self.assertEqual(eps["prompt-history"], {"global": ["global-prompt"], G2: ["per-thread-prompt"]})
        self.assertEqual(eps["client-thread-bindings-v1"], {"client-new-thread:" + N3: G2})
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
        self.assertEqual(result["removed_total"], 13)
        obj = json.loads(self.state_file.read_bytes())
        # 幽灵条目全清（两代命名空间）
        self.assertNotIn(G1, obj["projectless-thread-ids"])
        self.assertNotIn(G2, obj["pinned-thread-ids"])
        self.assertNotIn(G1, obj["thread-workspace-root-hints"])
        self.assertNotIn(G1, obj["thread-titles"]["titles"])
        self.assertNotIn(G2, obj["thread-titles"]["order"])
        self.assertNotIn(G2, obj["thread-writable-roots"])
        self.assertNotIn(G1, obj["thread-projectless-output-directories"])
        self.assertNotIn(G1, obj["thread-project-assignments"])
        eps = obj["electron-persisted-atom-state"]
        self.assertNotIn(G1, eps["thread-descriptions-v1"])
        self.assertNotIn(G2, eps["heartbeat-thread-permissions-by-id"])
        self.assertNotIn("thread-reference-capability:" + G1, eps)
        # 确证排除的结构不受影响（含幽灵 id 的缓存条目原样保留）
        self.assertEqual(eps["prompt-history"], {"global": ["global-prompt"], G2: ["per-thread-prompt"]})
        # 存活会话的侧栏条目保留
        self.assertIn(A1, obj["projectless-thread-ids"])
        self.assertEqual(obj["thread-titles"]["titles"], {A1: "存活标题"})
        self.assertEqual(obj["thread-project-assignments"],
                         {A1: {"projectKind": "remote", "projectId": N1}})
        self.assertEqual(eps["thread-descriptions-v1"], {A1: "存活描述"})

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
        con.executemany(
            "INSERT INTO threads (id) VALUES (?)",
            [(G1,), (A1,)])  # G1 待删；A1 存活（其侧栏条目必须保留）
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
        # 确证删除的 id 全清（两代命名空间）
        self.assertNotIn(G1, obj["projectless-thread-ids"])
        self.assertNotIn(G1, obj["thread-titles"]["titles"])
        self.assertNotIn(G1, obj["thread-project-assignments"])
        eps = obj["electron-persisted-atom-state"]
        self.assertNotIn(G1, eps["thread-descriptions-v1"])
        self.assertNotIn("thread-reference-capability:" + G1, eps)
        # 删除动作连带全量扫鬼：G2 本就不在 threads 表，也应一并剪除
        self.assertEqual(obj["pinned-thread-ids"], [])
        self.assertNotIn(G2, obj["thread-titles"]["order"])
        self.assertNotIn(G2, obj["thread-writable-roots"])
        self.assertNotIn(G2, eps["heartbeat-thread-permissions-by-id"])
        # 存活会话不受影响
        self.assertIn(A1, obj["projectless-thread-ids"])
        self.assertEqual(obj["thread-titles"]["titles"], {A1: "存活标题"})
        self.assertIn(A1, eps["thread-descriptions-v1"])

    def test_delete_still_prunes_confirmed_ids_when_ghost_recompute_fails(self):
        # ghost 实时重算失败（DB 锁等）只影响「顺带清扫」，确证删除 id 的剪除照常生效
        with mock.patch.object(codex_ui_state, "live_thread_ids",
                               side_effect=sqlite3.OperationalError("locked")):
            result = agent_delete.delete_codex_threads([G1])
        ui = result["ui_state"]
        self.assertNotIn("skipped", ui)
        obj = json.loads(self.state_file.read_bytes())
        self.assertNotIn(G1, obj["projectless-thread-ids"])
        self.assertNotIn(G1, obj["thread-titles"]["titles"])
        # G2 未被清扫（重算失败，留给下次流程步骤补收）
        self.assertEqual(obj["pinned-thread-ids"], [G2])

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


class LiveGhostRefreshTest(unittest.TestCase):
    """live_ghost_entries / refresh_codex_ghosts：报告入口的幽灵实时重算。
    契约：报告展示的幽灵数量必须是当下真实存在的数量——流程在起报告前已清扫过时，
    快照旧计数被实时结果覆盖（通常为 0，页面隐藏 👻 区块），不得误导用户。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True)
        db = codex_dir / "state_5.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO threads (id) VALUES (?)", [(A1,)])
        con.commit()
        con.close()
        self.state_file = codex_dir / ".codex-global-state.json"
        self.state_file.write_bytes(dumps(fixture_state()))

    def snapshot(self) -> dict:
        return {"agents": [
            {"key": "codex", "installed": True, "ghost_ui_entry_count": 99,
             "ghost_ui_entries": [{"id": "stale", "keys": [], "title": None}]},
            {"key": "zcode", "installed": True},
        ]}

    def test_live_recompute_replaces_snapshot_counts(self):
        # 快照虚高 99 → 实时重算为真实的 2（G1/G2）
        out = codex_ui_state.refresh_codex_ghosts(self.snapshot(), self.home)
        codex = out["agents"][0]
        self.assertEqual(codex["ghost_ui_entry_count"], 2)
        self.assertEqual({g["id"] for g in codex["ghost_ui_entries"]}, {G1, G2})
        # 非 codex agent 原样保留
        self.assertEqual(out["agents"][1], {"key": "zcode", "installed": True})

    def test_original_snapshot_not_mutated(self):
        snap = self.snapshot()
        codex_ui_state.refresh_codex_ghosts(snap, self.home)
        self.assertEqual(snap["agents"][0]["ghost_ui_entry_count"], 99)

    def test_clean_state_reports_zero(self):
        # 已清扫干净（状态文件里无幽灵）→ 0（页面会隐藏 👻 区块）
        data = codex_ui_state.parse_state(self.state_file.read_bytes())
        codex_ui_state.prune(data, {G1, G2})
        self.state_file.write_bytes(codex_ui_state.canonical_bytes(data))
        out = codex_ui_state.refresh_codex_ghosts(self.snapshot(), self.home)
        self.assertEqual(out["agents"][0]["ghost_ui_entry_count"], 0)
        self.assertEqual(out["agents"][0]["ghost_ui_entries"], [])

    def test_unreadable_state_falls_back_to_snapshot(self):
        # 状态文件损坏 → None → 保留快照值（宁可显示旧数也不谎报 0）
        self.state_file.write_bytes(b"not-json")
        out = codex_ui_state.refresh_codex_ghosts(self.snapshot(), self.home)
        self.assertEqual(out["agents"][0]["ghost_ui_entry_count"], 99)

    def test_missing_codex_dir_reports_clean(self):
        # 未装 Codex / 文件不存在 → 真实含义是干净（0），而非无法判定
        self.assertEqual(codex_ui_state.live_ghost_entries(Path("/nonexistent-home")), [])


class CliMainTest(unittest.TestCase):
    """流程默认清扫的 CLI 入口（SKILL.md Step 3 ② 调用的命令）。"""

    def test_runs_and_reports_sweep(self):
        with mock.patch.object(agent_delete, "cleanup_codex_ui_ghosts",
                               return_value={"ok": True, "ghosts_found": 2,
                                             "removed_total": 6, "backup_created": True}):
            with mock.patch("sys.argv", ["agent_delete.py", "--codex-ghosts"]):
                self.assertEqual(agent_delete.main(), 0)

    def test_rejection_returns_nonzero(self):
        with mock.patch.object(agent_delete, "cleanup_codex_ui_ghosts",
                               side_effect=ValueError("App 仍在运行")):
            with mock.patch("sys.argv", ["agent_delete.py", "--codex-ghosts"]):
                self.assertEqual(agent_delete.main(), 1)

    def test_no_flags_prints_help_and_fails(self):
        with mock.patch("sys.argv", ["agent_delete.py"]):
            self.assertEqual(agent_delete.main(), 1)


if __name__ == "__main__":
    unittest.main()
