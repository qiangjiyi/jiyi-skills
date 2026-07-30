#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan as scan_module  # noqa: E402


HOME = Path.home()
CONFIG_NAME = ".claude.json"
BACKUP_SUFFIX = ".session-analyzer.bak"
MARKER_SUFFIX = ".claude-config-clean"


class CleanupError(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class CleanupResult:
    removed: int
    live_cwds: int
    backup_created: bool


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CleanupError("malformed_json")
        result[key] = value
    return result


def _load_json(raw: bytes, category: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except CleanupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CleanupError(category) from None


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.normpath(os.path.expanduser(path)))


def _validate_private_regular_file(path: Path, category: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            with os.fdopen(fd, "rb", closefd=False) as f:
                raw = f.read()
        finally:
            os.close(fd)
    except OSError:
        raise CleanupError(category) from None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise CleanupError(category)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise CleanupError(category)
    return raw


def _snapshot_sessions(snapshot_path: Path, home: Path) -> dict[tuple[str, str], str]:
    raw = _validate_private_regular_file(snapshot_path, "invalid_scan_snapshot")
    try:
        snapshot = _load_json(raw, "invalid_scan_snapshot")
    except CleanupError:
        raise CleanupError("invalid_scan_snapshot") from None
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("agents"), list)
        or not isinstance(snapshot.get("home"), str)
        or _normalize_path(snapshot["home"]) != _normalize_path(str(home))
    ):
        raise CleanupError("invalid_scan_snapshot")

    claude_agents = [
        agent for agent in snapshot["agents"]
        if isinstance(agent, dict) and agent.get("key") == "claude"
    ]
    if len(claude_agents) != 1:
        raise CleanupError("invalid_scan_snapshot")
    claude = claude_agents[0]
    installed = claude.get("installed")
    if not isinstance(installed, bool):
        raise CleanupError("invalid_scan_snapshot")
    projects = claude.get("projects")
    if not isinstance(projects, list) or (not installed and projects):
        raise CleanupError("invalid_scan_snapshot")

    sessions: dict[tuple[str, str], str] = {}
    for project in projects:
        if (
            not isinstance(project, dict)
            or not isinstance(project.get("id"), str)
            or not isinstance(project.get("sessions"), list)
        ):
            raise CleanupError("invalid_scan_snapshot")
        for session in project["sessions"]:
            if not isinstance(session, dict) or not isinstance(session.get("extra"), dict):
                raise CleanupError("invalid_scan_snapshot")
            extra = session["extra"]
            kind = extra.get("claude_kind")
            if kind == "orphan_dir":
                continue
            sid = session.get("id")
            cwd = extra.get("cwd")
            if (
                kind != "session"
                or not isinstance(sid, str)
                or not sid
                or not isinstance(cwd, str)
                or not cwd.strip()
            ):
                raise CleanupError("invalid_scan_snapshot")
            key = (project["id"], sid)
            if key in sessions:
                raise CleanupError("invalid_scan_snapshot")
            sessions[key] = _normalize_path(cwd.strip())
    return sessions


def _current_sessions(home: Path) -> dict[tuple[str, str], str]:
    projects_dir = home / ".claude" / "projects"
    if not projects_dir.exists():
        return {}
    try:
        project_dirs = sorted(path for path in projects_dir.iterdir() if path.is_dir())
    except OSError:
        raise CleanupError("session_state_changed") from None
    history = scan_module._claude_history_projects(home / ".claude" / "history.jsonl")
    sessions: dict[tuple[str, str], str] = {}
    try:
        for project_dir in project_dirs:
            fallback = scan_module._resolve_claude_path(project_dir.name)
            for path in sorted(project_dir.glob("*.jsonl")):
                sid = path.stem
                cwd = scan_module._claude_jsonl_cwd(path) or history.get(sid, "") or fallback
                if not cwd:
                    raise CleanupError("session_state_changed")
                sessions[(project_dir.name, sid)] = _normalize_path(cwd)
    except OSError:
        raise CleanupError("session_state_changed") from None
    return sessions


def load_live_cwds(snapshot_path: Path, home: Path) -> set[str]:
    snapshot_sessions = _snapshot_sessions(snapshot_path, home)
    if _current_sessions(home) != snapshot_sessions:
        raise CleanupError("session_state_changed")
    return set(snapshot_sessions.values())


def _parse_config(raw: bytes) -> dict[str, Any]:
    try:
        config = _load_json(raw, "malformed_config")
    except CleanupError:
        raise CleanupError("malformed_config") from None
    if not isinstance(config, dict) or not isinstance(config.get("projects"), dict):
        raise CleanupError("malformed_config")
    return config


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _filter_projects(config: dict[str, Any], live_cwds: set[str]) -> tuple[dict[str, Any], int]:
    projects = config["projects"]
    kept = {
        key: value
        for key, value in projects.items()
        if _normalize_path(key) in live_cwds
    }
    filtered = dict(config)
    filtered["projects"] = kept
    return filtered, len(projects) - len(kept)


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_temp(directory: Path, prefix: str, raw: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    path = Path(name)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _atomic_write(path: Path, raw: bytes) -> None:
    temp_path = _write_temp(path.parent, f".{path.name}.", raw)
    try:
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def marker_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(snapshot_path.name + MARKER_SUFFIX)


def _snapshot_digest(snapshot_path: Path) -> str:
    raw = _validate_private_regular_file(snapshot_path, "invalid_scan_snapshot")
    return hashlib.sha256(raw).hexdigest()


def _config_state(path: Path) -> str:
    try:
        raw = _read_bytes(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        raise CleanupError("config_read_error") from None
    _parse_config(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_success_marker(snapshot_path: Path, home: Path, config_state: str) -> None:
    raw = (json.dumps({
        "snapshot_sha256": _snapshot_digest(snapshot_path),
        "home": _normalize_path(str(home)),
        "config_state": config_state,
    }) + "\n").encode()
    _atomic_write(marker_path(snapshot_path), raw)


def verify_cleanup_marker(snapshot_path: Path, home: Path | None = None) -> bool:
    base = HOME if home is None else home
    try:
        raw = _validate_private_regular_file(marker_path(snapshot_path), "cleanup_not_completed")
        marker = _load_json(raw, "cleanup_not_completed")
        return (
            isinstance(marker, dict)
            and marker.get("snapshot_sha256") == _snapshot_digest(snapshot_path)
            and marker.get("home") == _normalize_path(str(base))
            and marker.get("config_state") == _config_state(base / CONFIG_NAME)
        )
    except CleanupError:
        return False


def cleanup(snapshot_path: Path, home: Path | None = None) -> CleanupResult:
    base = HOME if home is None else home
    initial_sessions = _snapshot_sessions(snapshot_path, base)
    try:
        marker_path(snapshot_path).unlink(missing_ok=True)
    except OSError:
        raise CleanupError("marker_error") from None
    if _current_sessions(base) != initial_sessions:
        raise CleanupError("session_state_changed")
    live_cwds = set(initial_sessions.values())
    config_path = base / CONFIG_NAME
    backup_path = base / f"{CONFIG_NAME}{BACKUP_SUFFIX}"

    try:
        baseline = _read_bytes(config_path)
    except FileNotFoundError:
        if _current_sessions(base) != initial_sessions or config_path.exists():
            raise CleanupError("concurrent_change")
        result = CleanupResult(removed=0, live_cwds=len(live_cwds), backup_created=False)
        _write_success_marker(snapshot_path, base, "missing")
        return result
    except OSError:
        raise CleanupError("config_read_error") from None

    config = _parse_config(baseline)
    _, initial_removed = _filter_projects(config, live_cwds)
    if initial_removed == 0:
        if _current_sessions(base) != initial_sessions:
            raise CleanupError("session_state_changed")
        try:
            final_current = _read_bytes(config_path)
        except OSError:
            raise CleanupError("concurrent_change") from None
        if final_current != baseline:
            raise CleanupError("concurrent_change")
        result = CleanupResult(removed=0, live_cwds=len(live_cwds), backup_created=False)
        _write_success_marker(snapshot_path, base, hashlib.sha256(final_current).hexdigest())
        return result

    try:
        latest = _read_bytes(config_path)
    except OSError:
        raise CleanupError("concurrent_change") from None
    if latest != baseline:
        raise CleanupError("concurrent_change")
    filtered, removed = _filter_projects(_parse_config(latest), live_cwds)
    if removed == 0:
        result = CleanupResult(removed=0, live_cwds=len(live_cwds), backup_created=False)
        _write_success_marker(snapshot_path, base, hashlib.sha256(latest).hexdigest())
        return result

    replacement = (json.dumps(filtered, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        backup_temp = _write_temp(backup_path.parent, f".{backup_path.name}.", latest)
    except OSError:
        raise CleanupError("backup_error") from None
    try:
        try:
            config_temp = _write_temp(config_path.parent, f".{config_path.name}.", replacement)
        except OSError:
            raise CleanupError("write_error") from None
        try:
            try:
                final_current = _read_bytes(config_path)
            except OSError:
                raise CleanupError("concurrent_change") from None
            if final_current != baseline:
                raise CleanupError("concurrent_change")
            _parse_config(final_current)
            if _current_sessions(base) != initial_sessions:
                raise CleanupError("session_state_changed")
            try:
                os.replace(config_temp, config_path)
                try:
                    os.replace(backup_temp, backup_path)
                except OSError:
                    try:
                        rollback_temp = _write_temp(config_path.parent, f".{config_path.name}.rollback.", latest)
                        os.replace(rollback_temp, config_path)
                    except OSError:
                        raise CleanupError("rollback_error") from None
                    finally:
                        if "rollback_temp" in locals():
                            rollback_temp.unlink(missing_ok=True)
                    raise CleanupError("backup_error") from None
                _fsync_directory(config_path.parent)
            except CleanupError:
                raise
            except OSError:
                raise CleanupError("atomic_replace_error") from None
        finally:
            config_temp.unlink(missing_ok=True)
    finally:
        backup_temp.unlink(missing_ok=True)

    result = CleanupResult(removed=removed, live_cwds=len(live_cwds), backup_created=True)
    try:
        _write_success_marker(
            snapshot_path,
            base,
            hashlib.sha256(replacement).hexdigest(),
        )
    except OSError:
        raise CleanupError("marker_error") from None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="清理没有真实 Claude session 的全局项目配置")
    parser.add_argument("snapshot", type=Path, help="scan.py 生成的 JSON 快照")
    args = parser.parse_args()
    try:
        result = cleanup(args.snapshot)
    except CleanupError as exc:
        print(f"[claude-config] failed: {exc.category}", file=sys.stderr)
        return 1
    except OSError:
        print("[claude-config] failed: marker_error", file=sys.stderr)
        return 1

    if result.removed:
        print(
            f"[claude-config] completed: removed={result.removed} "
            f"live_cwds={result.live_cwds} backup=created",
            file=sys.stderr,
        )
    else:
        print(
            f"[claude-config] no changes: removed=0 live_cwds={result.live_cwds}",
            file=sys.stderr,
        )
    print("✓ DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
