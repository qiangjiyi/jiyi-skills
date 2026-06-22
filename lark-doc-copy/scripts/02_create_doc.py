#!/usr/bin/env python3
"""
第 1 + 3 步：创建新文档

功能：
1. 清理源 XML（去除 block ID、图片标签、空 grid 容器）
2. 用 docs +create 创建新文档（默认放根目录，可用 --target-dir-token 指定目录）
3. 获取新文档的 ID

输入参数：
  --source <URL>          : 源文档 URL（可省略，自动从 state.json 读取）
  --target-dir-token <T>  : 可选，手动指定目标目录 token（默认根目录）

输出：
  - cleaned.xml: 清理后的 XML
  - state.json: 更新 state（new_doc_id, new_doc_url, target_dir_token, target_dir_name）
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    load_state,
    print_progress,
    print_step,
    run_lark_cli_json,
    update_state,
)


def clean_xml(content: str) -> str:
    """清理 XML：去除 block ID、图片标签、画板标签、空 grid 容器"""
    # 去除 block ID
    content = re.sub(r'\s+id="[^"]*"', '', content)
    # 去除 seq-level 属性
    content = re.sub(r'\s+seq-level="[^"]*"', '', content)

    # 防止 ol 合并：当 img 位于两个 ol 之间时（OL + img + OL），
    # 删掉 img 后两个 ol 会相邻，飞书解析器会把它合并成一个 ol。
    # workaround：把位于 OL 之间的 img 替换成空 <p></p> 占位符。
    # 注意：只处理 ol/ul 之间的 img，避免影响段落之间的 img
    content = re.sub(
        r'(</ol>\s*)<img[^>]*?/>(\s*<ol)',
        r'\1<p></p>\2',
        content
    )
    content = re.sub(
        r'(</ul>\s*)<img[^>]*?/>(\s*<ul)',
        r'\1<p></p>\2',
        content
    )
    # 跨 ol/ul 类型也要防（ol 后跟 ul 之类）
    content = re.sub(
        r'(</ol>\s*)<img[^>]*?/>(\s*<ul)',
        r'\1<p></p>\2',
        content
    )
    content = re.sub(
        r'(</ul>\s*)<img[^>]*?/>(\s*<ol)',
        r'\1<p></p>\2',
        content
    )

    # 防止 ol 被拆 + 文字错位：当 img 位于「列表 → 单个非空段落 → 列表」之间时
    # （源模式 </ol><img/><p>text</p><ol>），图片走 two_step 移动会触发飞书
    # block_move_after 的容器陷阱：第二步 mv(img, succ_p) 把 succ_p 错插进后面
    # ol 的第一个 li 之后，导致 ol 被拆成两段、段落卡在列表中间（实测 bug：
    # 「其他赛道一样」跑到「1.基于价值」和「2.遇到瓶颈」之间）。
    # workaround：在 img 处插空 <p></p> 占位符，move_images 改走可靠的占位符
    # 锚点（_find_empty_p_after_ol），彻底绕开 two_step 第二步。
    # 仅匹配「单个非空 p 后紧跟列表」，多 p 或空 p 场景不触发陷阱、无需处理。
    content = re.sub(
        r'(</[ou]l>\s*)<img[^>]*?/>(\s*<p\b[^>]*?>.+?</p>\s*<[ou]l\b)',
        r'\1<p></p>\2',
        content,
        flags=re.DOTALL,
    )

    # 去除 img 标签（图片单独处理）
    content = re.sub(r'<img[^>]*?/>', '', content)
    # 去除 whiteboard 标签（画板单独处理）：画板是 token 对象，docs +create 无法从
    # 跨租户 token 重建，会被静默丢弃（连标题下的占位都不留）。改由 03 的
    # migrate_whiteboards 读源画板 raw 节点、在对应锚点后重建，保留原始布局。
    content = re.sub(r'<whiteboard\b[^>]*?/>', '', content)
    content = re.sub(
        r'<whiteboard\b[^>]*?>.*?</whiteboard>', '', content, flags=re.DOTALL
    )
    # 去除空 grid 容器（删 img 后 grid 里只剩空 column）。支持任意列数，
    # 否则 3 列及以上的并排图 grid 删图后会残留空 grid，且会和 rebuild_grids
    # 重建的 grid 重复。grid 的并排布局由第 7.6 步 rebuild_grids 重新还原。
    content = re.sub(
        r'<grid>\s*(?:<column[^>]*>\s*</column>\s*)+</grid>',
        '', content
    )
    return content


def main():
    parser = argparse.ArgumentParser(description="分析目标目录并创建新文档")
    parser.add_argument("--source", help="源文档 URL（自动从 state 读取）")
    parser.add_argument("--output-dir", default=".", help="输出目录")
    parser.add_argument("--target-dir-token", help="手动指定目标目录 token")
    parser.add_argument("--target-dir-name", help="手动指定目标目录名称")
    args = parser.parse_args()

    state = load_state()
    source_url = args.source or state.get("source_url")
    output_dir = Path(args.output_dir)

    print_step("第 1+3 步：创建新文档")

    # 读取源 XML
    xml_path = Path(state.get("source_xml_path", output_dir / "source.xml"))
    if not xml_path.exists():
        print(f"❌ 源 XML 不存在: {xml_path}")
        print("请先运行 01_fetch_source.py")
        sys.exit(1)

    with open(xml_path, "r", encoding="utf-8") as f:
        xml_content = f.read()

    # 目标目录：默认根目录，可用 --target-dir-token 显式指定
    if args.target_dir_token:
        target_token = args.target_dir_token
        target_name = args.target_dir_name or "(手动指定)"
    else:
        target_token, target_name = "", "根目录"
    print_progress(f"目标目录: {target_name} (token: {target_token or '空'})")

    # 清理 XML
    print_progress("清理 XML...")
    cleaned = clean_xml(xml_content)
    cleaned_path = output_dir / "cleaned.xml"
    with open(cleaned_path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    print_progress(f"清理后大小: {len(cleaned)} 字符 (原 {len(xml_content)})")

    # 创建新文档
    print_progress("调用 lark-cli 创建新文档...")
    # 注意：--content 与 @file 必须是两个独立 argv（写成 "--content@file"
    # 会被 lark-cli 当成一个未知 flag 而报错）。
    create_args = [
        "docs", "+create",
        "--api-version", "v2",
        "--doc-format", "xml",
        "--content", f"@{cleaned_path.name}",
    ]
    if target_token:
        create_args.extend(["--parent-token", target_token])

    result = run_lark_cli_json(create_args, timeout=120)

    if not result or not result.get("ok"):
        print("❌ 创建文档失败")
        if result:
            print(f"  错误: {result.get('error', {}).get('message', '')}")
        sys.exit(1)

    new_doc_id = result.get("data", {}).get("document", {}).get("document_id")
    new_doc_url = result.get("data", {}).get("document", {}).get("url")

    print_progress(f"✅ 新文档创建成功")
    print_progress(f"   ID: {new_doc_id}")
    print_progress(f"   URL: {new_doc_url}")

    # 更新状态
    update_state(
        target_dir_token=target_token,
        target_dir_name=target_name,
        cleaned_xml_path=str(cleaned_path),
        new_doc_id=new_doc_id,
        new_doc_url=new_doc_url,
    )

    print_step("完成", "新文档已创建到目标目录")
    print(f"  放置目录: {target_name or '根目录'}")
    print(f"  新文档链接: {new_doc_url}")


if __name__ == "__main__":
    main()
