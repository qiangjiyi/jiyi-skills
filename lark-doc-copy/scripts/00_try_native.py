#!/usr/bin/env python3
"""
第 0.5 步：尝试原生复制主文档（drive files copy）

优先用飞书原生 `drive files copy` 复制主文档——这是保真最高、最快的方式：
编号、样式、图片由飞书端到端复制，**不经过「扒取重建」**，天然没有 seq/grid/
图片定位等后处理 bug（实测 2026-06-19：扒取重建会把跨独立 ol 的隐式续号算错，
而原生副本结构与源逐字节一致、编号天然正确）。

策略与 cite 引用文档处理（process_cites）保持一致：
  有权限能复制 → 原生复制；失败（跨租户禁复制 / 无权限 / 接口报错）→ 退回扒取重建。

成功后写 state.native_copy=True + new_doc_id/new_doc_url，run_all.sh 据此跳过
01/02/03 扒取流程，只跑 cite 重指向 + 核验 + 清理。失败写 native_copy=False，
run_all.sh 退回 01→02→03 扒取重建。

注意：本步骤不做 probe_permission 前置门控——实测原生 copy 能否成功与 meta 探测
不完全一致（meta 可能因个人版/跨租户返回空，copy 仍可成功），直接「试 copy、
失败兜底」最稳。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import update_state, print_step, print_progress, run_lark_cli_json
from cite_lib import try_native_copy, get_root_folder_token


def inspect_doc(url: str):
    """drive +inspect 一次拿到 (token, type, title)。wiki URL 会被解包成 docx。"""
    result = run_lark_cli_json(
        ["drive", "+inspect", "--as", "user", "--url", url], timeout=60
    )
    if not result:
        return None
    data = result.get("data", result)
    tok = data.get("token") or data.get("obj_token")
    typ = data.get("type") or data.get("obj_type") or "docx"
    title = data.get("title") or "复制文档"
    if not tok:
        return None
    return tok, typ, title


def main():
    parser = argparse.ArgumentParser(description="尝试原生复制主文档")
    parser.add_argument("--source", required=True, help="源文档 URL")
    parser.add_argument("--target-dir-token", help="目标目录 token（默认根目录）")
    parser.add_argument("--target-dir-name", help="目标目录名称")
    args = parser.parse_args()

    print_step("第 0.5 步：尝试原生复制（drive files copy）")

    # 始终先记下 source_url + 默认 native_copy=False，供后续流程（无论哪条路径）使用
    update_state(source_url=args.source, native_copy=False)

    # 1) 解析 / 解包 token + 类型 + 标题
    info = inspect_doc(args.source)
    if not info:
        print_progress("⚠ 无法解析源文档（可能无权限），退回扒取重建")
        return
    token, dtype, title = info
    print_progress(f"源文档: {title}（type={dtype}）")

    # 原生复制只支持 docx / doc（旧版文档）；其它类型直接退回扒取
    if dtype not in ("docx", "doc"):
        print_progress(f"⚠ 源文档类型为 {dtype}，不走原生复制，退回扒取重建")
        return

    # 2) 目标目录：显式指定优先，否则根目录
    folder_token = args.target_dir_token or ""
    target_name = args.target_dir_name or "(手动指定)"
    if not folder_token:
        root = get_root_folder_token()
        if not root:
            print_progress("⚠ 无法获取根目录 token，退回扒取重建")
            return
        folder_token, target_name = root, "根目录"

    # 3) 尝试原生复制
    print_progress(f"调用 drive files copy → 目录: {target_name}")
    copied = try_native_copy(token, dtype, folder_token, title)
    if not copied:
        print_progress("⚠ 原生复制失败（跨租户禁复制 / 接口报错），退回扒取重建")
        return

    update_state(
        native_copy=True,
        new_doc_id=copied["token"],
        new_doc_url=copied.get("url"),
        target_dir_token=folder_token,
        target_dir_name=target_name,
        source_title=title,
    )

    print_progress("✅ 原生复制成功（保真最高，跳过扒取重建）")
    print_progress(f"   ID: {copied['token']}")
    print_progress(f"   URL: {copied.get('url')}")
    print_step("完成", "原生副本已创建，后续仅做 cite 重指向 + 核验")
    print(f"  放置目录: {target_name}")
    print(f"  新文档链接: {copied.get('url')}")


if __name__ == "__main__":
    main()
