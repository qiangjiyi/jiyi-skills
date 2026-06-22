#!/usr/bin/env python3
"""
第 2 步：读取源文档并下载所有图片

功能：
1. 用 lark-cli docs +fetch 读取源文档完整 XML
2. 提取所有图片 token
3. 用 docs +media-preview 下载图片到本地（_img_download/）

输入参数：
  --source <URL> : 源文档 URL

输出：
  - source.xml: 源文档的 XML
  - img_tokens.txt: 图片 token 列表
  - _img_download/<token>.png: 下载的图片
  - state.json: 更新 state（source_url, source_xml_path, img_tokens, img_dir）
"""

import argparse
import sys
from pathlib import Path

# 添加同目录的 lib.py
sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    extract_image_tokens,
    download_image,
    fetch_doc_xml,
    print_progress,
    print_step,
    update_state,
)


def main():
    parser = argparse.ArgumentParser(description="读取源文档并下载图片")
    parser.add_argument("--source", required=True, help="源文档 URL")
    parser.add_argument("--output-dir", default=".", help="输出目录")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print_step("第 2 步：读取源文档与下载图片", f"源文档: {args.source}")

    # 读取源文档
    print_progress("读取源文档 XML...")
    xml_content = fetch_doc_xml(args.source, detail="full")
    if not xml_content:
        print("❌ 无法读取源文档")
        print("  可能原因：URL 无效、无访问权限、网络问题")
        sys.exit(1)

    # 保存 XML
    xml_path = output_dir / "source.xml"
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print_progress(f"XML 已保存到 {xml_path} ({len(xml_content)} 字符)")

    # 提取图片 token
    img_tokens = extract_image_tokens(xml_content)
    print_progress(f"找到 {len(img_tokens)} 张图片")

    if img_tokens:
        # 保存 token 列表
        tokens_path = output_dir / "img_tokens.txt"
        with open(tokens_path, "w") as f:
            for t in img_tokens:
                f.write(t + "\n")
        print_progress(f"Token 列表已保存到 {tokens_path}")

        # 下载图片
        img_dir = output_dir / "_img_download"
        print_progress(f"开始下载图片到 {img_dir}...")
        success = 0
        failed = []
        for token in img_tokens:
            result = download_image(token, img_dir)
            if result:
                success += 1
            else:
                failed.append(token)

        print_progress(f"下载成功: {success}/{len(img_tokens)}")
        if failed:
            print_progress(f"下载失败: {failed[:5]}...")
    else:
        img_dir = output_dir / "_img_download"
        img_dir.mkdir(exist_ok=True)

    # 更新状态
    update_state(
        source_url=args.source,
        source_xml_path=str(xml_path),
        img_tokens=img_tokens,
        img_dir=str(img_dir),
        output_dir=str(output_dir),
    )

    print_step("完成", "源文档已读取，图片已下载。")
    print(f"  源文档 XML: {xml_path}")
    print(f"  图片目录: {img_dir}")
    print(f"  图片数量: {len(img_tokens)}")


if __name__ == "__main__":
    main()
