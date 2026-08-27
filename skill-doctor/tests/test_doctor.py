from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from doctor import scan_source, session_metadata  # noqa: E402


class DoctorTests(unittest.TestCase):
    def test_claude_fixture_detects_native_skill(self) -> None:
        trace = session_metadata(ROOT / "tests" / "fixtures" / "claude-sample.jsonl")
        self.assertEqual(len(trace["calls"]), 2)
        self.assertEqual(len([call for call in trace["calls"] if call["native_skill_call"]]), 1)
        self.assertEqual(trace["calls"][0]["skill"], "jiyi-little-dancer-illustrations")

    def test_codex_fixture_detects_native_skill(self) -> None:
        trace = session_metadata(ROOT / "tests" / "fixtures" / "codex-sample.jsonl")
        native = [call for call in trace["calls"] if call["native_skill_call"]]
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0]["skill"], "baoyu-format-markdown")

    def test_source_scanner_does_not_flag_prohibited_fallback_as_risk(self) -> None:
        target = Path("/Users/jiyi/Projects/active/jiyi-skills/wechat-article-production")
        findings = scan_source(target)["findings"]
        self.assertNotIn("SRC-003", {finding["id"] for finding in findings})


if __name__ == "__main__":
    unittest.main()
