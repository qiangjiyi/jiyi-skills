from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("session_analyzer_scan", SCRIPTS / "scan.py")
scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan)
import agent_delete  # noqa: E402


NOW_MS = 1_800_000_000_000  # 固定「现在」，避免测试依赖真实时钟
STALE_MS = NOW_MS - 3 * 60 * 60 * 1000  # 3 小时前：稳定可删
LIVE_MS = NOW_MS - 60 * 1000  # 1 分钟前：仍在 10 分钟活跃窗口内


def make_cli_db(db: Path) -> None:
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE session (
          id text primary key, path text, title text,
          task_type text not null default 'interactive',
          parent_id text, time_created integer not null default 0,
          time_updated integer not null default 0, time_archived integer
        );
        CREATE TABLE session_entry (
          id text primary key,
          session_id text not null references session(id) on delete cascade,
          type text, time_created integer, time_updated integer, data text
        );
        CREATE TABLE message (
          id text primary key,
          session_id text not null references session(id) on delete cascade,
          time_created integer, time_updated integer, data text
        );
        CREATE TABLE part (
          id text primary key,
          message_id text not null references message(id) on delete cascade,
          session_id text not null, time_created integer, time_updated integer, data text
        );
        CREATE TABLE input_history (
          id text primary key, project_id text not null, session_id text,
          text text not null, kind text not null, time_created integer not null
        );
        """
    )
    con.commit()
    con.close()


def make_tasks_db(db: Path) -> None:
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE tasks (
          workspace_key TEXT NOT NULL, task_id TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '', task_status TEXT, model TEXT,
          deleted INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (workspace_key, task_id)
        );
        """
    )
    con.commit()
    con.close()


def seed(con: sqlite3.Connection, sql: str, params=()) -> None:
    con.execute(sql, params)
    con.commit()


class ZcodeScanTest(unittest.TestCase):
    def build_home(self, tmp: str) -> Path:
        home = Path(tmp)
        work_a = home / "proj-a"
        work_a.mkdir(parents=True)
        cli = home / ".zcode" / "cli"
        (cli / "db").mkdir(parents=True)
        (home / ".zcode" / "v2").mkdir(parents=True)
        make_cli_db(cli / "db" / "db.sqlite")
        make_tasks_db(home / ".zcode" / "v2" / "tasks-index.sqlite")

        con = sqlite3.connect(cli / "db" / "db.sqlite")
        seed(con, "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
             ("sess_" + "a" * 8 + "-1", str(work_a), "主会话", "interactive",
              None, STALE_MS, STALE_MS, None))
        seed(con, "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
             ("sess_" + "b" * 8 + "-2", str(home / "deleted-proj"), "孤儿会话",
              "interactive", None, STALE_MS, STALE_MS, None))
        seed(con, "INSERT INTO message VALUES (?,?,?,?,?)",
             ("m1", "sess_" + "a" * 8 + "-1", STALE_MS, STALE_MS, "x" * 100))
        seed(con, "INSERT INTO input_history VALUES (?,?,?,?,?,?)",
             ("h1", "proj_x", "sess_" + "a" * 8 + "-1", "hi", "user", STALE_MS))
        con.close()

        tc = sqlite3.connect(home / ".zcode" / "v2" / "tasks-index.sqlite")
        seed(tc, "INSERT INTO tasks (workspace_key, task_id, task_status, model) VALUES (?,?,?,?)",
             (str(work_a), "sess_" + "a" * 8 + "-1", "completed", "glm-5.3"))
        seed(tc, "INSERT INTO tasks (workspace_key, task_id, task_status, model) VALUES (?,?,?,?)",
             (str(home / "gone"), "sess_deadbeef-9", "completed", "glm-5.3"))
        tc.close()

        # 卫星：主会话有 artifacts/exec/rollout；另有孤儿残留目录与孤儿 rollout
        (cli / "artifacts" / ("sess_" + "a" * 8 + "-1")).mkdir(parents=True)
        (cli / "artifacts" / ("sess_" + "a" * 8 + "-1") / "call-1.json").write_text("{}")
        (cli / "exec" / ("sess_" + "a" * 8 + "-1")).mkdir(parents=True)  # 合法空目录
        (cli / "image-cache" / "sess_deadbeef-9").mkdir(parents=True)  # 孤儿残留
        (cli / "image-cache" / "sess_deadbeef-9" / "img.png").write_text("png")
        (cli / "rollout").mkdir(parents=True)
        (cli / "rollout" / ("model-io-sess_" + "a" * 8 + "-1.jsonl")).write_text("{}\n")
        (cli / "rollout" / "model-io-sess_deadbeef-9.jsonl").write_text("{}\n")
        return home

    def scan(self, home: Path) -> dict:
        with mock.patch.object(scan, "HOME", home), \
             mock.patch.object(scan, "time", mock.Mock(**{"time.return_value": NOW_MS / 1000})):
            return scan.scan_zcode()

    def test_scan_groups_orphans_and_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.build_home(tmp)
            agent = self.scan(home)
            self.assertTrue(agent["installed"])
            # 2 个真实会话 + 2 条孤儿残留（卫星目录与 rollout 去重后 1 条 + 任务索引 1 条）
            self.assertEqual(agent["session_count"], 4)
            self.assertEqual(agent["orphan_session_count"], 3)  # 孤儿会话 1 + 残留 2
            # 项目按 path 聚合；已删目录是孤儿项目
            by_pid = {p["id"]: p for p in agent["projects"]}
            self.assertIn(str(home / "proj-a"), by_pid)
            orphan_proj = by_pid[str(home / "deleted-proj")]
            self.assertTrue(orphan_proj["orphan"])
            # 会话 size 计入 message data 字节 + 卫星文件
            main = by_pid[str(home / "proj-a")]["sessions"][0]
            self.assertGreater(main["size"], 100)
            # 任务状态来自 v2 索引
            self.assertEqual(main["extra"]["task_status"], "completed")
            self.assertEqual(main["extra"]["model"], "glm-5.3")
            # 3 小时前更新的会话不算 live
            self.assertNotIn("zcode_live", main["extra"])
            # 孤儿残留组：卫星目录 + rollout + 任务索引行
            residue = by_pid["(zcode-orphans)"]
            kinds = {s["id"] for s in residue["sessions"]}
            self.assertIn("sess_deadbeef-9", kinds)
            self.assertTrue(any(i["id"].startswith("task:") for i in residue["sessions"]))
            self.assertTrue(residue["orphan"])

    def test_scan_marks_live_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self.build_home(tmp)
            con = sqlite3.connect(home / ".zcode" / "cli" / "db" / "db.sqlite")
            seed(con, "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
                 ("sess_" + "c" * 8 + "-3", str(home / "proj-a"), "活跃会话",
                  "interactive", None, LIVE_MS, LIVE_MS, None))
            con.close()
            agent = self.scan(home)
            live = [s for p in agent["projects"] for s in p["sessions"]
                    if s["extra"].get("zcode_live")]
            self.assertEqual(len(live), 1)
            self.assertEqual(live[0]["title"], "活跃会话")


class ZcodeDeleteTest(unittest.TestCase):
    def build_home(self, tmp: str) -> tuple[Path, str, str]:
        home = Path(tmp)
        (home / "proj-a").mkdir(parents=True)
        cli = home / ".zcode" / "cli"
        (cli / "db").mkdir(parents=True)
        (home / ".zcode" / "v2").mkdir(parents=True)
        make_cli_db(cli / "db" / "db.sqlite")
        make_tasks_db(home / ".zcode" / "v2" / "tasks-index.sqlite")
        parent = "sess_" + "a" * 8 + "-1"
        child = "sess_subagent_agent_" + "b" * 8 + "-2"
        con = sqlite3.connect(cli / "db" / "db.sqlite")
        seed(con, "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
             (parent, str(home / "proj-a"), "父", "interactive", None, STALE_MS, STALE_MS, None))
        seed(con, "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
             (child, str(home / "proj-a"), "子", "subagent_child", parent, STALE_MS, STALE_MS, None))
        seed(con, "INSERT INTO message VALUES (?,?,?,?,?)",
             ("m1", parent, STALE_MS, STALE_MS, "x" * 50))
        seed(con, "INSERT INTO message VALUES (?,?,?,?,?)",
             ("m2", child, STALE_MS, STALE_MS, "y" * 50))
        seed(con, "INSERT INTO input_history VALUES (?,?,?,?,?,?)",
             ("h1", "proj_x", parent, "hi", "user", STALE_MS))
        con.close()
        tc = sqlite3.connect(home / ".zcode" / "v2" / "tasks-index.sqlite")
        seed(tc, "INSERT INTO tasks (workspace_key, task_id, task_status) VALUES (?,?,?)",
             (str(home / "proj-a"), parent, "completed"))
        seed(tc, "INSERT INTO tasks (workspace_key, task_id, task_status) VALUES (?,?,?)",
             ("ws-gone", "sess_taskorphan-9", "completed"))
        tc.close()
        for sub in ("artifacts", "exec", "agents"):
            (cli / sub / parent).mkdir(parents=True)
        (cli / sub / parent / "agent_x" / "transcript.jsonl").parent.mkdir(parents=True)
        (cli / sub / parent / "agent_x" / "transcript.jsonl").write_text("[]")
        (cli / "rollout").mkdir(parents=True)
        (cli / "rollout" / f"model-io-{parent}.jsonl").write_text("{}\n")
        return home, parent, child

    def delete(self, home: Path, ids: list, now_ms: int = NOW_MS) -> dict:
        with mock.patch.object(agent_delete, "HOME", home):
            return agent_delete.delete_zcode_sessions(ids, "rm", now_ms=now_ms)

    def test_delete_cascades_children_satellites_and_task_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, parent, child = self.build_home(tmp)
            result = self.delete(home, [parent])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["db_rows"]["db.sqlite:session(cascade)"], 2)  # 父 + 子
            self.assertEqual(result["db_rows"]["db.sqlite:input_history"], 1)
            self.assertEqual(result["db_rows"]["tasks-index.sqlite:tasks"], 1)  # 父的任务行
            con = sqlite3.connect(home / ".zcode" / "cli" / "db" / "db.sqlite")
            self.assertEqual(con.execute("SELECT count(*) FROM session").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM message").fetchone()[0], 0)
            con.close()
            tc = sqlite3.connect(home / ".zcode" / "v2" / "tasks-index.sqlite")
            remaining = tc.execute("SELECT task_id FROM tasks").fetchall()
            self.assertEqual([r[0] for r in remaining], ["sess_taskorphan-9"])
            tc.close()
            cli = home / ".zcode" / "cli"
            self.assertFalse((cli / "artifacts" / parent).exists())
            self.assertFalse((cli / "exec" / parent).exists())
            self.assertFalse((cli / "agents" / parent).exists())
            self.assertFalse((cli / "rollout" / f"model-io-{parent}.jsonl").exists())

    def test_delete_task_orphan_only_touches_index_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, parent, _child = self.build_home(tmp)
            result = self.delete(home, ["task:ws-gone|sess_taskorphan-9"])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["db_rows"]["tasks-index.sqlite:tasks"], 1)
            tc = sqlite3.connect(home / ".zcode" / "v2" / "tasks-index.sqlite")
            remaining = [r[0] for r in tc.execute("SELECT task_id FROM tasks")]
            tc.close()
            self.assertIn(parent, remaining)
            self.assertNotIn("sess_taskorphan-9", remaining)

    def test_delete_refuses_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, parent, _child = self.build_home(tmp)
            con = sqlite3.connect(home / ".zcode" / "cli" / "db" / "db.sqlite")
            seed(con, "UPDATE session SET time_updated = ? WHERE id = ?", (LIVE_MS, parent))
            con.close()
            with self.assertRaises(ValueError):
                self.delete(home, [parent])
            # 拒绝时不得有任何实际删除
            con = sqlite3.connect(home / ".zcode" / "cli" / "db" / "db.sqlite")
            self.assertEqual(con.execute("SELECT count(*) FROM session").fetchone()[0], 2)
            con.close()
            self.assertTrue((home / ".zcode" / "cli" / "artifacts" / parent).exists())


if __name__ == "__main__":
    unittest.main()
