#!/usr/bin/env python3
"""Deterministic source and execution diagnostics for composed Agent Skills.

The script is deliberately read-only with respect to targets, sessions and
artifacts. It emits redacted evidence and recommendations; patching remains a
separate, user-confirmed operation handled by the parent agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VERSION = "0.1.0"
STAGES = {"prepare", "format", "cover", "illustrate", "typeset", "validate", "publish"}
SKILL_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z0-9]*(?:-[a-z0-9]+){1,})(?![A-Za-z0-9_])")
PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|markdown|json|ya?ml|html?|png|jpe?g|webp|csv|toml|sqlite)(?![A-Za-z0-9_])", re.I)
SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:api[_-]?key|access[_-]?token|client[_-]?secret|app[_-]?secret|cookie|authorization)\s*[:=]\s*)[^\s,;]+"
)
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
GENERIC_NAMES = {
    "skill", "skills", "stage", "stages", "source", "target", "output", "inputs", "reports",
    "execution", "workflow", "pipeline", "doctor", "article", "image", "images", "prompt",
}
NON_SKILL_PREFIXES = (
    "allow-", "article-", "execution-", "illustration-", "invocation-", "little-dancer-reference-",
    "manifest-", "native-", "package-", "stage-", "upload-", "validate-", "validation-",
    "create-", "publish-", "run-", "tool-",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(value: Any, limit: int = 280) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = SECRET_RE.sub(lambda m: (m.group(1) or m.group(2) or "") + "[REDACTED]", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})", "[REDACTED]", text)
    text = UUID_RE.sub("[UUID]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def read_text(path: Path, limit: int = 4_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except (OSError, UnicodeError):
        return ""


def iter_source_files(target: Path) -> list[Path]:
    files: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        relative = path.relative_to(target)
        if relative.parts[0] == "docs":
            continue
        if relative == Path("SKILL.md") or relative.parts[0] in {"agents", "references", "scripts", "tests"}:
            files.append(path)
    return sorted(files)


def frontmatter_name(skill_file: Path) -> str:
    text = read_text(skill_file, 20_000)
    match = re.search(r"(?m)^name:\s*([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else skill_file.parent.name


def line_evidence(path: Path, root: Path, line_no: int, line: str, kind: str) -> dict[str, Any]:
    return {
        "file": rel(path, root),
        "line": line_no,
        "kind": kind,
        "excerpt": redact(line, 220),
    }


def candidate_skills(line: str, current: str) -> set[str]:
    found: set[str] = set()
    patterns = [
        r"(?:--skill|--invoked-skill|skill\s*[:=]|Skill\s*[:=(]?)\s*[`\"']?([a-z][a-z0-9]*(?:-[a-z0-9]+)+)",
        r"\$([a-z][a-z0-9]*(?:-[a-z0-9]+)+)",
    ]
    for pattern in patterns:
        found.update(re.findall(pattern, line, flags=re.I))
    for value in SKILL_NAME_RE.findall(line):
        if (
            value not in GENERIC_NAMES
            and not value.startswith(NON_SKILL_PREFIXES)
            and not any(value.endswith(suffix) for suffix in ("-contract", "-schema", "-report", "-reference", "-handoff"))
            and ("skill" in line.lower() or "调用" in line or "invoke" in line.lower())
        ):
            found.add(value)
    found.discard(current)
    return found


def scan_source(target: Path) -> dict[str, Any]:
    skill_file = target / "SKILL.md"
    current = frontmatter_name(skill_file) if skill_file.exists() else target.name
    files = iter_source_files(target)
    skills: dict[str, dict[str, Any]] = {}
    tools: dict[str, list[dict[str, Any]]] = {}
    artifacts: dict[str, list[dict[str, Any]]] = {}
    edges: list[dict[str, Any]] = []
    stages: dict[str, list[dict[str, Any]]] = {stage: [] for stage in sorted(STAGES)}
    manual_evidence: list[dict[str, Any]] = []
    native_evidence: list[dict[str, Any]] = []
    parallel_evidence: list[dict[str, Any]] = []
    failure_evidence: list[dict[str, Any]] = []
    all_text = ""

    for path in files:
        text = read_text(path)
        all_text += "\n" + text
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            evidence_base = line_evidence(path, target, number, line, "source")
            names = candidate_skills(line, current)
            for name in sorted(names):
                skills.setdefault(name, {"name": name, "evidence": []})["evidence"].append(evidence_base)
                if re.search(r"\bSkill\b|原生|native|invoke|调用", line, re.I):
                    edges.append({"from": current, "to": name, "relation": "declared-call", "evidence": evidence_base})
            if re.search(r"\bSkill\b|native[-_ ]skill|原生.*调用|调用.*Skill", line, re.I):
                native_evidence.append(evidence_base)
            manual_signal = re.search(r"manual|simulate|mock|手工|模拟|代替|补写|降级|主 Agent.*(?:生成|写|插入)", line, re.I)
            guarded_fallback = re.search(r"fallback", line, re.I) and re.search(r"manual|授权|authorization|允许|降级|模拟|手工", line, re.I)
            if manual_signal or guarded_fallback:
                prohibitive = re.search(r"不要|不得|不允许|不可|不能|禁止|不手写|不补写|不插入|不可冒充|must not|do not|not count|不算|仅.*授权", line, re.I)
                explicitly_authorized = re.search(r"authorization|authorized|授权|allow[-_]?manual[-_]?fallback|manualfallback|显式|明确", line, re.I)
                schema_declaration = path.suffix.lower() in {".json", ".yaml", ".yml"} or path.name.endswith(".schema.json")
                if not prohibitive and not explicitly_authorized and not schema_declaration:
                    manual_evidence.append(evidence_base)
            if re.search(r"parallel|Promise\.all|gather|并行|并发|并行分支", line, re.I):
                parallel_evidence.append(evidence_base)
            if re.search(r"failed|failure|blocked|retry|error|失败|阻塞|重试|错误", line, re.I):
                failure_evidence.append(evidence_base)
            for stage in STAGES:
                if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(stage)}(?![A-Za-z0-9_-])", line, re.I):
                    stages[stage].append(evidence_base)
            for artifact in sorted(set(PATH_RE.findall(line))):
                artifacts.setdefault(artifact, []).append(evidence_base)
            tool_matches = re.findall(r"(?:mcp__[-A-Za-z0-9_]+|\b(?:node|python3?|bash|ruby|sqlite3)\b)", line)
            for tool in sorted(set(tool_matches)):
                tools.setdefault(tool, []).append(evidence_base)

    findings: list[dict[str, Any]] = []
    def finding(code: str, severity: str, title: str, impact: str, evidence: list[dict[str, Any]], recommendation: str) -> None:
        findings.append({
            "id": code,
            "severity": severity,
            "confidence": "medium",
            "evidence_type": "source-inferred",
            "title": title,
            "impact": impact,
            "evidence": evidence[:12],
            "recommendation": recommendation,
        })

    child_names = sorted(skills)
    if not skill_file.exists():
        finding("SRC-001", "P0", "缺少 SKILL.md", "无法确认目标 Skill 的入口契约", [], "补充合法 frontmatter 和最小执行契约后再诊断")
    if child_names and not native_evidence:
        finding("SRC-002", "P1", "发现子 Skill 名称但没有明确原生 Skill 调度证据", "父级可能只读取、模仿或间接调用子 Skill", [e for name in child_names for e in skills[name]["evidence"]], "要求运行时原生 Skill 调用，并记录调用证据、输入和 handoff")
    if manual_evidence and child_names:
        finding("SRC-003", "P1", "源码包含手工 fallback 或模拟执行路径", "子 Skill 失败后可能被父级静默替代，导致业务逻辑未执行", manual_evidence, "将 fallback 明确标记为 blocked/failed；只有用户授权的外部降级才能单独记录")
    output_words = re.findall(r"completed|stage-complete|完成|产物|handoff|outputs?", all_text, re.I)
    check_words = re.findall(r"exists|stat|validate|校验|检查|manifest|非空|真实存在", all_text, re.I)
    if output_words and len(check_words) < 2:
        finding("SRC-004", "P1", "产物完成语义明显多于确定性存在性校验", "Agent 可能仅凭声明或路径登记把阶段判定为完成", [], "把输出检查、文件类型/非空、路径边界和失败状态交给脚本验证")
    if parallel_evidence and not re.search(r"join|gather|Promise\.all|汇聚|全部.*完成|all.*result", all_text, re.I):
        finding("SRC-005", "P1", "发现并行执行描述但没有明确汇聚或部分失败规则", "下游可能在分支未完成时继续，或丢失部分失败信息", parallel_evidence, "定义并行分支的唯一 handoff、汇聚条件、超时和失败传播")
    if child_names and not failure_evidence:
        finding("SRC-006", "P2", "没有发现明确的失败、阻塞或重试契约", "子 Skill 报错时父级可能继续执行或伪造完成", [], "为每个原生子 Skill 调用定义失败传播和可重试边界")
    if not artifacts and child_names:
        finding("SRC-007", "P2", "没有发现可识别的文件型 handoff", "父子 Skill 之间可能依赖隐式上下文，难以证明执行完整", [], "为关键阶段定义稳定、可检查的 handoff 或机器可读 manifest")

    return {
        "schema_version": "1.0",
        "doctor_version": VERSION,
        "generated_at": now_iso(),
        "mode": "source",
        "target": str(target),
        "skill": current,
        "files": [{"path": rel(path, target), "bytes": path.stat().st_size} for path in files if path.exists()],
        "skills": sorted(skills.values(), key=lambda item: item["name"]),
        "edges": edges,
        "tools": [{"name": name, "evidence": evidence[:12]} for name, evidence in sorted(tools.items())],
        "artifacts": [{"path": name, "evidence": evidence[:12]} for name, evidence in sorted(artifacts.items())],
        "stages": {name: evidence[:12] for name, evidence in stages.items() if evidence},
        "control_flow": {
            "native_call_evidence": native_evidence[:20],
            "manual_fallback_evidence": manual_evidence[:20],
            "parallel_evidence": parallel_evidence[:20],
            "failure_evidence": failure_evidence[:20],
        },
        "findings": findings,
        "limitations": [
            "源码扫描只能推断意图，不能证明某次运行真的调用了子 Skill。",
            "未提供执行 Session 时，不对实际 Tool/Skill 调用下结论。",
        ],
    }


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def input_summary(tool: str, raw_value: Any, params: dict[str, Any]) -> tuple[str, str]:
    raw = json.dumps(raw_value, ensure_ascii=False, sort_keys=True, default=str) if not isinstance(raw_value, str) else raw_value
    fingerprint = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe_keys = sorted(str(key) for key in params)
    safe_fields: dict[str, Any] = {}
    for key in ("skill", "stage", "mode", "agent", "path", "package", "inputs", "outputs"):
        value = params.get(key)
        if value is not None and isinstance(value, (str, int, float, bool, list)):
            safe_fields[key] = redact(value, 120)
    if tool.lower() in {"exec", "bash", "shell", "computer", "computer-use"}:
        summary = f"opaque tool input; keys={safe_keys[:20]}"
    else:
        summary = redact({"fields": safe_fields, "keys": safe_keys[:20]}, 260)
    return summary, fingerprint


def timestamp_of(record: dict[str, Any]) -> str | None:
    for key in ("timestamp", "created_at", "updated_at"):
        value = record.get(key)
        if value is not None:
            return str(value)
    payload = record.get("payload")
    if isinstance(payload, dict):
        for key in ("timestamp", "created_at", "started_at", "completed_at"):
            if payload.get(key) is not None:
                return str(payload[key])
    return None


def extract_calls(record: dict[str, Any], line_no: int) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    timestamp = timestamp_of(record)

    def add_call(tool: Any, input_value: Any, call_id: Any, kind: str, status: Any = None) -> None:
        tool_name = str(tool or "unknown")
        params = json_object(input_value)
        skill = params.get("skill") or params.get("invoked_skill") or params.get("name")
        if not isinstance(skill, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", skill):
            skill = None
        summary, fingerprint = input_summary(tool_name, input_value, params)
        calls.append({
            "line": line_no,
            "timestamp": timestamp,
            "tool": tool_name,
            "kind": kind,
            "id": redact(call_id, 100) if call_id else None,
            "skill": skill,
            "native_skill_call": tool_name == "Skill" or tool_name.lower() == "skill",
            "status": redact(status, 80) if status is not None else None,
            "input_summary": summary,
            "input_fingerprint": fingerprint,
        })

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        block_type = str(node.get("type", ""))
        event_like = any(key in node for key in ("name", "tool_name", "input", "arguments", "output", "content", "tool_use_id", "function", "call_id"))
        if block_type in {"tool_use", "custom_tool_call", "function_call", "tool_call"} and event_like:
            add_call(node.get("name") or node.get("tool_name"), node.get("input") or node.get("arguments") or node.get("function"), node.get("id") or node.get("call_id"), block_type, node.get("status"))
        elif block_type in {"tool_result", "custom_tool_call_output", "function_call_output", "tool_call_output"} and event_like:
            calls.append({
                "line": line_no,
                "timestamp": timestamp,
                "tool": str(node.get("name") or node.get("tool_name") or "tool-result"),
                "kind": block_type,
                "id": redact(node.get("id") or node.get("call_id"), 100),
                "skill": None,
                "native_skill_call": False,
                "status": "result",
                "input_summary": "tool result omitted; see status and event type",
                "input_fingerprint": hashlib.sha256(redact(node.get("output") or node.get("content") or node.get("result"), 500).encode("utf-8")).hexdigest()[:16],
            })
        for key, value in node.items():
            if key in {"input", "arguments", "output", "result", "function"}:
                continue
            if key == "content" and block_type in {"tool_use", "custom_tool_call", "function_call", "tool_call", "tool_result", "custom_tool_call_output", "function_call_output", "tool_call_output"}:
                continue
            if key not in {"type", "name", "tool_name", "id", "call_id", "status"}:
                visit(value)
    visit(record)
    return calls


def session_metadata(path: Path) -> dict[str, Any]:
    first: dict[str, Any] = {}
    line_count = 0
    calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                line_count = line_no
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    errors.append({"line": line_no, "kind": "invalid-json"})
                    continue
                if not first:
                    first = record if isinstance(record, dict) else {}
                calls.extend(extract_calls(record, line_no))
                flat = redact(line, 500)
                if re.search(r"\b(error|failed|failure|blocked|exception)\b|错误|失败|阻塞", flat, re.I):
                    errors.append({"line": line_no, "kind": "error-like-event", "excerpt": flat})
    except OSError as exc:
        return {"path": str(path), "readable": False, "error": str(exc)}
    payload = first.get("payload", {}) if isinstance(first, dict) else {}
    return {
        "path": str(path),
        "readable": True,
        "bytes": path.stat().st_size if path.exists() else 0,
        "line_count": line_count,
        "agent": "codex" if path.name.startswith("rollout-") or isinstance(payload, dict) and payload.get("type") else "claude-or-unknown",
        "calls": calls,
        "errors": errors[:100],
        "first_record_summary": {
            "type": first.get("type") if isinstance(first, dict) else None,
            "payload_type": first.get("payload", {}).get("type") if isinstance(first.get("payload"), dict) else None,
            "keys": sorted(first.keys()) if isinstance(first, dict) else [],
        },
    }


def resolve_codex_thread(thread_id: str, db_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"thread_id": thread_id, "db_path": str(db_path), "readable": False}
    try:
        uri = f"file:{db_path}?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT id, rollout_path, cwd, title, source, updated_at FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        result["error"] = redact(str(exc))
        return result
    if row is None:
        result["error"] = "thread not found"
        return result
    result.update({
        "readable": True,
        "rollout_path": str(Path(row["rollout_path"]).expanduser()) if row["rollout_path"] else None,
        "cwd": row["cwd"],
        "title": redact(row["title"], 180),
        "source": redact(row["source"], 80),
        "updated_at": row["updated_at"],
    })
    return result


def find_scan_session(scan_path: Path | None, session_id: str | None) -> dict[str, Any] | None:
    if not scan_path or not scan_path.exists() or not session_id:
        return None
    try:
        data = json.loads(read_text(scan_path))
    except json.JSONDecodeError:
        return None
    for agent in data.get("agents", []) if isinstance(data, dict) else []:
        for project in agent.get("projects", []) if isinstance(agent, dict) else []:
            for session in project.get("sessions", []) if isinstance(project, dict) else []:
                if str(session.get("id")) == session_id:
                    return {
                        "agent": agent.get("key"),
                        "project": redact(project.get("real_path") or project.get("label"), 180),
                        "session": {key: session.get(key) for key in ("id", "title", "mtime", "size")},
                    }
    return None


def package_evidence(package: Path | None) -> dict[str, Any]:
    if package is None:
        return {"provided": False, "files": [], "manifests": []}
    if not package.exists():
        return {"provided": True, "path": str(package), "readable": False, "error": "package not found", "files": [], "manifests": []}
    files: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            item = {"path": rel(path, package), "bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
        except OSError:
            continue
        files.append(item)
        if path.name in {"execution-manifest.json", "manifest.json", "validation-report.json", "illustration-handoff.json"}:
            try:
                value = json.loads(read_text(path, 1_000_000))
                if path.name == "execution-manifest.json":
                    value = {
                        "file": item["path"],
                        "status": value.get("status"),
                        "scope": value.get("scope", {}).get("execution_stages"),
                        "stages": [
                            {key: record.get(key) for key in ("id", "status", "skill", "invocation", "inputs", "outputs", "error")}
                            for record in value.get("stages", [])
                        ],
                    }
                else:
                    value = {"file": item["path"], "keys": sorted(value) if isinstance(value, dict) else []}
                manifests.append(value)
            except (OSError, json.JSONDecodeError):
                manifests.append({"file": item["path"], "invalid": True})
    return {"provided": True, "path": str(package), "readable": True, "files": files, "manifests": manifests}


def execution_trace(session: Path | None, thread_id: str | None, codex_db: Path | None, scan: Path | None, package: Path | None) -> dict[str, Any]:
    resolution: dict[str, Any] | None = None
    if thread_id:
        resolution = resolve_codex_thread(thread_id, codex_db or Path.home() / ".codex" / "state_5.sqlite")
        if session is None and resolution.get("rollout_path"):
            session = Path(resolution["rollout_path"])
    trace = session_metadata(session) if session else {"readable": False, "error": "no raw session supplied"}
    session_id = thread_id
    if not session_id:
        match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})", str(session or ""), re.I)
        session_id = match.group(1) if match else None
    scan_match = find_scan_session(scan, session_id)
    calls = trace.get("calls", []) if isinstance(trace, dict) else []
    native = [call for call in calls if call.get("native_skill_call")]
    return {
        "schema_version": "1.0",
        "doctor_version": VERSION,
        "generated_at": now_iso(),
        "mode": "execution",
        "session_resolution": resolution,
        "session": {key: trace.get(key) for key in ("path", "readable", "bytes", "line_count", "agent", "first_record_summary")},
        "calls": calls,
        "native_skill_calls": native,
        "errors": trace.get("errors", []),
        "session_analyzer_match": scan_match,
        "package": package_evidence(package),
        "limitations": [
            "仅有 session-analyzer 扫描快照时，只能证明 Session 元数据存在，不能证明 Tool/Skill 调用发生。",
            "不同 Agent 的 transcript schema 可能变化，未识别的事件会保留在统计缺口而不是被猜测。",
        ],
    }


def merge_diagnosis(source: dict[str, Any] | None, execution: dict[str, Any] | None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if source:
        findings.extend(source.get("findings", []))
    if execution:
        native = execution.get("native_skill_calls", [])
        if not execution.get("session", {}).get("readable"):
            findings.append({
                "id": "RUN-001", "severity": "P1", "confidence": "high", "evidence_type": "missing-evidence",
                "title": "没有可读的原始执行 Session", "impact": "无法证明任何子 Skill 真实执行", "evidence": [],
                "recommendation": "提供 Claude JSONL、Codex rollout JSONL 或 Codex thread ID 后重新诊断。",
            })
        if execution.get("session", {}).get("readable") and not native:
            findings.append({
                "id": "RUN-002", "severity": "P1", "confidence": "high", "evidence_type": "missing-evidence",
                "title": "Session 中没有识别到原生 Skill 调用", "impact": "目标工作流可能没有真正触发子 Skill，或当前 transcript 适配器未识别该事件", "evidence": [],
                "recommendation": "检查运行时调度器和 transcript schema；在目标 Skill 中禁止用脚本/文本模拟替代原生 Skill 调用。",
            })
        if execution.get("errors"):
            findings.append({
                "id": "RUN-003", "severity": "P1", "confidence": "medium", "evidence_type": "runtime-proven",
                "title": "Session 包含错误或失败样事件", "impact": "需要确认父级是否在子 Skill 失败后错误继续或补写产物", "evidence": execution["errors"][:12],
                "recommendation": "把错误绑定到阶段状态；失败时停止下游，不允许仅凭路径或模型回复登记 completed。",
            })
        package = execution.get("package", {})
        if package.get("provided") and package.get("readable") and not native and package.get("files"):
            findings.append({
                "id": "RUN-004", "severity": "P1", "confidence": "high", "evidence_type": "artifact-only",
                "title": "存在执行产物但没有原生 Skill 调用证据", "impact": "产物可能由父级或脚本手工生成，不能证明子 Skill 完整执行", "evidence": package.get("files", [])[:12],
                "recommendation": "将原生 Skill 调用纳入运行时 transcript 验证，并让子 Skill 自己交付 handoff；缺证据时保持 blocked。",
            })
    severity_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    findings.sort(key=lambda item: (severity_order.get(item.get("severity", "P3"), 9), item.get("id", "")))
    if any(item.get("severity") == "P0" for item in findings):
        status = "blocked"
    elif any(item.get("severity") == "P1" for item in findings):
        status = "needs-optimization"
    else:
        status = "healthy-with-limitations"
    return {
        "schema_version": "1.0",
        "doctor_version": VERSION,
        "generated_at": now_iso(),
        "status": status,
        "source": source,
        "execution": execution,
        "findings": findings,
        "recommendation": {
            "sequence": [
                "先修复 P0/P1 的原生调用和失败传播问题",
                "再固化输入/输出/handoff 与机器校验",
                "最后用 Claude Code 和 Codex 的真实 Session 做回归",
            ],
            "apply_requires_confirmation": True,
        },
    }


def md_report(report: dict[str, Any]) -> str:
    findings = report.get("findings", [])
    lines = [
        "# Skill Doctor 诊断报告",
        "",
        f"- 状态：`{report.get('status', 'unknown')}`",
        f"- 生成时间：`{report.get('generated_at')}`",
        f"- 问题数：`{len(findings)}`",
        "",
        "## 结论",
        "",
        "本报告区分源码推断和运行时证据。只有 Session/Tool/文件时间线直接支持的结论才标记为 `runtime-proven`；产物存在本身不证明子 Skill 被原生调用。",
        "",
        "## 问题清单",
        "",
    ]
    if not findings:
        lines.append("未发现已记录的问题；仍需结合真实 Session 做 forward-test。")
    for item in findings:
        lines.extend([
            f"### {item.get('severity')} {item.get('id')}：{item.get('title')}",
            "",
            f"- 证据类型：`{item.get('evidence_type')}`",
            f"- 置信度：`{item.get('confidence')}`",
            f"- 影响：{item.get('impact')}",
            f"- 建议：{item.get('recommendation')}",
        ])
        evidence = item.get("evidence") or []
        if evidence:
            lines.append("- 证据：")
            for entry in evidence[:8]:
                if isinstance(entry, dict):
                    location = f"{entry.get('file')}:{entry.get('line')}" if entry.get("file") else entry.get("path") or entry.get("line") or "event"
                    detail = entry.get("excerpt") or entry.get("kind") or entry.get("status") or ""
                    lines.append(f"  - `{location}` {redact(detail, 180)}")
                else:
                    lines.append(f"  - {redact(entry, 180)}")
        lines.append("")
    lines.extend([
        "## 推荐实施顺序",
        "",
        "1. 修复原生 Skill 调度证据和失败阻塞；",
        "2. 固化 handoff、manifest 和脚本校验；",
        "3. 分别用 Claude Code 与 Codex Session 回归；",
        "4. 用户确认后再应用 patch。",
        "",
        "## 局限",
        "",
        "- 静态扫描不能证明历史执行；",
        "- 缺少原始 transcript 时不能判定 Tool/Skill 是否真实调用；",
        "- 未识别的 Agent 事件会标记为证据缺失，不会自动猜测。",
        "",
    ])
    return "\n".join(lines)


def default_out() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("/Users/jiyi/Workspace/exports/skill-doctor") / f"diagnosis-{stamp}"


def write_reports(out: Path, source: dict[str, Any] | None, execution: dict[str, Any] | None, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if source is not None:
        (out / "source-graph.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if execution is not None:
        (out / "execution-trace.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "diagnostic-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "diagnostic-report.md").write_text(md_report(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose composed Skill source and Claude/Codex execution evidence")
    parser.add_argument("mode", choices=("source", "execution", "full"))
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--thread-id")
    parser.add_argument("--codex-db", type=Path)
    parser.add_argument("--scan", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.target.expanduser().resolve()
    if not target.is_dir():
        print(json.dumps({"ok": False, "error": f"Target Skill directory not found: {target}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    out = (args.out or default_out()).expanduser().resolve()
    source = scan_source(target) if args.mode in {"source", "full"} else None
    execution = execution_trace(args.session.expanduser().resolve() if args.session else None, args.thread_id, args.codex_db.expanduser().resolve() if args.codex_db else None, args.scan.expanduser().resolve() if args.scan else None, args.package.expanduser().resolve() if args.package else None) if args.mode in {"execution", "full"} else None
    report = merge_diagnosis(source, execution)
    write_reports(out, source, execution, report)
    print(json.dumps({"ok": True, "out": str(out), "status": report["status"], "finding_count": len(report["findings"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
