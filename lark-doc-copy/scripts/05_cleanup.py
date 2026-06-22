#!/usr/bin/env python3
"""
第 13 步：清理临时文件

清理：
- 下载的本地图片（_img_download/）
- 中间 XML 文件（source.xml, cleaned.xml）
- 临时 token 文件（img_tokens.txt）
- 状态文件（state.json）

输出最终报告。
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import load_state, print_step


def main():
    state = load_state()
    output_dir = Path(state.get("output_dir", "."))

    print_step("第 13 步：清理临时文件")

    cleaned_files = []

    # 清理下载的图片
    img_dir = output_dir / "_img_download"
    if img_dir.exists():
        for f in img_dir.iterdir():
            f.unlink()
            cleaned_files.append(str(f))
        img_dir.rmdir()
        print(f"  ✅ 已清理 {len(cleaned_files)} 个图片文件")

    # 清理 cite 递归子调用的临时工作目录
    for d in output_dir.glob("_cite_*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            print(f"  ✅ 已清理递归工作目录 {d.name}")

    # 清理中间文件
    for name in ["source.xml", "cleaned.xml", "img_tokens.txt", "state.json"]:
        path = output_dir / name
        if path.exists():
            path.unlink()
            print(f"  ✅ 已清理 {name}")

    # 共享 cite registry / 副本台账 仅由顶层（depth 0）删除，子调用不动它
    if os.environ.get("LARK_DOC_COPY_DEPTH", "0") == "0":
        reg = os.environ.get("LARK_DOC_COPY_REGISTRY")
        reg_path = Path(reg) if reg else (output_dir / "cite_registry.json")
        for p in (reg_path, reg_path.with_name("cite_copies.json")):
            if p.exists():
                p.unlink()
                print(f"  ✅ 已清理 {p.name}")

    print_step("清理完成", "所有临时文件已删除")


if __name__ == "__main__":
    main()
