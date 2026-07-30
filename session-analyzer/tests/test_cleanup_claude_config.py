from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "session_analyzer_cleanup_claude_config",
    SCRIPTS / "cleanup_claude_config.py",
)
cleanup_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = cleanup_config
SPEC.loader.exec_module(cleanup_config)


class CleanupClaudeConfigTest(unittest.TestCase):
    def write_snapshot(self, home: Path, sessions: list[dict], installed: bool = True) -> Path:
        path = home / "snapshot.json"
        normalized_sessions = []
        if installed:
            project_dir = home / ".claude" / "projects" / "encoded"
            project_dir.mkdir(parents=True, exist_ok=True)
            for index, session in enumerate(sessions):
                item = json.loads(json.dumps(session))
                if item.get("extra", {}).get("claude_kind") == "session":
                    item["id"] = f"00000000-0000-0000-0000-{index:012d}"
                    (project_dir / f"{item['id']}.jsonl").write_text(
                        json.dumps({"cwd": item["extra"]["cwd"]}) + "\n",
                        encoding="utf-8",
                    )
                normalized_sessions.append(item)
        projects = [{"id": "encoded", "sessions": normalized_sessions}] if installed else []
        path.write_text(json.dumps({
            "home": str(home),
            "agents": [{
                "key": "claude",
                "installed": installed,
                "projects": projects,
            }],
        }), encoding="utf-8")
        path.chmod(0o600)
        return path

    def live_session(self, cwd: str) -> dict:
        return {"id": cwd, "extra": {"claude_kind": "session", "cwd": cwd}}

    def orphan_session(self) -> dict:
        return {"id": "orphan", "extra": {"claude_kind": "orphan_dir"}}

    def write_config(self, home: Path, config: dict) -> tuple[Path, bytes]:
        path = home / ".claude.json"
        raw = (json.dumps(config, ensure_ascii=False, indent=4) + "\n").encode()
        path.write_bytes(raw)
        return path, raw

    def test_removes_stale_projects_even_when_directory_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = home / "live"
            stale_existing = home / "stale-existing"
            stale_existing.mkdir()
            snapshot = self.write_snapshot(home, [self.live_session(str(live))])
            config_path, original = self.write_config(home, {
                "projects": {
                    str(live): {"allowedTools": ["Read"]},
                    str(stale_existing): {"hasTrustDialogAccepted": True},
                    str(home / "missing"): {"mcpServers": {"secret": "do-not-print"}},
                },
                "oauthAccount": {"token": "oauth-secret"},
            })

            result = cleanup_config.cleanup(snapshot, home)

            self.assertEqual(result.removed, 2)
            self.assertTrue(result.backup_created)
            config = json.loads(config_path.read_text())
            self.assertEqual(config["projects"], {str(live): {"allowedTools": ["Read"]}})
            self.assertEqual(config["oauthAccount"], {"token": "oauth-secret"})
            backup = home / ".claude.json.session-analyzer.bak"
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_multi_cwd_keeps_every_live_project_independent_of_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            first = str(home / "first")
            second = str(home / "second")
            snapshot = self.write_snapshot(home, [self.live_session(first), self.live_session(second)])
            config_path, _ = self.write_config(home, {
                "projects": {first: {"id": 1}, second: {"id": 2}},
                "theme": "dark",
            })

            result = cleanup_config.cleanup(snapshot, home)

            self.assertEqual(result.removed, 0)
            self.assertEqual(json.loads(config_path.read_text())["projects"], {
                first: {"id": 1}, second: {"id": 2},
            })
            self.assertFalse((home / ".claude.json.session-analyzer.bak").exists())

    def test_orphan_only_and_not_installed_authorize_empty_live_set(self) -> None:
        for installed, sessions in ((True, [self.orphan_session()]), (False, [])):
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = self.write_snapshot(home, sessions, installed=installed)
                config_path, _ = self.write_config(home, {
                    "projects": {str(home / "old"): {"trusted": True}},
                    "other": [1, 2],
                })

                result = cleanup_config.cleanup(snapshot, home)

                self.assertEqual(result.removed, 1)
                self.assertEqual(json.loads(config_path.read_text()), {
                    "projects": {}, "other": [1, 2],
                })

    def test_missing_config_and_no_changes_are_noop_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = str(home / "live")
            snapshot = self.write_snapshot(home, [self.live_session(live)])
            self.assertEqual(cleanup_config.cleanup(snapshot, home).removed, 0)

            config_path, original = self.write_config(home, {"projects": {live: {"x": 1}}})
            result = cleanup_config.cleanup(snapshot, home)
            self.assertEqual(result.removed, 0)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse((home / ".claude.json.session-analyzer.bak").exists())

    def test_invalid_snapshots_fail_closed(self) -> None:
        cases = [
            {"agents": []},
            {"agents": [{"key": "claude", "installed": True, "projects": "bad"}]},
            {"agents": [{"key": "claude", "installed": True, "projects": [{"sessions": [
                {"extra": {"claude_kind": "session"}},
            ]}]}]},
            {"agents": [{"key": "claude", "installed": True, "projects": [{"sessions": [
                {"extra": {"claude_kind": "unknown"}},
            ]}]}]},
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = home / "snapshot.json"
                snapshot.write_text(json.dumps(payload))
                snapshot.chmod(0o600)
                config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
                with self.assertRaisesRegex(cleanup_config.CleanupError, "invalid_scan_snapshot"):
                    cleanup_config.cleanup(snapshot, home)
                self.assertEqual(config_path.read_bytes(), original)

    def test_snapshot_must_match_home_and_be_private(self) -> None:
        for mutate in (
            lambda path, home: path.chmod(0o644),
            lambda path, home: path.write_text(json.dumps({"home": "/other", "agents": []})),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = self.write_snapshot(home, [])
                mutate(snapshot, home)
                config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
                with self.assertRaisesRegex(cleanup_config.CleanupError, "invalid_scan_snapshot"):
                    cleanup_config.cleanup(snapshot, home)
                self.assertEqual(config_path.read_bytes(), original)

    def test_session_state_change_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = str(home / "live")
            snapshot = self.write_snapshot(home, [self.live_session(live)])
            project_dir = home / ".claude" / "projects" / "new-project"
            project_dir.mkdir(parents=True)
            (project_dir / "new-session.jsonl").write_text(
                json.dumps({"cwd": str(home / "new-live")}) + "\n"
            )
            config_path, original = self.write_config(home, {
                "projects": {live: {}, str(home / "new-live"): {}},
            })

            with self.assertRaisesRegex(cleanup_config.CleanupError, "session_state_changed"):
                cleanup_config.cleanup(snapshot, home)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(cleanup_config.marker_path(snapshot).exists())

    def test_final_session_change_preserves_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            snapshot = self.write_snapshot(home, [])
            config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
            backup = home / ".claude.json.session-analyzer.bak"
            previous_backup = b"previous-backup"
            backup.write_bytes(previous_backup)
            current_calls = 0

            def changing_sessions(_home: Path):
                nonlocal current_calls
                current_calls += 1
                if current_calls == 2:
                    return {("new", "sid"): "/new"}
                return {}

            with mock.patch.object(cleanup_config, "_current_sessions", side_effect=changing_sessions):
                with self.assertRaisesRegex(cleanup_config.CleanupError, "session_state_changed"):
                    cleanup_config.cleanup(snapshot, home)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(backup.read_bytes(), previous_backup)

    def test_cleanup_marker_is_bound_to_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            snapshot = self.write_snapshot(home, [])
            self.write_config(home, {"projects": {}})
            cleanup_config.cleanup(snapshot, home)
            marker = cleanup_config.marker_path(snapshot)
            self.assertTrue(cleanup_config.verify_cleanup_marker(snapshot, home))
            self.assertFalse(cleanup_config.verify_cleanup_marker(snapshot, home / "other"))
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            config_path = home / ".claude.json"
            config_path.write_text('{"projects": {"/new-stale": {}}}')
            self.assertFalse(cleanup_config.verify_cleanup_marker(snapshot, home))
            config_path.write_text('{"projects": {}}')
            snapshot.write_text(snapshot.read_text() + " ")
            self.assertFalse(cleanup_config.verify_cleanup_marker(snapshot, home))

    def test_invalid_configs_fail_closed(self) -> None:
        cases = [
            b"not-json",
            b"[]",
            b'{"other": {}}',
            b'{"projects": []}',
            b'{"projects": {}, "projects": {"/duplicate": {}}}',
        ]
        for index, raw in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = self.write_snapshot(home, [])
                config_path = home / ".claude.json"
                config_path.write_bytes(raw)
                with self.assertRaisesRegex(cleanup_config.CleanupError, "malformed_config"):
                    cleanup_config.cleanup(snapshot, home)
                self.assertEqual(config_path.read_bytes(), raw)
                self.assertFalse((home / ".claude.json.session-analyzer.bak").exists())

    def test_precommit_and_final_read_conflicts_fail_closed(self) -> None:
        for conflict_read in (2, 3):
            with self.subTest(conflict_read=conflict_read), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = self.write_snapshot(home, [])
                config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
                concurrent = b'{"projects": {"/concurrent": {}}}\n'
                calls = 0

                def changing_read(path: Path) -> bytes:
                    nonlocal calls
                    calls += 1
                    if calls == conflict_read:
                        return concurrent
                    return original

                with mock.patch.object(cleanup_config, "_read_bytes", side_effect=changing_read):
                    with self.assertRaisesRegex(cleanup_config.CleanupError, "concurrent_change"):
                        cleanup_config.cleanup(snapshot, home)
                self.assertEqual(config_path.read_bytes(), original)
                self.assertFalse(cleanup_config.marker_path(snapshot).exists())

    def test_backup_and_write_failures_leave_config_unchanged(self) -> None:
        for failure_call, expected in ((1, "backup_error"), (2, "write_error")):
            with self.subTest(failure_call=failure_call), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                snapshot = self.write_snapshot(home, [])
                config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
                original_write_temp = cleanup_config._write_temp
                calls = 0

                def failing_write(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failure_call:
                        raise OSError("secret-path")
                    return original_write_temp(*args, **kwargs)

                with mock.patch.object(cleanup_config, "_write_temp", side_effect=failing_write):
                    with self.assertRaisesRegex(cleanup_config.CleanupError, expected):
                        cleanup_config.cleanup(snapshot, home)
                self.assertEqual(config_path.read_bytes(), original)

    def test_replace_failure_leaves_config_unchanged_and_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            snapshot = self.write_snapshot(home, [])
            config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
            real_replace = os.replace

            def fail_config_replace(src, dst):
                if Path(dst) == config_path:
                    raise OSError("do-not-print")
                return real_replace(src, dst)

            with mock.patch.object(cleanup_config.os, "replace", side_effect=fail_config_replace):
                with self.assertRaisesRegex(cleanup_config.CleanupError, "atomic_replace_error"):
                    cleanup_config.cleanup(snapshot, home)
            self.assertEqual(config_path.read_bytes(), original)
            leftovers = [p for p in home.iterdir() if p.name.startswith("..claude.json.")]
            self.assertEqual(leftovers, [])

    def test_backup_install_failure_rolls_back_config_and_preserves_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            snapshot = self.write_snapshot(home, [])
            config_path, original = self.write_config(home, {"projects": {"/stale": {}}})
            backup = home / ".claude.json.session-analyzer.bak"
            previous_backup = b"previous-backup"
            backup.write_bytes(previous_backup)
            real_replace = os.replace

            def fail_backup_replace(src, dst):
                if Path(dst) == backup:
                    raise OSError("do-not-print")
                return real_replace(src, dst)

            with mock.patch.object(cleanup_config.os, "replace", side_effect=fail_backup_replace):
                with self.assertRaisesRegex(cleanup_config.CleanupError, "backup_error"):
                    cleanup_config.cleanup(snapshot, home)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(backup.read_bytes(), previous_backup)
            self.assertFalse(cleanup_config.marker_path(snapshot).exists())

    def test_no_change_branch_detects_config_change_before_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            live = str(home / "live")
            snapshot = self.write_snapshot(home, [self.live_session(live)])
            config_path, original = self.write_config(home, {"projects": {live: {}}})
            concurrent = b'{"projects": {"/new-stale": {}}}\n'
            reads = iter((original, concurrent))
            with mock.patch.object(cleanup_config, "_read_bytes", side_effect=lambda path: next(reads)):
                with self.assertRaisesRegex(cleanup_config.CleanupError, "concurrent_change"):
                    cleanup_config.cleanup(snapshot, home)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(cleanup_config.marker_path(snapshot).exists())

    def test_backup_is_bounded_to_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            snapshot = self.write_snapshot(home, [])
            config_path, first = self.write_config(home, {"projects": {"/one": {}}})
            cleanup_config.cleanup(snapshot, home)
            backup = home / ".claude.json.session-analyzer.bak"
            self.assertEqual(backup.read_bytes(), first)

            second = b'{"projects": {"/two": {}}}\n'
            config_path.write_bytes(second)
            cleanup_config.cleanup(snapshot, home)
            backups = list(home.glob(".claude.json.session-analyzer.bak*"))
            self.assertEqual(backups, [backup])
            self.assertEqual(backup.read_bytes(), second)

    def test_main_output_does_not_reveal_paths_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            secret_path = str(home / "secret-project-key")
            snapshot = self.write_snapshot(home, [])
            self.write_config(home, {
                "projects": {secret_path: {"mcp": "mcp-secret"}},
                "oauth": "oauth-secret",
            })
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(cleanup_config, "HOME", home), mock.patch.object(
                sys, "argv", ["cleanup_claude_config.py", str(snapshot)]
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cleanup_config.main(), 0)
            output = stdout.getvalue() + stderr.getvalue()
            self.assertIn("removed=1", output)
            for secret in (secret_path, "mcp-secret", "oauth-secret"):
                self.assertNotIn(secret, output)


if __name__ == "__main__":
    unittest.main()
