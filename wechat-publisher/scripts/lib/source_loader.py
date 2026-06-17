"""Source file loaders for newspic mode: .md / .yaml / .json."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import CONTENT_MAX_CHARS, MAX_IMAGES, PublishError, TITLE_MAX_CHARS


def parse_scalar(value: str):
    value = value.strip()
    if value in ("", "null", "Null", "NULL", "~"):
        return None
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_simple_yaml(text: str) -> dict:
    data: dict[str, object] = {}
    current_list: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            if current_list and raw_line.strip().startswith("- "):
                item = raw_line.strip()[2:].strip()
                data.setdefault(current_list, [])
                assert isinstance(data[current_list], list)
                data[current_list].append(parse_scalar(item))
                continue
            raise PublishError(f"unsupported YAML line: {raw_line}")
        if ":" not in raw_line:
            raise PublishError(f"unsupported YAML line: {raw_line}")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            data[key] = []
            current_list = key
        else:
            data[key] = parse_scalar(value)
            current_list = None
    return data


def split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def parse_markdown(text: str) -> dict:
    """Parse source.md format: optional YAML frontmatter, then first H1 = title, rest = content."""
    frontmatter, body = split_frontmatter(text)
    data = parse_simple_yaml(frontmatter) if frontmatter.strip() else {}
    body_lines = body.splitlines()
    title = None
    content_start = 0
    for idx, line in enumerate(body_lines):
        if not line.strip():
            continue
        if line.lstrip().startswith("# "):
            title = line.lstrip()[2:].strip()
            content_start = idx + 1
        else:
            content_start = idx
        break
    content = "\n".join(body_lines[content_start:]).strip()
    if title and not data.get("title"):
        data["title"] = title
    if content and not data.get("content"):
        data["content"] = content
    return data


def load_source(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        data = parse_simple_yaml(text)
    elif suffix in (".md", ".markdown"):
        data = parse_markdown(text)
    else:
        raise PublishError("source must be .md, .yaml, .yml, or .json")
    if not isinstance(data, dict):
        raise PublishError("source must contain an object at the top level")
    return data


def validate_newspic_source(source: dict, base_dir: Path) -> tuple[str, str, str, str, list[Path]]:
    """Validate the newspic source fields. Returns (title, content, author, digest, images)."""
    title = str(source.get("title") or "").strip()
    content = str(source.get("content") or "").strip()
    author = str(source.get("author") or "").strip()
    digest = str(source.get("digest") or "").strip()
    images_raw = source.get("images")
    if not title:
        raise PublishError("title is required")
    if len(title) > TITLE_MAX_CHARS:
        raise PublishError(f"title must be at most {TITLE_MAX_CHARS} characters, got {len(title)}")
    if not content:
        raise PublishError("content is required")
    if not isinstance(images_raw, list):
        raise PublishError("images must be a list")
    if not 1 <= len(images_raw) <= MAX_IMAGES:
        raise PublishError(f"images must contain 1-{MAX_IMAGES} paths")
    images: list[Path] = []
    for item in images_raw:
        p = Path(str(item)).expanduser()
        if not p.is_absolute():
            p = base_dir / p
        if not p.exists() or not p.is_file():
            raise PublishError(f"image not found: {p}")
        images.append(p)
    return title, content[:CONTENT_MAX_CHARS], author, digest, images
