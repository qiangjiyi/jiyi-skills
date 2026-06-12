#!/usr/bin/env python3
"""Inject a scan JSON into the HTML template -> a standalone read-only report.

Usage:
    build_report.py <scan.json> [output.html]

Produces a shareable / archivable static report. It has NO delete buttons: a
file:// page can't reach a local server, so the template renders read-only when
DELETE config is null. For one-click cleanup, use server.py instead.
"""
import json
import os
import sys
from pathlib import Path

# HTML template (relative to this file: ../assets/report_template.html).
TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "report_template.html"


def render_readonly(data: dict) -> str:
    """Inject scan data into the HTML template as a read-only report.

    DELETE config is null, so the template renders without delete buttons — a
    file:// page can't reach the local server anyway. For one-click cleanup,
    serve the same template via server.py (which injects a real config).
    """
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(data, ensure_ascii=False)
    return tpl.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", "null")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/Desktop/session-report.html")

    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    html = render_readonly(data)

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"只读报告已生成: {out}")
    print(f"打开: open '{out}'")


if __name__ == "__main__":
    main()
