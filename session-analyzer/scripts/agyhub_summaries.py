#!/usr/bin/env python3
"""Antigravity 会话索引（agyhub_summaries_proto.pb）的只读解析与按 id 剔除。

新版 Antigravity（migrate_convos_into_projects 完成后）把侧栏会话列表存进这个二进制
protobuf 索引，而不再是 conversations/<uuid>.pb。删 conversations 文件不会让侧栏条目消失，
必须同步从本索引里剔除对应记录。

文件结构（逆向自 wire 格式，无官方 schema）：
  顶层 = 重复的 field 1（每条一个会话，length-delimited）
  每条 field1 内：
    1: 会话 uuid（侧栏条目主键）
    2: { 3: google.protobuf.Timestamp{1:seconds}（更新时间）, ... 其余为摘要正文 }

只依赖顶层"扁平重复字段"这一事实做删除：剔除一整条 field1 记录时，保留记录的原始字节
逐字节复制、无需重算任何嵌套长度——这是最不易损坏的编辑方式。
"""
from __future__ import annotations

import os
from pathlib import Path


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while True:
        x = b[i]
        i += 1
        val |= (x & 0x7F) << shift
        if not (x & 0x80):
            break
        shift += 7
    return val, i


def _iter_fields(b: bytes):
    """逐个产出 (field_no, wire_type, value)。value 对 len 型是 bytes，对 varint 是 int。"""
    i, n = 0, len(b)
    while i < n:
        key, i = _read_varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _read_varint(b, i)
            yield f, 0, v
        elif wt == 2:
            ln, i = _read_varint(b, i)
            yield f, 2, b[i:i + ln]
            i += ln
        elif wt == 5:
            yield f, 5, b[i:i + 4]
            i += 4
        elif wt == 1:
            yield f, 1, b[i:i + 8]
            i += 8
        else:
            return  # 未知 wire type：停止（容错）


def _top_records(data: bytes) -> list[tuple[str | None, bytes]]:
    """切出顶层每条 field1 记录，返回 [(主uuid, 该条原始字节)]。非预期结构则返回空。"""
    recs: list[tuple[str | None, bytes]] = []
    i, n = 0, len(data)
    while i < n:
        start = i
        key, i = _read_varint(data, i)
        f, wt = key >> 3, key & 7
        if f != 1 or wt != 2:
            return []  # 不是预期的"重复 field1"结构，放弃（安全优先）
        ln, i = _read_varint(data, i)
        payload = data[i:i + ln]
        i += ln
        recs.append((_entry_id(payload), data[start:i]))
    return recs


def _entry_id(payload: bytes) -> str | None:
    for f, wt, v in _iter_fields(payload):
        if f == 1 and wt == 2:
            try:
                return v.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def _sub(b: bytes, fn: int):
    """取第一个 field==fn 的值（len 型 bytes 或 varint int）；无则 None。"""
    for f, wt, v in _iter_fields(b):
        if f == fn:
            return v
    return None


def _decode_workspace(raw: str | None) -> str | None:
    """file:///Users/x/Projects/foo%20bar → /Users/x/Projects/foo bar。"""
    if not raw:
        return None
    from urllib.parse import unquote, urlparse
    try:
        if raw.startswith("file://"):
            return unquote(urlparse(raw).path) or None
        return raw
    except ValueError:
        return None


def _entry_meta(payload: bytes) -> dict:
    """按已知精确路径取 标题(1.2.1) / 时间(1.2.3.1) / workspace(1.2.9.1)。"""
    f2 = _sub(payload, 2)
    if not isinstance(f2, (bytes, bytearray)):
        return {"title": None, "mtime": None, "workspace": None}
    # 标题：1.2.1 是字符串时取之；否则视为无标题
    title = None
    t = _sub(f2, 1)
    if isinstance(t, (bytes, bytearray)):
        try:
            s = t.decode("utf-8")
            if s.isprintable() and len(s) <= 120:
                title = s.strip() or None
        except UnicodeDecodeError:
            pass
    # 时间：1.2.3.1 (Timestamp.seconds)
    mtime = None
    ts = _sub(f2, 3)
    if isinstance(ts, (bytes, bytearray)):
        sec = _sub(ts, 1)
        if isinstance(sec, int):
            mtime = sec
    # workspace：1.2.9.1 (file:// URI)
    ws = None
    f9 = _sub(f2, 9)
    if isinstance(f9, (bytes, bytearray)):
        wraw = _sub(f9, 1)
        if isinstance(wraw, (bytes, bytearray)):
            try:
                ws = _decode_workspace(wraw.decode("utf-8"))
            except UnicodeDecodeError:
                ws = None
    return {"title": title, "mtime": mtime, "workspace": ws}


def list_entries(pb_path: Path) -> list[dict]:
    """解析索引，返回每条会话 {id, title, mtime, size, workspace}。
    文件缺失/结构异常返回 []。"""
    try:
        data = pb_path.read_bytes()
    except OSError:
        return []
    out = []
    for uid, rec in _top_records(data):
        if not uid:
            continue
        payload = next((v for f, wt, v in _iter_fields(rec) if f == 1 and wt == 2), b"")
        meta = _entry_meta(payload)
        ws = meta["workspace"]
        title = meta["title"] or (ws.rsplit("/", 1)[-1] if ws else None) or "(无标题会话)"
        out.append({
            "id": uid,
            "title": title,
            "mtime": meta["mtime"],
            "size": len(rec),
            "workspace": ws,
        })
    return out


def remove_entries(data: bytes, drop_ids: set) -> tuple[bytes, list]:
    """从索引字节里剔除 id ∈ drop_ids 的整条记录。返回 (新字节, 被删id列表)。

    保留记录原始字节逐字节复制；若结构不符预期则原样返回、不删（安全优先）。
    """
    recs = _top_records(data)
    if not recs:
        return data, []
    kept = bytearray()
    removed = []
    for uid, rec in recs:
        if uid in drop_ids:
            removed.append(uid)
        else:
            kept += rec
    return bytes(kept), removed


def remove_from_index(pb_path: Path, drop_ids: set) -> list:
    """原子改写索引文件，剔除 drop_ids 对应记录。返回被删 id 列表。

    索引是多会话共享的单文件，不能整文件移废纸篓——只能就地硬改（temp + replace），
    与 jsonl 索引行的处理同理。文件缺失或无匹配则不写盘。
    """
    try:
        data = pb_path.read_bytes()
    except OSError:
        return []
    new_data, removed = remove_entries(data, drop_ids)
    if removed:
        tmp = pb_path.with_suffix(pb_path.suffix + ".tmp")
        tmp.write_bytes(new_data)
        os.replace(tmp, pb_path)
    return removed
