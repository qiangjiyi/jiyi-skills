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
SPEC = importlib.util.spec_from_file_location("session_analyzer_scan", SCRIPTS / "scan.py")
scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan)


class ScanTest(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                if isinstance(row, str):
                    f.write(row + "\n")
                else:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def scan_claude(self, home: Path) -> dict:
        with mock.patch.object(scan, "HOME", home), mock.patch.object(
            scan, "_enrich_multica_sessions", side_effect=lambda agent: None
        ):
            return scan.scan_claude()

    def test_jsonl_cwd_is_deterministic_and_skips_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            self.write_jsonl(path, [
                "not-json",
                {"cwd": ""},
                {"message": {"cwd": "/nested/is/not/authoritative"}},
                {"cwd": "/z-path"},
                {"cwd": "/a-path"},
                {"cwd": "/z-path"},
            ])
            self.assertEqual(scan._claude_jsonl_cwd(path), "/z-path")

            self.write_jsonl(path, [{"cwd": "/z-path"}, {"cwd": "/a-path"}])
            self.assertEqual(scan._claude_jsonl_cwd(path), "/a-path")

    def test_jsonl_cwd_reads_the_full_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            rows = [{"cwd": "/early", "padding": "x" * (1 << 20)}]
            rows.extend({"cwd": "/late"} for _ in range(2))
            self.write_jsonl(path, rows)
            self.assertEqual(scan._claude_jsonl_cwd(path), "/late")

    def test_jsonl_cwd_wins_over_history_even_when_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-Users-test-deleted-project"
            sid = "11111111-1111-1111-1111-111111111111"
            pdir = home / ".claude" / "projects" / encoded
            deleted_cwd = str(home / "deleted-real-cwd")
            existing_history = home / "history-project"
            existing_history.mkdir()
            self.write_jsonl(pdir / f"{sid}.jsonl", [
                {"cwd": deleted_cwd, "type": "user", "message": {"content": "hello"}}
            ])
            self.write_jsonl(home / ".claude" / "history.jsonl", [
                {"sessionId": sid, "project": str(existing_history)}
            ])

            project = self.scan_claude(home)["projects"][0]
            self.assertEqual(project["id"], encoded)
            self.assertEqual(project["label"], deleted_cwd)
            self.assertEqual(project["real_path"], deleted_cwd)
            self.assertEqual(project["sessions"][0]["extra"]["cwd"], deleted_cwd)
            self.assertTrue(project["orphan"])
            self.assertEqual(project["orphan_reason"], "missing_workdir")

    def test_history_wins_over_encoded_path_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-unresolvable-project-name"
            sid = "22222222-2222-2222-2222-222222222222"
            pdir = home / ".claude" / "projects" / encoded
            history_cwd = home / "history-project"
            history_cwd.mkdir()
            self.write_jsonl(pdir / f"{sid}.jsonl", [
                {"type": "user", "message": {"content": "hello"}}
            ])
            self.write_jsonl(home / ".claude" / "history.jsonl", [
                {"sessionId": sid, "project": str(history_cwd)}
            ])

            project = self.scan_claude(home)["projects"][0]
            self.assertEqual(project["real_path"], str(history_cwd))
            self.assertEqual(project["sessions"][0]["extra"]["cwd"], str(history_cwd))
            self.assertFalse(project["orphan"])
            self.assertIsNone(project["orphan_reason"])

    def test_deleted_skill_runtime_has_specific_orphan_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-Users-test-Workspace-exports-baoyu-studio--skill-runtime-releases-digest-runner"
            sid = "33333333-3333-3333-3333-333333333333"
            digest = "a" * 64
            cwd = home / "Workspace" / "exports" / "baoyu-studio" / "_skill-runtime" / "releases" / digest / "runner"
            self.write_jsonl(home / ".claude" / "projects" / encoded / f"{sid}.jsonl", [
                {"cwd": str(cwd), "type": "user", "message": {"content": "draw"}}
            ])

            project = self.scan_claude(home)["projects"][0]
            self.assertEqual(project["id"], encoded)
            self.assertEqual(project["label"], str(cwd))
            self.assertEqual(project["real_path"], str(cwd))
            self.assertTrue(project["orphan"])
            self.assertEqual(project["orphan_reason"], "temporary_skill_runtime")

    def test_existing_skill_runtime_is_not_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-Users-test-existing-skill-runtime"
            sid = "44444444-4444-4444-4444-444444444444"
            digest = "b" * 64
            cwd = home / "exports" / "baoyu-studio" / "_skill-runtime" / "releases" / digest / "runner"
            cwd.mkdir(parents=True)
            self.write_jsonl(home / ".claude" / "projects" / encoded / f"{sid}.jsonl", [
                {"cwd": str(cwd), "type": "user", "message": {"content": "draw"}}
            ])

            project = self.scan_claude(home)["projects"][0]
            self.assertFalse(project["orphan"])
            self.assertIsNone(project["orphan_reason"])

    def test_mixed_project_paths_prefers_existing_and_is_not_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-Users-test-mixed-project"
            pdir = home / ".claude" / "projects" / encoded
            existing = home / "existing-project"
            existing.mkdir()
            deleted = home / "deleted-project"
            self.write_jsonl(pdir / "55555555-5555-5555-5555-555555555555.jsonl", [
                {"cwd": str(deleted), "type": "user", "message": {"content": "old"}}
            ])
            self.write_jsonl(pdir / "66666666-6666-6666-6666-666666666666.jsonl", [
                {"cwd": str(existing), "type": "user", "message": {"content": "new"}}
            ])

            project = self.scan_claude(home)["projects"][0]
            self.assertEqual(project["real_path"], str(existing))
            self.assertEqual(
                {session["extra"]["cwd"] for session in project["sessions"]},
                {str(existing), str(deleted)},
            )
            self.assertFalse(project["orphan"])
            self.assertIsNone(project["orphan_reason"])

    def test_empty_project_synthetic_session_has_no_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            encoded = "-missing-empty-project"
            (home / ".claude" / "projects" / encoded).mkdir(parents=True)

            session = self.scan_claude(home)["projects"][0]["sessions"][0]
            self.assertEqual(session["extra"]["claude_kind"], "orphan_dir")
            self.assertNotIn("cwd", session["extra"])

    def test_project_path_choice_uses_frequency_then_lexical_order(self) -> None:
        with mock.patch.object(scan, "_path_exists", return_value=False):
            self.assertEqual(
                scan._choose_claude_project_path(["/z", "/a", "/z"]),
                "/z",
            )
            self.assertEqual(
                scan._choose_claude_project_path(["/z", "/a"]),
                "/a",
            )

    def test_json_out_is_private_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.json"
            with mock.patch.object(scan, "scan_codex", return_value={
                "key": "codex", "installed": False, "session_count": 0,
                "orphan_session_count": 0, "total_size": 0,
            }), mock.patch.object(scan, "scan_antigravity", return_value={
                "key": "antigravity", "installed": False, "session_count": 0,
                "orphan_session_count": 0, "total_size": 0,
            }), mock.patch.object(scan, "scan_claude", return_value={
                "key": "claude", "installed": False, "session_count": 0,
                "orphan_session_count": 0, "total_size": 0,
            }), mock.patch.object(sys, "argv", ["scan.py", "--json-out", str(path)]):
                self.assertEqual(scan.main(), 0)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["home"], str(scan.HOME))
            self.assertEqual(list(Path(tmp).glob(".scan.json.*")), [])

    def test_scan_codex_does_not_modify_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / ".codex"
            root.mkdir()
            db = root / "state_5.sqlite"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE threads ("
                "id TEXT, source TEXT, archived INTEGER, title TEXT, cwd TEXT, "
                "rollout_path TEXT, updated_at INTEGER, first_user_message TEXT, preview TEXT)"
            )
            con.commit()
            con.close()
            config = root / "config.toml"
            original = '[projects."/missing/project"]\ntrust_level = "trusted"\n'
            config.write_text(original, encoding="utf-8")
            before_mtime = config.stat().st_mtime_ns

            with mock.patch.object(scan, "HOME", home):
                agent = scan.scan_codex()

            self.assertTrue(agent["installed"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(config.stat().st_mtime_ns, before_mtime)
            self.assertFalse((root / "config.toml.tmp").exists())


if __name__ == "__main__":
    unittest.main()
