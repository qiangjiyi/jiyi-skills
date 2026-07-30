from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_report = load_module("session_analyzer_build_report", SCRIPTS / "build_report.py")
server = load_module("session_analyzer_server", SCRIPTS / "server.py")
cleanup = sys.modules["cleanup_claude_config"]


class ReportGateTest(unittest.TestCase):
    def write_snapshot(self, directory: Path) -> Path:
        path = directory / "snapshot.json"
        path.write_text(json.dumps({"home": str(directory), "agents": []}))
        path.chmod(0o600)
        return path

    def test_static_report_requires_matching_cleanup_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self.write_snapshot(root)
            output = root / "report.html"
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", ["build_report.py", str(snapshot), str(output)]), \
                    contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    build_report.main()
            self.assertEqual(raised.exception.code, 1)
            self.assertFalse(output.exists())
            self.assertIn("配置清理未成功", stderr.getvalue())

            config = root / ".claude.json"
            config.write_text('{"projects": {}}')
            cleanup._write_success_marker(
                snapshot,
                root,
                cleanup._config_state(config),
            )
            with mock.patch.object(cleanup, "HOME", root), \
                    mock.patch.object(sys, "argv", ["build_report.py", str(snapshot), str(output)]):
                build_report.main()
            self.assertTrue(output.exists())

    def test_server_requires_matching_cleanup_marker_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self.write_snapshot(Path(tmp))
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", ["server.py", str(snapshot)]), \
                    mock.patch.object(server, "load") as load, \
                    contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    server.main()
            self.assertEqual(raised.exception.code, 1)
            load.assert_not_called()
            self.assertIn("配置清理未成功", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
