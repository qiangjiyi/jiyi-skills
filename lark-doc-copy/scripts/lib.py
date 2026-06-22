#!/usr/bin/env python3
"""
飞书文档复制 skill 通用工具库

提供 lark-cli 调用的 Python 包装、XML 解析、状态管理等。
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ===== 路径与状态管理 =====

def get_state_file() -> Path:
    """获取当前会话的状态文件路径"""
    return Path.cwd() / "state.json"


def load_state() -> Dict[str, Any]:
    """加载当前会话的状态"""
    sf = get_state_file()
    if sf.exists():
        with open(sf, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: Dict[str, Any]) -> None:
    """保存当前会话的状态"""
    sf = get_state_file()
    with open(sf, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def update_state(**kwargs) -> Dict[str, Any]:
    """更新状态"""
    state = load_state()
    state.update(kwargs)
    save_state(state)
    return state


# ===== lark-cli 调用包装 =====

def run_lark_cli(args: List[str], timeout: int = 60, check: bool = True) -> Tuple[int, str, str]:
    """
    调用 lark-cli 命令

    Returns: (returncode, stdout, stderr)
    """
    proc = subprocess.run(
        ["lark-cli"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(Path.cwd()),
    )
    if check and proc.returncode != 0:
        print(f"[lark-cli] command failed: {' '.join(args[:5])}...", file=sys.stderr)
        print(f"[lark-cli] stderr: {proc.stderr[:300]}", file=sys.stderr)
    return proc.returncode, proc.stdout, proc.stderr


def run_lark_cli_json(args: List[str], timeout: int = 60) -> Optional[Dict[str, Any]]:
    """调用 lark-cli 并解析 JSON 输出"""
    rc, out, err = run_lark_cli(args, timeout=timeout, check=False)
    if rc != 0:
        print(f"[lark-cli] failed: {err[:300]}", file=sys.stderr)
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        print(f"[lark-cli] JSON parse error. stdout: {out[:300]}", file=sys.stderr)
        return None


# ===== 文档读取 =====

def fetch_doc_content(doc: str, detail: str = "with-ids") -> Optional[Dict[str, Any]]:
    """读取飞书文档内容"""
    result = run_lark_cli_json([
        "docs", "+fetch",
        "--api-version", "v2",
        "--doc", doc,
        "--detail", detail,
    ])
    if not result or not result.get("ok"):
        return None
    return result.get("data", {}).get("document", {})


def fetch_doc_xml(doc: str, detail: str = "with-ids") -> Optional[str]:
    """读取飞书文档的 XML 内容"""
    doc_data = fetch_doc_content(doc, detail=detail)
    if not doc_data:
        return None
    return doc_data.get("content", "")


# ===== 图片处理 =====

def extract_image_tokens(xml_content: str) -> List[str]:
    """从 XML 中提取所有图片 token。

    按 <img> 标签逐个解析：优先用 src（源文档里的真实媒体 token），
    只有在没有 src 时才退回 name。**不能**把全文的 src 和 name 分别全量
    抽取——那样 name="image.png" 这类占位名会混进来变成 "image" 等假 token，
    导致后续下载失败。
    """
    tokens = []
    for m in re.finditer(r'<img\b[^>]*?/>', xml_content):
        tag = m.group(0)
        src_m = re.search(r'\ssrc="([^"]+)"', tag)
        if src_m:
            token = src_m.group(1)
        else:
            name_m = re.search(r'\sname="([^"]+)"', tag)
            if not name_m:
                continue
            name = name_m.group(1)
            token = name[:-4] if name.endswith((".png", ".jpg")) else name
        if token not in tokens:
            tokens.append(token)
    return tokens


def download_image(token: str, output_dir: Path, attempts: int = 3) -> Optional[Path]:
    """下载图片（用 media-preview 跨租户友好）。

    递归批量扒取大文档时，media-preview 偶发瞬时失败（网络 / 限流）会让图片被
    静默跳过、最终漏图（实测：得物文档 18 张漏 1 张）。这里做有限次重试，显著
    降低漏图概率。
    """
    import time
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{token}.png"

    for i in range(attempts):
        rc, _, _ = run_lark_cli([
            "docs", "+media-preview",
            "--token", token,
            "--output", str(output_path),
        ], check=False)
        if rc == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return output_path
        if i < attempts - 1:
            time.sleep(1.5 * (i + 1))
    return None


# ===== XML 解析 =====

def xml_to_blocks(content: str) -> List[Dict[str, Any]]:
    """
    解析 XML 为扁平的 block 列表

    Returns: list of {tag, id, depth, path, all_text, attributes}
    """
    import xml.etree.ElementTree as ET

    wrapped = f"<root>{content}</root>"
    root = ET.fromstring(wrapped)

    blocks = []

    def walk(elem, depth=0, path=None):
        if path is None:
            path = []
        for child in elem:
            tag = child.tag
            cid = child.get("id")
            new_path = path + [tag]

            # 获取所有文本（包括子元素）
            all_text = "".join(child.itertext()).strip()

            blocks.append({
                "tag": tag,
                "id": cid,
                "depth": depth,
                "path": new_path,
                "all_text": all_text,
            })

            # 递归遍历容器
            if tag in ("callout", "blockquote", "ol", "ul", "grid", "column"):
                walk(child, depth + 1, new_path)

    walk(root)
    return blocks


def get_image_context(
    blocks: List[Dict[str, Any]],
    img_index: int,
    radius: int = 3,
) -> Dict[str, str]:
    """获取图片位置 signature：取最近的一个非空顶级文本块作为前/后锚点。

    只取**单个**最近锚点（而不是固定数量、固定半径的多块拼接），这样当多张
    图片相邻成簇、或图片前后有空 p 时，signature 仍然稳定——只要图片落在同一
    对文本之间就判定一致。早期「取前后各 2 块拼接」的写法会因为图片成簇导致
    大量假阳性（同一位置被报成不一致）。`radius` 参数保留仅为兼容旧调用。
    """
    TEXT_TAGS = ("p", "h1", "h2", "h3", "callout", "blockquote")

    def nearest(rng):
        for j in rng:
            b = blocks[j]
            if b["depth"] == 0 and b["tag"] in TEXT_TAGS and b["all_text"]:
                return b["all_text"][:60]
        return ""

    return {
        "prev": nearest(range(img_index - 1, -1, -1)),
        "next": nearest(range(img_index + 1, len(blocks))),
    }


# ===== 文本块提取 =====

def extract_text_blocks(content: str) -> List[Dict[str, Any]]:
    """提取所有有文本内容的 block（用于内容核验）"""
    import xml.etree.ElementTree as ET

    def walk_text(elem, depth=0):
        results = []
        for child in elem:
            tag = child.tag
            if tag in ("p", "h1", "h2", "h3", "title", "li", "blockquote", "callout"):
                text = "".join(child.itertext()).strip()
                if text:
                    results.append({"tag": tag, "depth": depth, "text": text})
            elif tag in ("callout", "blockquote"):
                results.extend(walk_text(child, depth + 1))
        return results

    wrapped = f"<root>{content}</root>"
    root = ET.fromstring(wrapped)
    return walk_text(root)


# ===== 输出辅助 =====

def print_step(step_name: str, message: str = "") -> None:
    """打印步骤信息"""
    if message:
        print(f"\n=== {step_name} ===\n{message}")
    else:
        print(f"\n=== {step_name} ===")


def print_progress(message: str) -> None:
    """打印进度信息"""
    print(f"  • {message}", file=sys.stderr)
