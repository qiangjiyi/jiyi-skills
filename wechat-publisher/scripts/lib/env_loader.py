"""Load .env files with precedence: source dir → skill dir → process env."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def merged_env(base_dir: Path, env_file: str | None, skill_dir: Path) -> tuple[dict[str, str], Path | None]:
    """Look for .env in: explicit path → base_dir/.env.local → base_dir/.env → skill_dir/.env.local → skill_dir/.env.

    Returns the merged env dict and the path that was loaded (or None).
    """
    candidates: list[Path] = []
    if env_file:
        candidates.append(Path(env_file).expanduser())
    else:
        candidates.extend([
            base_dir / ".env.local",
            base_dir / ".env",
            skill_dir / ".env.local",
            skill_dir / ".env",
        ])
    file_env: dict[str, str] = {}
    used: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            file_env = load_dotenv(candidate)
            used = candidate
            break
    env = dict(os.environ)
    env.update(file_env)
    return env, used
