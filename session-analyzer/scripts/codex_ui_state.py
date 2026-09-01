#!/usr/bin/env python3
"""Codex Electron UI sidebar state (~/.codex/.codex-global-state.json) — parse & prune.

侧栏数据源实测演进（本模块的命名空间清单必须跟着 App 版本走）：
- 2026-08 布局：thread 条目在顶层 projectless-thread-ids / pinned-thread-ids /
  thread-titles.* / thread-workspace-root-hints。
- 2026-09 布局（ChatGPT 内嵌 Codex，App 150+）：上述旧命名空间仍在但普遍为空，
  thread 条目迁入顶层新 dict（thread-writable-roots /
  thread-projectless-output-directories / thread-project-assignments）与
  electron-persisted-atom-state 子结构（thread-descriptions-v1 /
  heartbeat-thread-permissions-by-id / thread-reference-capability:<tid> 前缀 key）。
  只按旧白名单剪除会「扫出 0 幽灵、no-op」，已删会话的引用原样残留。
- 同期起侧栏列表本身大量来自账号云端同步（本地无 threads 行、无 rollout 的会话
  也会显示，点开报「no rollout found」）。本地剪除只能清掉本地缓存残渣，
  云端条目必须在 App 侧栏里手动删除一次（原生删除会同步云端）。

本模块是该文件唯一合法的读写入口，scan.py（只读解析）与 agent_delete.py（删除收尾）
共用。安全模型与 cleanup_claude_config.py 对齐：

- **保守剪除**：只碰确证的 thread-id 命名空间（两代布局并收，见下方常量表）。
  thread-project-assignments 的 value 含 projectId 等 uuid，只按 key 精确匹配剪条目、
  value 一律不解析；确证排除的结构（prompt-history 用户提示词缓存、
  chatgpt-sidebar-state-v1 的 host uuid、client-thread-bindings-v1 的 client uuid、
  名字不含 thread 的 atom-state 子 key）一个字节不碰——严禁「凡不在 threads 表的
  uuid 一律删」式宽匹配。
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

# ── thread-id 命名空间清单（两代布局并收，缺失/类型变体逐 key 跳过）──
_LIST_NAMESPACES = ("projectless-thread-ids", "pinned-thread-ids")  # list 元素 = tid
_DICT_NAMESPACES = (  # 顶层 dict，key = tid；thread-project-assignments 的 value 含
    "thread-workspace-root-hints",          # projectId 等 uuid——只按 key 精确剪条目，
    "thread-writable-roots",                # value 一律不解析不触碰
    "thread-projectless-output-directories",
    "thread-project-assignments",
)
_ATOM_STATE = "electron-persisted-atom-state"
_ATOM_DICT_SUBKEYS = ("thread-descriptions-v1", "heartbeat-thread-permissions-by-id")  # 子 dict key = tid
_ATOM_TID_PREFIX = "thread-reference-capability:"  # 子 key 形如 prefix<tid>，须全 id 匹配


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


def _dict_str_keys(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [k for k in value if isinstance(k, str) and k]


def _atom_state(data: dict) -> dict:
    eps = data.get(_ATOM_STATE)
    return eps if isinstance(eps, dict) else {}


def namespace_thread_ids(data: dict) -> dict[str, set[str]]:
    """白名单命名空间 → thread id → 出现过的 key 名。逐 key 防御：
    缺失 / 类型变体直接当空处理，绝不让新版本 Codex 的结构变化炸掉扫描或误删。"""
    found: dict[str, set[str]] = {}

    def add(tid: str, key: str) -> None:
        found.setdefault(tid, set()).add(key)

    for name in _LIST_NAMESPACES:
        for x in _str_ids(data.get(name)):
            add(x, name)

    for name in _DICT_NAMESPACES:
        for tid in _dict_str_keys(data.get(name)):
            add(tid, name)

    titles = data.get("thread-titles")
    if isinstance(titles, dict):
        for tid in _str_ids(titles.get("order")):
            add(tid, "thread-titles.order")
        for tid in _dict_str_keys(titles.get("titles")):
            add(tid, "thread-titles.titles")

    eps = _atom_state(data)
    for name in _ATOM_DICT_SUBKEYS:
        for tid in _dict_str_keys(eps.get(name)):
            add(tid, f"{_ATOM_STATE}.{name}")
    prefix_len = len(_ATOM_TID_PREFIX)
    for k in eps:
        if isinstance(k, str) and k.startswith(_ATOM_TID_PREFIX) and len(k) > prefix_len:
            add(k[prefix_len:], f"{_ATOM_STATE}.thread-reference-capability")
    return found


def title_of(data: dict, tid: str) -> str | None:
    titles = data.get("thread-titles")
    if isinstance(titles, dict) and isinstance(titles.get("titles"), dict):
        t = titles["titles"].get(tid)
        if isinstance(t, str):
            return t
    # 新布局：标题缓存迁到 atom-state 的 thread-descriptions-v1（App 自动生成的描述）
    sub = _atom_state(data).get("thread-descriptions-v1")
    if isinstance(sub, dict):
        d = sub.get(tid)
        if isinstance(d, str):
            return d
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
    注意：存活会话出现在这些命名空间是正常状态（权限/描述等元数据），不构成任何
    「残留」信号——本函数只供诊断用途，扫描与报告不再据此打标。"""
    return set(namespace_thread_ids(data))


# ───────────────────────────── 剪除（纯内存） ─────────────────────────────

def prune(data: dict, drop_ids: set[str]) -> dict[str, int]:
    """从白名单命名空间（两代布局并收）剪除 drop_ids。返回每个 key 实际剪掉的条数；
    结构变体的 key 跳过（计 0）。白名单之外的 key 不被本函数触碰。"""
    removed: dict[str, int] = {}

    def count(key: str, n: int) -> None:
        if n:
            removed[key] = removed.get(key, 0) + n

    for name in _LIST_NAMESPACES:
        value = data.get(name)
        if isinstance(value, list):
            kept = [x for x in value if not (isinstance(x, str) and x in drop_ids)]
            count(name, len(value) - len(kept))
            data[name] = kept

    for name in _DICT_NAMESPACES:
        value = data.get(name)
        if isinstance(value, dict):
            drop_here = [k for k in value if isinstance(k, str) and k in drop_ids]
            for k in drop_here:
                value.pop(k, None)
            count(name, len(drop_here))

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

    eps = _atom_state(data)
    for name in _ATOM_DICT_SUBKEYS:
        sub = eps.get(name)
        if isinstance(sub, dict):
            drop_here = [k for k in sub if isinstance(k, str) and k in drop_ids]
            for k in drop_here:
                sub.pop(k, None)
            count(f"{_ATOM_STATE}.{name}", len(drop_here))
    prefix_len = len(_ATOM_TID_PREFIX)
    pref_drop = [k for k in list(eps)
                 if isinstance(k, str) and k.startswith(_ATOM_TID_PREFIX)
                 and k[prefix_len:] in drop_ids]
    for k in pref_drop:
        eps.pop(k, None)
    count(f"{_ATOM_STATE}.thread-reference-capability", len(pref_drop))
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


def live_ghost_entries(home: Path | None = None) -> list[dict] | None:
    """实时重算当前幽灵条目（只读，毫秒级）。报告入口每次渲染前调用，保证页面展示的
    幽灵数量 = 当下真实存在的数量（流程在起报告前已清扫过时，快照计数会虚高误导用户）。

    返回 [] 表示「当前确实干净」；返回 None 表示无法判定（状态文件损坏 / DB 被锁 /
    路径缺失），调用方应保留快照值——宁可显示旧数也不谎报 0。"""
    import sqlite3

    h = home or Path.home()
    spath = state_path(h)
    db = h / ".codex" / "state_5.sqlite"
    try:
        if not spath.exists() or not db.exists():
            return []
        live = live_thread_ids(db)
        raw, _ = load_state_raw(spath)
        data = parse_state(raw)
        return ghost_entries(data, live)
    except (CleanupRejected, OSError, sqlite3.Error):
        return None


def refresh_codex_ghosts(snapshot: dict, home: Path | None = None) -> dict:
    """返回把快照里 codex agent 的幽灵计数/明细替换为实时重算结果后的浅层副本。
    不改动传入快照（server 的删除闸门依赖快照不可变）；无法判定时原样返回副本。"""
    out = dict(snapshot)
    agents = list(snapshot.get("agents", []))
    ghosts = live_ghost_entries(home)
    if ghosts is not None:
        agents = [dict(a) if a.get("key") == "codex" else a for a in agents]
        for a in agents:
            if a.get("key") == "codex":
                a["ghost_ui_entry_count"] = len(ghosts)
                a["ghost_ui_entries"] = ghosts
    out["agents"] = agents
    return out
