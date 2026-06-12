#!/usr/bin/env python3
"""开场兜底清理：在只读扫描之前，清掉三个 Agent 历史遗留的空目录，以及 Claude
session-env / file-history / tasks 里「对应会话已不存在」的卫星孤儿。

为什么需要：删会话只删会话本体，逐会话删不收空了的 projects/<dir>、Codex 删 rollout
留空日期目录、旧工具删除也遗留过空壳/孤儿——这些 scan.py（只读）看不到也不展示，是纯
残渣。新产生的残渣已由 agent_delete 的删除链路就地收掉，本步骤主要补历史欠账。

清理默认走废纸篓（可逆），且只在各 Agent 自己的数据子树内动手。scan.py 仍严格只读：
清理是这个独立步骤干的，不在扫描里。
"""
from __future__ import annotations

import argparse
import sys

import agent_delete as ad


def main() -> int:
    ap = argparse.ArgumentParser(description="清理空目录 + Claude 卫星孤儿（默认移废纸篓）")
    ap.add_argument("--hard", action="store_true", help="直接删除而非移废纸篓")
    args = ap.parse_args()
    mode = "rm" if args.hard else "trash"

    removed: list = []
    all_roots = [r for rs in ad.prune_roots().values() for r in rs]
    keep = ad._claude_live_sids() | {"memory"}  # 活跃会话卫星目录 + 持久记忆，空也不清
    ad.prune_empty_dirs(all_roots, mode, removed, keep)
    orphans = ad.prune_claude_satellites(mode, removed)
    stale_state = ad.prune_claude_session_state(mode, removed)

    where = "已删除" if args.hard else "已移入废纸篓"
    print(f"[precleanup] {where}：残渣 {len(removed)} 项"
          f"（其中 Claude 卫星孤儿 {len(orphans)} 个、陈旧进程状态文件 {len(stale_state)} 个）。",
          file=sys.stderr)
    for p in removed:
        print(f"  - {p}", file=sys.stderr)
    print("✓ DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
