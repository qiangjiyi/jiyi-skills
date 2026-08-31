#!/usr/bin/env python3
"""Codex Electron UI sidebar state (~/.codex/.codex-global-state.json) — parse & prune.

2026-08 实测发现：Codex 桌面端侧栏会话列表不读 state_5.sqlite，而是读它自己的
Electron UI 状态文件 `.codex-global-state.json`。只删 DB 行 + rollout 会让侧栏留下
「幽灵条目」——旧标题仍在，点开报「恢复对话失败: no rollout found for thread id …」。

本模块是该文件唯一合法的读写入口，scan.py（只读解析）与 agent_delete.py（删除收尾）
共用。安全模型与 cleanup_claude_config.py 对齐：

- **保守剪除**：只碰确证的 thread-id 命名空间 key（projectless-thread-ids /
  pinned-thread-ids / thread-titles.titles / thread-titles.order /
  thread-workspace-root-hints）。文件里存在大量非 thread 的 uuid（约 197 个，分布在
  thread-project-assignments、thread-descriptions-v1、prompt-history 等几十个 key），
  严禁「凡不在 threads 表的 uuid 一律删」式宽匹配——白名单之外的 key 一个字节不碰，
  其中 electron-saved-workspace-roots 存的是目录路径、连 uuid 都不是。
- **运行检测 fail closed**：改文件前用 pgrep 校验 ChatGPT App（现持有 ~/.codex/）与
  遗留独立 Codex App 已退出；pgrep 本身不可用/报错时保守拒绝。注意：`codex` CLI 进程
  （Rust 引擎，comm 名恰好也是 "codex"）不写此 Electron 文件，不参与门禁——否则日常
  CLI 会话会让清理永远不可用。
- **备份 + 原子写**：改前整文件备份到固定单份（0600、每次覆盖，对齐
  `~/.claude.json.session-analyzer.bak`）；同目录 mkstemp 临时文件 + fsync +
  os.replace + 目录 fsync；写前重读比对，文件被并发修改（应用未退净/其他工具）即中止。
- **逐字节保留**：Electron 以 JSON.stringify 落盘（紧凑、无空格、非 ASCII 不转义），
  本机实测 json.dumps(ensure_ascii=False, separators=(",",":")) 与原文件 round-trip
  字节恒等。写之前强制做恒等校验，原文件不是该规范格式就拒绝——校验通过则「只删该删
  的条目、其余逐字节保留」有字节级保证。
- **幂等 / 防御**：无命中时 no-op（不写、不备份）；key 缺失或结构变体逐 key 跳过，
  新版本 Codex 改结构只会「少清理」而不会崩溃或误删。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

STATE_NAME = ".codex-global-state.json"
BACKUP_SUFFIX = ".session-analyzer.bak"

# 幽灵候选的形态守卫：只有 uuid 形态的字符串才可能成为 thread id。命名空间里若出现
# 非 uuid 哨兵值（新版本 Codex 的未知标记），不当 thread id 处理、也不参与剪除判定。
_UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def state_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".codex" / STATE_NAME


def backup_path(home: Path | None = None) -> Path:
    return state_path(home).with_name(STATE_NAME + BACKUP_SUFFIX)


class CleanupRejected(Exception):
    """门禁拒绝。category 是稳定字符串，调用方据此向用户转述，不携带文件内容。"""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


# ───────────────────────────── 解析（只读） ─────────────────────────────

def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key: {k}")
        out[k] = v
    return out


def load_state_raw(path: Path) -> tuple[bytes, os.stat_result]:
    """读原文并返回写入前要复核的 stat 快照。文件不存在抛 FileNotFoundError。"""
    raw = path.read_bytes()
    return raw, path.stat()


def parse_state(raw: bytes) -> dict:
    """解析 UI 状态。重复 key / 顶层非 dict / JSON 损坏 → CleanupRejected（防误写）。"""
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise CleanupRejected("ui_state_unparsable") from e
    if not isinstance(data, dict):
        raise CleanupRejected("ui_state_unparsable")
    return data


def is_canonical(raw: bytes) -> bool:
    """Electron JSON.stringify 的紧凑格式恒等校验。不满足 → 一律拒绝写，
    因为只有规范格式能保证「重序列化后其余内容逐字节不变」。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8") == raw


def canonical_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _str_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, str) and x]


def _thread_id_keys(key: str, value: object) -> list[str]:
    """dict 且 key 是 thread id 的命名空间（如 thread-workspace-root-hints）。"""
    if not isinstance(value, dict):
        return []
    return [k for k in value if isinstance(k, str) and k]


def namespace_thread_ids(data: dict) -> dict[str, set[str]]:
    """白名单命名空间 → thread id → 出现过的 key 名。逐 key 防御：
    缺失 / 类型变体直接当空处理，绝不让新版本 Codex 的结构变化炸掉扫描或误删。"""
    found: dict[str, set[str]] = {}

    def add(tid: str, key: str) -> None:
        found.setdefault(tid, set()).add(key)

    add_list = lambda name: [add(x, name) for x in _str_ids(data.get(name))]
    add_list("projectless-thread-ids")
    add_list("pinned-thread-ids")

    hints = data.get("thread-workspace-root-hints")
    if isinstance(hints, dict):
        for tid in _thread_id_keys("thread-workspace-root-hints", hints):
            add(tid, "thread-workspace-root-hints")

    titles = data.get("thread-titles")
    if isinstance(titles, dict):
        order = _str_ids(titles.get("order"))
        for tid in order:
            add(tid, "thread-titles.order")
        tmap = titles.get("titles")
        if isinstance(tmap, dict):
            for tid in _thread_id_keys("thread-titles.titles", tmap):
                add(tid, "thread-titles.titles")
    return found


def title_of(data: dict, tid: str) -> str | None:
    titles = data.get("thread-titles")
    if isinstance(titles, dict) and isinstance(titles.get("titles"), dict):
        t = titles["titles"].get(tid)
        if isinstance(t, str):
            return t
    return None


def ghost_entries(data: dict, live_thread_ids: set[str]) -> list[dict]:
    """幽灵 = uuid 形态 ∧ 出现在白名单命名空间 ∧ 不在 threads 表。纯 UI 视角：
    rollout 是否存在不影响判定（threads 行才是会话的权威定义）。"""
    return [
        {"id": tid, "keys": sorted(keys), "title": title_of(data, tid)}
        for tid, keys in sorted(namespace_thread_ids(data).items())
        if _UUID_SHAPE.match(tid) and tid not in live_thread_ids
    ]


def residue_ids(data: dict) -> set[str]:
    """出现在任一白名单命名空间的全部 thread id（无论是否存活）。
    scan.py 用它给存活会话打 extra.codex_ui_residue 标记。"""
    return set(namespace_thread_ids(data))


# ───────────────────────────── 剪除（纯内存） ─────────────────────────────

def prune(data: dict, drop_ids: set[str]) -> dict[str, int]:
    """从白名单命名空间剪除 drop_ids。返回每个 key 实际剪掉的条数；
    结构变体的 key 跳过（计 0）。白名单之外的 key 不被本函数触碰。"""
    removed: dict[str, int] = {}

    def count(key: str, n: int) -> None:
        if n:
            removed[key] = removed.get(key, 0) + n

    for name in ("projectless-thread-ids", "pinned-thread-ids"):
        value = data.get(name)
        if isinstance(value, list):
            kept = [x for x in value if not (isinstance(x, str) and x in drop_ids)]
            count(name, len(value) - len(kept))
            data[name] = kept

    hints = data.get("thread-workspace-root-hints")
    if isinstance(hints, dict):
        drop_here = [k for k in hints if isinstance(k, str) and k in drop_ids]
        for k in drop_here:
            hints.pop(k, None)
        count("thread-workspace-root-hints", len(drop_here))

    titles = data.get("thread-titles")
    if isinstance(titles, dict):
        order = titles.get("order")
        if isinstance(order, list):
            kept = [x for x in order if not (isinstance(x, str) and x in drop_ids)]
            count("thread-titles.order", len(order) - len(kept))
            titles["order"] = kept
        tmap = titles.get("titles")
        if isinstance(tmap, dict):
            drop_here = [k for k in tmap if isinstance(k, str) and k in drop_ids]
            for k in drop_here:
                tmap.pop(k, None)
            count("thread-titles.titles", len(drop_here))
    return removed


# ───────────────────────────── 运行检测（fail closed） ─────────────────────────────

def app_running() -> bool | None:
    """ChatGPT App（2026-07 起持有 ~/.codex/）或遗留独立 Codex App 是否在运行。
    True=在运行必须拒绝；False=已退出；None=检测不到进程列表 → 调用方保守拒绝。
    注意 `codex` CLI 引擎进程不写此 Electron 文件，刻意不参与门禁。"""
    if not sys.platform.startswith("darwin"):
        return None  # 本 skill 的删除面只支持/只在 macOS 实测，其它平台保守拒绝
    checks = (
        ["pgrep", "-x", "ChatGPT"],                                  # 现：ChatGPT App 主进程
        ["pgrep", "-f", r"Codex\.app/Contents/MacOS"],               # 遗留：独立 Codex App
    )
    for cmd in checks:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            return True
        if r.returncode > 1:  # pgrep 语法错误/致命错误 = 检测不到进程列表
            return None
    return False


# ───────────────────────────── 带门禁的文件写 ─────────────────────────────

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


def _write_backup(backup: Path, raw: bytes) -> None:
    """固定单份 0600 备份（每次覆盖最近一次）。mkstemp 天然 0600，写完即 replace。"""
    fd, name = tempfile.mkstemp(prefix=f".{backup.name}.", dir=backup.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, backup)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def prune_ui_state_file(
    path: Path,
    drop_ids: set[str],
    *,
    require_app_quit: bool = True,
    backup: bool = True,
) -> dict:
    """带全门禁的剪除写路径。drop_ids 必须来自「确证删除的会话 id」或「ghost_entries()
    的实时计算结果」，调用方不得把非白名单来源的 uuid 混进来。

    返回 {"pruned": {key: n}, "removed_total": n, "backup_created": bool}。
    全部拒绝路径抛 CleanupRejected，category：
      app_running          — ChatGPT/Codex App 仍在运行（或无法确认已退出）
      ui_state_missing     — 状态文件不存在（视为无需清理的成功 no-op 由调用方处理）
      ui_state_unparsable  — JSON 损坏 / 顶层非 dict / 重复 key
      ui_state_not_canonical — 非 Electron 规范紧凑格式，无法保证逐字节保留
      concurrent_change    — 写前重读发现 mtime 或内容与读取时不一致
      backup_error / write_error — 备份或原子替换失败
    """
    if not path.exists():
        raise CleanupRejected("ui_state_missing")
    if require_app_quit:
        running = app_running()
        if running is not False:  # True=在运行；None=检测不到 → 一律拒绝
            raise CleanupRejected("app_running")

    raw, st = load_state_raw(path)
    data = parse_state(raw)
    if not is_canonical(raw):
        raise CleanupRejected("ui_state_not_canonical")

    pruned = prune(data, drop_ids)
    removed_total = sum(pruned.values())
    if removed_total == 0:
        # 幂等：无可剪除条目 = no-op，不写文件、不产生备份
        return {"pruned": {}, "removed_total": 0, "backup_created": False}

    replacement = canonical_bytes(data)

    # 写前重读（在动备份之前——并发冲突时不覆盖既有备份，对齐 cleanup_claude_config
    # 的语义）。mtime 或内容任一变化（应用没退净 / 并发写）→ 中止，绝不覆盖。
    try:
        st2 = path.stat()
        raw2 = path.read_bytes()
    except OSError:
        raise CleanupRejected("concurrent_change") from None
    if raw2 != raw or (st2.st_mtime_ns, st2.st_size) != (st.st_mtime_ns, st.st_size):
        raise CleanupRejected("concurrent_change")

    if backup:
        try:
            _write_backup(path.with_name(path.name + BACKUP_SUFFIX), raw)
        except OSError:
            raise CleanupRejected("backup_error") from None

    fd, name = tempfile.mkstemp(prefix=f".{path.name}.session-analyzer.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(replacement)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, st.st_mode & 0o777)  # 保持原文件权限
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise CleanupRejected("write_error") from None
    return {"pruned": pruned, "removed_total": removed_total, "backup_created": backup}


def live_thread_ids(db_path: Path) -> set[str]:
    """threads 表现存 id 全集（ghost 判定的权威基准）。删除阶段 App 已退出，用普通
    mode=ro 即可拿到含最新 WAL 数据的一致视图；只读打不开 = 有进程仍持有写锁 →
    调用方按 fail closed 处理（scan 阶段无人持锁问题时沿用 immutable=1 亦可）。"""
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT id FROM threads")}
    finally:
        con.close()
