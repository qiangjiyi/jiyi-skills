#!/usr/bin/env python3
"""云端型 Agent（豆包、Grok Bot）：会话存在云端，本地只能清会话产生的缓存。

- 豆包：本地消息库 sdk_storage/<uid>/im.db、messages.db 是 SQLCipher 加密缓存，列不出会话、
  也不碰（删了会影响登录态/设置）；只清 ~/.doubao/chats/<日期>/<名称>/ 的 Agent 工作目录、
  运行日志、下载临时目录。
- Grok Bot：会话在 xAI 云端；只清附件暂存与附件/链接预览缓存。
云端会话本身请在 App 内删除。删除前必须退出对应 App；「清空」类条目只删目录内容、保留目录本身。
"""
from __future__ import annotations

from pathlib import Path

import agent_common as c
import agent_registry

APP_SUPPORT = agent_registry.APP_SUPPORT


def _targets(key: str, home: Path = c.HOME) -> list[dict]:
    """id -> 路径的唯一来源（扫描与删除共用；删除时重算，客户端无法指定路径）。"""
    out = []
    support = home / "Library" / "Application Support"
    if key == "doubao":
        chats = home / ".doubao" / "chats"
        if chats.is_dir():
            for d in sorted(chats.glob("*/*")):
                if d.is_dir():
                    out.append({"id": f"chats:{d.parent.name}/{d.name}", "path": d, "group": "workdir",
                                "title": f"{d.parent.name} / {d.name}", "empty_only": False})
        sdk = support / "Doubao" / "sdk_storage"
        out.append({"id": "cache:log", "path": sdk / "log", "group": "cache",
                    "title": "运行日志（sdk_storage/log）", "empty_only": True})
        for tmp in sorted(sdk.glob("*/drive_download_tmp")) if sdk.is_dir() else []:
            out.append({"id": f"cache:dl:{tmp.parent.name[:12]}", "path": tmp, "group": "cache",
                        "title": "下载临时目录（drive_download_tmp）", "empty_only": True})
    elif key == "grok":
        app = support / "Grok Bot"
        for cid, path, title in (
            ("cache:staging", home / ".grokbot" / "attachment-staging", "附件上传暂存（~/.grokbot/attachment-staging）"),
            ("cache:attach", app / "attachment-image-cache", "附件图片缓存"),
            ("cache:preview", app / "link-preview-cache", "链接预览缓存"),
        ):
            out.append({"id": cid, "path": path, "group": "cache", "title": title, "empty_only": True})
    return [t for t in out if t["path"].exists()]


GROUP_LABEL = {"workdir": "Agent 工作目录（会话生成）", "cache": "本地缓存"}
CLOUD_HINT = {
    "doubao": "会话保存在云端，本地消息库为加密缓存无法逐条列出；云端会话请在豆包 App 内删除。",
    "grok": "会话保存在 xAI 云端；云端会话请在 Grok Bot App 内删除。",
}


def scan(key: str, home: Path = c.HOME) -> dict:
    spec = agent_registry.BY_KEY[key]
    root = spec.detect[0]
    agent = c.new_agent(key, root)
    if not agent["installed"]:
        return c.finalize(agent)
    groups: dict[str, list] = {}
    for t in _targets(key, home):
        size = c.dir_size(t["path"])
        if t["empty_only"] and size == 0:
            continue
        groups.setdefault(t["group"], []).append(
            c.session(t["id"], t["title"], c.mtime_of(t["path"]), size, str(t["path"]),
                      cloud_cache=True, empty_only=t["empty_only"]))
    for g, sess in groups.items():
        agent["projects"].append(c.project(g, GROUP_LABEL[g], None, False, sess))
    agent["note"] = CLOUD_HINT[key] + f" 清理本地缓存前需先退出 {spec.name}。"
    agent["cloud_hint"] = CLOUD_HINT[key]
    return c.finalize(agent)


def delete(key: str, ids: list, mode: str, home: Path = c.HOME, gate: bool = True) -> dict:
    if gate:
        agent_registry.require_closed(key)
    by_id = {t["id"]: t for t in _targets(key, home)}
    removed, errors = [], []
    for i in ids:
        t = by_id.get(i)
        if not t:
            continue
        if t["empty_only"]:
            for child in list(t["path"].iterdir()):
                c.safe_remove(child, mode, removed, errors)
        else:
            c.safe_remove(t["path"], mode, removed, errors)
            parent = t["path"].parent
            if parent.is_dir() and not any(x for x in parent.iterdir() if x.name not in c.JUNK_NAMES):
                c.safe_remove(parent, mode, removed, errors)
    return {"removed": removed, "errors": errors}
