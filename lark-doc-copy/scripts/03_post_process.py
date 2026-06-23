#!/usr/bin/env python3
"""
后处理（综合）：在新文档创建后，依次完成内容迁移与修复

执行以下子步骤：
1. 构建 source → new block ID 映射
2. 更新锚点链接和 cite 引用
3. 上传图片到新文档末尾
4. 移动图片到正确位置
5. 修复图片显示尺寸（scale）
6. 还原并排图 grid + 迁移画板（whiteboard）
7. 修复有序列表的 seq
8. 合并连续 blockquote

输入参数：
  （自动从 state.json 读取）

输出：
  - state.json: 更新所有映射和移动后的状态
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    fetch_doc_xml,
    load_state,
    print_progress,
    print_step,
    run_lark_cli_json,
    update_state,
    xml_to_blocks,
)


def build_id_mapping(state: Dict) -> Dict[str, str]:
    """
    第 4 步：构建 source_block_id → new_block_id 映射

    通过文本内容匹配建立映射。
    """
    print_step("第 4 步：构建 block ID 映射")

    # 读取源文档
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    source_blocks = xml_to_blocks(source_xml)

    # 读取新文档
    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids")
    if not new_xml:
        print("❌ 无法读取新文档")
        return {}
    new_blocks = xml_to_blocks(new_xml)

    print_progress(f"源文档 blocks: {len(source_blocks)}")
    print_progress(f"新文档 blocks: {len(new_blocks)}")

    # 通过文本匹配建立映射
    # 对每个源 block（排除 img），在新文档中找匹配
    used_new_ids = set()
    mapping = {}

    for sb in source_blocks:
        if sb["tag"] == "img":
            continue
        if not sb["id"] or not sb["all_text"]:
            continue

        # 在新文档中找匹配
        for nb in new_blocks:
            if nb["id"] in used_new_ids:
                continue
            if nb["tag"] == sb["tag"] and nb["all_text"] == sb["all_text"]:
                mapping[sb["id"]] = nb["id"]
                used_new_ids.add(nb["id"])
                break

    print_progress(f"映射建立: {len(mapping)} 个")

    # 找出未映射的源 block（通常是嵌套的 li）
    unmapped = [
        sb for sb in source_blocks
        if sb["id"] and sb["all_text"] and sb["id"] not in mapping
    ]
    if unmapped:
        print_progress(f"未映射的源 block: {len(unmapped)}（多为嵌套 li，由 04_verify.py 核验是否丢失/重复）")

    update_state(id_mapping=mapping)
    return mapping


def update_anchors(state: Dict, mapping: Dict[str, str]) -> None:
    """
    第 5 步：更新目录锚点链接

    （cite @文档 / 文档链接的重指向已移到 process_cites.py，第 8.6 步统一处理）
    """
    print_step("第 5 步：更新目录锚点链接")

    new_doc_id = state["new_doc_id"]
    new_doc_url = state["new_doc_url"]
    source_url = state["source_url"]

    # 提取源文档的 host 和 token
    host_match = re.search(r'https://([^/]+)/docx/([^/]+)', source_url)
    if host_match:
        source_host = host_match.group(1)
        source_token = host_match.group(2)
    else:
        print("  ⚠ 无法解析源 URL，使用默认值")
        return

    # 提取新文档的 host
    new_host_match = re.search(r'https://([^/]+)/docx/', new_doc_url)
    new_host = new_host_match.group(1) if new_host_match else source_host

    # 读取新文档
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not new_xml:
        return

    # 1. 找到 TOC 中的 li 块（包含 old URL 的）
    li_pattern = re.compile(r'<li\s+id="([^"]+)"[^>]*>(.*?)</li>', re.DOTALL)

    updates = []
    for m in li_pattern.finditer(new_xml):
        bid = m.group(1)
        body = m.group(2)
        if source_host not in body:
            continue

        # 找到老的锚点 ID
        anchor_match = re.search(rf'#{source_token}#([^"]+)', body)
        if not anchor_match:
            anchor_match = re.search(r'#([A-Za-z0-9]+)', body)
        if not anchor_match:
            continue

        old_anchor = anchor_match.group(1)
        new_anchor = mapping.get(old_anchor)
        if not new_anchor:
            continue

        # 替换 URL
        new_url = f'https://{new_host}/docx/{new_doc_id}#{new_anchor}'
        new_body = re.sub(
            r'href="https://[^"]+"',
            f'href="{new_url}"',
            body,
            count=1,
        )

        # 用 block_replace 替换
        new_content = f'<li>{new_body}</li>'
        updates.append((bid, new_content))

    print_progress(f"需要更新 {len(updates)} 个 TOC 锚点")

    # 执行更新
    for bid, new_content in updates:
        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_replace",
            "--block-id", bid,
            "--content", new_content,
        ], timeout=60)
        if not result or not result.get("ok"):
            print(f"  ⚠ 更新 {bid} 失败")

    # 注意：cite @文档引用（含自引用）的重指向已统一移到 process_cites.py
    # （str_replace 无法匹配 XML 属性，旧实现实际不生效）。本步骤只负责目录锚点。
    print_progress("目录锚点更新完成")


def upload_images(state: Dict) -> List[Dict]:
    """
    第 6 步：上传图片到新文档末尾

    Returns: [{orig_token, new_block_id, file_token}]
    """
    print_step("第 6 步：上传图片")

    img_dir = Path(state["img_dir"])
    new_doc_id = state["new_doc_id"]
    img_tokens = state.get("img_tokens", [])

    uploaded = []
    failed = []

    for token in img_tokens:
        img_path = img_dir / f"{token}.png"
        if not img_path.exists():
            failed.append(token)
            continue

        # 用相对 cwd 的路径（lark-cli 要求相对路径；img_dir 可能是相对或绝对，
        # os.path.relpath 两种都能正确处理）
        rel_path = os.path.relpath(img_path, Path.cwd())

        # media-insert 偶发瞬时失败也会漏图，做有限次重试（与 download_image 对称）
        result = None
        for _attempt in range(3):
            result = run_lark_cli_json([
                "docs", "+media-insert",
                "--doc", new_doc_id,
                "--file", rel_path,
                "--type", "image",
            ], timeout=120)
            if result and result.get("ok"):
                break

        if result and result.get("ok"):
            data = result.get("data", {})
            uploaded.append({
                "orig_token": token,
                "new_block_id": data.get("block_id"),
                "file_token": data.get("file_token"),
            })
        else:
            failed.append(token)

    print_progress(f"上传成功: {len(uploaded)}/{len(img_tokens)}")
    if failed:
        print_progress(f"失败: {failed[:3]}...")

    update_state(uploaded_images=uploaded)
    return uploaded


def compute_image_anchors(state: Dict, mapping: Dict[str, str]) -> List[Dict]:
    """
    为每张图片计算正确的 anchor

    Returns: [{orig_token, new_anchor_id}]
    """
    print_step("第 7 步：计算图片 anchor")

    # 读取源文档
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()

    # 解析源文档结构
    src_blocks = xml_to_blocks(source_xml)

    # 找到每张图片的位置
    image_anchors = []
    for i, b in enumerate(src_blocks):
        if b["tag"] != "img":
            continue

        # 关键：orig_token 必须取 img 的 src 属性（与 upload_images / img_tokens
        # 的 key 一致），不能用 block id —— 源文档里 <img id="blkA" src="tokenB">
        # 的 id 与 src 是两个不同的值，用 id 会导致 move_images 永远匹配不上。
        img_match = re.search(
            rf'<img\b[^>]*?id="{re.escape(b["id"])}"[^>]*?/>', source_xml
        ) if b.get("id") else None
        img_token = None
        if img_match:
            src_m = re.search(r'\ssrc="([^"]+)"', img_match.group(0))
            if src_m:
                img_token = src_m.group(1)
            else:
                name_m = re.search(r'\sname="([^"]+)"', img_match.group(0))
                if name_m:
                    n = name_m.group(1)
                    img_token = n[:-4] if n.endswith((".png", ".jpg")) else n
        if not img_token:
            continue

        # 找到图片应紧跟其后的「前驱 top-level block」，据此决定移动策略。
        # 规则（见 references/image-positioning.md 场景 A/B/C + api-limitations 5.1）：
        #   - 前驱是 p/h1-3/callout/blockquote → direct：直接 anchor 到该 block
        #     （top-level anchor，block_move_after 可靠落位）
        #   - 前驱是 grid（图片在并排容器内/后）→ direct：anchor 到 grid 之前的文本块
        #   - 前驱是 ol/ul → 命中「容器末项 anchor 陷阱」(5.1)：不能直接把 img
        #     移到 ol 最后一个 li 之后。改用 two_step：先把 img 移到「后继 p」之后，
        #     再把该后继 p 移到 img 之后 → 得到 ol, img, p（两步都用 top-level anchor）。
        #     若找不到可用后继（如夹在两个 ol 之间、或文末），退回 fallback（ol 末 li）。
        # pre（代码块）也是可直接锚定的顶级文本块：源文档常见「代码块 → 截图」，
        # 若不含 pre，图片前驱是代码块时会落入 fallback、无锚点、卡在文末
        # （实测 bug：OpenClaw 指南 9 张紧跟代码块的截图全部漂到文末）。
        # 注：h1-h3 不在列表——`compute_image_anchors` 看到的是 xml_to_blocks 解析
        # 的顶级块，顶级 heading 不会成为图的直接前驱（前驱一定是 p/callout/
        # blockquote/pre/li/img）。xml_to_blocks 不递归进 heading，嵌套标题里的
        # 图由 `move_nested_images`（第 7.05 步）单独处理。
        MAPPABLE_TOP = ("p", "callout", "blockquote", "pre")

        # 空 <p>（分隔空行）在文本映射里没有 key，也不是有意义的锚点；
        # 找前驱/后继时一律跳过，否则会得到 anchor_new_id=None（实测：3 张图
        # 因前驱是空 p 而无法移动）。
        def is_anchorable_top(blk):
            if blk["depth"] != 0:
                return False
            # 连续堆叠图片（源 `文本→图A→图B`）：图B 的前驱若取成图A（img），
            # 后面没有「前驱是 img」的分支处理 → 图B 拿不到 anchor、留在文末（实测
            # bug：第二张「行业流量大盘」漂到无关章节）。跳过 img，让同组图片都锚到
            # 上游同一个文本块；move_images 反向移动同 anchor 多图，顺序自然正确。
            if blk["tag"] == "img":
                return False
            if blk["tag"] == "p" and not blk["all_text"]:
                return False
            return True

        pred_idx = None
        for j in range(i - 1, -1, -1):
            if is_anchorable_top(src_blocks[j]):
                pred_idx = j
                break

        mode = "fallback"
        anchor_src_id = None        # direct：前驱 block；two_step：后继 block
        fallback_src_id = None      # ol 末 li（two_step/fallback 兜底）
        blank_gap = 0               # 前驱文本块与图片之间的空 <p> 个数（保留图前空行）

        if pred_idx is not None:
            pb = src_blocks[pred_idx]
            if pb["tag"] in MAPPABLE_TOP:
                mode, anchor_src_id = "direct", pb["id"]
                # 数前驱文本块和图片之间夹了几个空 <p>（源文档常用空行隔开图片）。
                # is_anchorable_top 跳过空 p，导致图片直接 anchor 到文本块、图前
                # 空行丢失（实测 bug：「背景图设置」图前空行没了）。记下个数，
                # move_images 把图 anchor 到对应的空 p 之后，保留空行。
                for j in range(pred_idx + 1, i):
                    if src_blocks[j]["tag"] == "p" and not src_blocks[j]["all_text"]:
                        blank_gap += 1
            elif pb["tag"] == "grid":
                # 图片在 grid 内/后：anchor = grid 之前的文本块（grid 在新文档里不保留）
                for j in range(pred_idx - 1, -1, -1):
                    if is_anchorable_top(src_blocks[j]):
                        if src_blocks[j]["tag"] in MAPPABLE_TOP:
                            mode, anchor_src_id = "direct", src_blocks[j]["id"]
                        break
            elif pb["tag"] in ("ol", "ul"):
                # ol 末 li 作兜底 anchor
                for k in range(pred_idx + 1, len(src_blocks)):
                    if src_blocks[k]["depth"] == 0:
                        break
                    if src_blocks[k]["tag"] == "li":
                        fallback_src_id = src_blocks[k]["id"]
                # 向后找第一个可用后继（非空、被另一个容器挡住则放弃 two_step）
                succ_src_id = None
                for k in range(i + 1, len(src_blocks)):
                    if src_blocks[k]["depth"] != 0:
                        continue
                    if src_blocks[k]["tag"] in MAPPABLE_TOP and src_blocks[k]["all_text"]:
                        succ_src_id = src_blocks[k]["id"]
                        break
                    if src_blocks[k]["tag"] in ("ol", "ul", "grid"):
                        break
                if succ_src_id:
                    mode, anchor_src_id = "two_step", succ_src_id
                else:
                    mode, anchor_src_id = "fallback", fallback_src_id

        image_anchors.append({
            "orig_token": img_token,
            "mode": mode,
            "anchor_new_id": mapping.get(anchor_src_id) if anchor_src_id else None,
            "fallback_new_id": mapping.get(fallback_src_id) if fallback_src_id else None,
            "blank_gap": blank_gap,
        })

    modes = defaultdict(int)
    for p in image_anchors:
        modes[p["mode"]] += 1
    print_progress(f"计算了 {len(image_anchors)} 个图片的 anchor（{dict(modes)}）")
    return image_anchors


def _find_empty_p_after_ol(new_xml: str, last_li_id: str):
    """在新文档里定位「包含 last_li_id 的顶级 ol」紧跟其后的空 <p> 的 id。

    `clean_xml` 删除 ol 之间/之后的 img 时，会在原图片位置留下一个空 <p>
    （ol-img-ol 主动插入的占位符，或源文档本就有的空行）。这个空 p 正好是
    图片该去的槽位，且是 top-level block，作为 block_move_after 的 anchor
    可靠落位，不受 5.1「容器末项」陷阱影响，也不需要移动 heading。
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(f"<root>{new_xml}</root>")
    except ET.ParseError:
        return None
    children = list(root)
    for idx, ch in enumerate(children):
        if ch.tag in ("ol", "ul") and any(li.get("id") == last_li_id for li in ch.iter("li")):
            if idx + 1 < len(children):
                nxt = children[idx + 1]
                if nxt.tag == "p" and not "".join(nxt.itertext()).strip():
                    return nxt.get("id")
            return None
    return None


def _nth_empty_p_after(new_xml: str, block_id: str, n: int):
    """返回新文档中 block_id 后面**紧邻连续**的第 n 个空 <p> 的 id。

    用于 direct 模式保留「图前空行」：源文档 `文本块 → 空p×n → img`，把图片
    anchor 到文本块后第 n 个空 p，使图片落在空行之后（而非紧贴文本块）。
    只数紧邻连续的空 p，遇到非空块即停止。
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(f"<root>{new_xml}</root>")
    except ET.ParseError:
        return None
    parent_map = {c: p for p in root.iter() for c in p}
    target = None
    for el in root.iter():
        if el.get("id") == block_id:
            target = el
            break
    if target is None:
        return None
    parent = parent_map.get(target)
    if parent is None:
        return None
    kids = list(parent)
    idx = kids.index(target)
    count = 0
    for j in range(idx + 1, len(kids)):
        ch = kids[j]
        if ch.tag == "p" and not "".join(ch.itertext()).strip():
            count += 1
            if count == n:
                return ch.get("id")
        else:
            break
    return None


def move_images(state: Dict, image_anchors: List[Dict]) -> int:
    """
    第 7 步：移动图片到正确位置

    按**反向源文档顺序**逐张处理（保证同一 anchor 的多张图最终顺序正确，
    且从右往左移动可减少相互干扰）。所有 block_move_after 的 anchor 均为
    top-level block，规避 5.1 末项陷阱。策略：
    - direct：anchor 到前驱文本块
    - ol 前驱（two_step/fallback）：优先用「ol 后的空 p 占位符」作 anchor
      （最稳，覆盖 ol-img-ol 夹心、ol-img-heading 等场景）；找不到占位符时
      退回 two_step（移动后继 p）或 fallback（ol 末 li）
    """
    print_step("第 7 步：移动图片")

    new_doc_id = state["new_doc_id"]
    uploaded = {u["orig_token"]: u["new_block_id"] for u in state.get("uploaded_images", [])}
    # 一次性读取新文档，用于定位空 p 占位符（li id 稳定、移动图片不改其 id）
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids") or ""

    def mv(anchor: str, src: str) -> bool:
        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_move_after",
            "--block-id", anchor,
            "--src-block-ids", src,
        ], timeout=60)
        return bool(result and result.get("ok"))

    success = 0
    failed = []
    for plan in reversed(image_anchors):
        tok = plan["orig_token"]
        if tok not in uploaded:
            failed.append(tok)
            continue
        img_id = uploaded[tok]

        if plan["mode"] == "direct" and plan["anchor_new_id"]:
            anchor = plan["anchor_new_id"]
            # 图前有空行：anchor 到文本块后第 blank_gap 个空 p，保留空行
            if plan.get("blank_gap"):
                emp = _nth_empty_p_after(new_xml, anchor, plan["blank_gap"])
                if emp:
                    anchor = emp
            ok = mv(anchor, img_id)
        else:
            # ol 前驱：先试空 p 占位符锚点
            ok = False
            placeholder = (
                _find_empty_p_after_ol(new_xml, plan["fallback_new_id"])
                if plan.get("fallback_new_id") else None
            )
            if placeholder:
                ok = mv(placeholder, img_id)
            if not ok and plan["mode"] == "two_step" and plan["anchor_new_id"]:
                succ = plan["anchor_new_id"]
                ok = mv(succ, img_id) and mv(img_id, succ)
            if not ok:
                anchor = plan["anchor_new_id"] or plan["fallback_new_id"]
                ok = bool(anchor) and mv(anchor, img_id)

        if ok:
            success += 1
        else:
            failed.append(tok)

    print_progress(f"移动成功: {success}/{len(image_anchors)}")
    if failed:
        print_progress(f"失败/待人工: {failed[:5]}")
    return success


# xml_to_blocks 会递归进入的容器（其余标签——尤其 h1-h9 折叠标题、p、table——
# 不递归，嵌套其中的图片对 compute_image_anchors 不可见）
_XTB_CONTAINERS = ("callout", "blockquote", "ol", "ul", "grid", "column")
_NESTED_ANCHOR_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6",
                       "callout", "pre", "blockquote", "li")


def _et_flatten(xml: str):
    """用 ElementTree 按文档顺序扁平化（递归进所有容器），返回 (元素列表, 父映射)。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(f"<root>{xml}</root>")
    flat, parent = [], {}
    def walk(e):
        for c in e:
            flat.append(c)
            parent[c] = e
            walk(c)
    walk(root)
    return flat, parent, root


def move_nested_images(state: Dict, mapping: Dict[str, str]) -> int:
    """第 7.05 步：移动嵌套在折叠标题/段落里的图片。

    飞书「折叠标题」(可折叠 heading) 把其下整段内容作为**子块嵌套进 heading 元素**，
    `xml_to_blocks` 不递归进 heading/p，导致这些嵌套图片对 `compute_image_anchors`
    不可见、从不被移动、全堆在文末（实测：OpenClaw 指南「内置 API 模型」等 21 张
    嵌套图丢位）。本步骤用 ElementTree 全量扁平化补救：
      1. 扁平化源文档，找出 `xml_to_blocks` 漏掉的嵌套图片（祖先链含非递归容器）
      2. 每张图取「最近的前驱可锚文本块」(p/h/callout/pre/blockquote/li 含文字) 作锚点
      3. 在新文档(同样全量扁平化, 含折叠标题内子块)里按 (tag, 文本) 定位锚点 block id
      4. `block_move_after` 把图(已上传在文末)移到锚点后；同锚点多图按反向源序
    """
    print_step("第 7.05 步：移动嵌套图片（折叠标题/段落内）")

    new_doc_id = state["new_doc_id"]
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()

    sflat, sparent, _ = _et_flatten(source_xml)

    def is_visible(el):
        """xml_to_blocks 能否看到该元素：祖先链只能由可递归容器组成。"""
        e = sparent.get(el)
        while e is not None and e.tag != "root":
            if e.tag not in _XTB_CONTAINERS:
                return False
            e = sparent.get(e)
        return True

    # 收集嵌套图片（src token + 源文档顺序 + 锚点文本）
    nested = []  # (token, order_idx, anchor_tag, anchor_text)
    for i, el in enumerate(sflat):
        if el.tag != "img":
            continue
        tok = el.get("src")
        if not tok or is_visible(el):
            continue
        anchor = None
        for j in range(i - 1, -1, -1):
            pe = sflat[j]
            if pe.tag in _NESTED_ANCHOR_TAGS:
                t = "".join(pe.itertext()).strip()
                if t:
                    anchor = (pe.tag, t)
                    break
        if anchor:
            nested.append((tok, i, anchor[0], anchor[1]))

    if not nested:
        print_progress("无嵌套图片，跳过")
        return 0
    print_progress(f"发现嵌套图片: {len(nested)} 张")

    # 新文档全量扁平化：(tag, 文本) -> [block id]，并按 name 定位图片块当前 id
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
    nflat, _, _ = _et_flatten(new_xml)
    text2id = defaultdict(list)
    img_name2id = {}
    for el in nflat:
        if el.tag in _NESTED_ANCHOR_TAGS and el.get("id"):
            t = "".join(el.itertext()).strip()
            if t:
                text2id[(el.tag, t)].append(el.get("id"))
        elif el.tag == "img" and el.get("id"):
            nm = el.get("name", "")
            if nm.endswith((".png", ".jpg")):
                nm = nm[:-4]
            img_name2id[nm] = el.get("id")

    def find_anchor_id(tag, text):
        ids = text2id.get((tag, text))
        if ids:
            return ids[0]
        for (tg, tx), v in text2id.items():
            if tx == text:
                return v[0]
        return None

    def mv(anchor, src):
        r = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2", "--doc", new_doc_id,
            "--command", "block_move_after", "--block-id", anchor,
            "--src-block-ids", src,
        ], timeout=60)
        return bool(r and r.get("ok"))

    # 反向源序移动：同锚点多图最终保持正序
    success = 0
    for tok, _, atag, atext in sorted(nested, key=lambda x: x[1], reverse=True):
        img_id = img_name2id.get(tok)
        anchor_id = find_anchor_id(atag, atext)
        if img_id and anchor_id and mv(anchor_id, img_id):
            success += 1
        else:
            print_progress(f"  ⚠ 嵌套图 {tok[:12]} 移动失败（img={bool(img_id)} anchor={bool(anchor_id)}）")

    print_progress(f"嵌套图片移动成功: {success}/{len(nested)}")
    return success


def fix_image_sizes(state: Dict) -> int:
    """
    第 7.5 步：修复图片显示尺寸（scale + width + height）

    飞书 `docs +create` 不会保留源文档的图片 scale，新文档所有图片
    默认为 scale=1.000000（全尺寸）。但源文档通常用 scale 把图片缩到
    合理显示大小（如 0.4 左右），让版面紧凑。

    本函数读取源文档的 img 标签属性（width/height/scale），通过
    `block_replace` 更新新文档对应图片的属性。

    匹配方式（见 references/image-positioning.md）：
    - 源文档：img 的 src="<orig_token>" → 用 src 作 key
    - 新文档：img 的 name="<orig_token>.png" → 去掉 .png/.jpg 后
      是更长的全 token；用前缀匹配（源 src 是新 name 的前缀）

    Returns: 更新的图片数量
    """
    print_step("第 7.5 步：修复图片显示尺寸（scale）")

    new_doc_id = state["new_doc_id"]
    uploaded = {u["orig_token"]: u["new_block_id"] for u in state.get("uploaded_images", [])}

    # 1. 解析源文档，建立 orig_token → (w, h, scale) 映射
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()

    src_imgs = re.findall(r'<img\s+([^>]+)/>', source_xml)
    src_attrs = {}  # orig_token (src) → {w, h, scale}
    for attrs in src_imgs:
        src_m = re.search(r'src="([^"]+)"', attrs)
        w_m = re.search(r'width="([^"]+)"', attrs)
        h_m = re.search(r'height="([^"]+)"', attrs)
        s_m = re.search(r'scale="([^"]+)"', attrs)
        if src_m:
            src_attrs[src_m.group(1)] = {
                "w": w_m.group(1) if w_m else "",
                "h": h_m.group(1) if h_m else "",
                "scale": s_m.group(1) if s_m else "1.000000",
            }

    # 2. 读取新文档当前所有 img 标签
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not new_xml:
        print_progress("无法读取新文档")
        return 0

    new_imgs = re.findall(r'<img\s+([^>]+)/>', new_xml)

    # 3. 对每张新图片，找源 token，更新 width/height/scale
    #    注意：不能只比/只改 scale。media-insert 偶发把图片原生 width/height 写成
    #    占位的 100×100（实测：问答集 8 张图有 3 张如此），此时即便 scale 与源一致，
    #    显示尺寸 = 原生尺寸 × scale 仍然偏小。必须连 width/height 一起还原，
    #    且判断"是否需要更新"要同时看三者，否则 scale 相同的图会被早退跳过。
    success = 0
    for attrs in new_imgs:
        name_m = re.search(r'name="([^"]+)"', attrs)
        bid_m = re.search(r'id="([^"]+)"', attrs)
        new_scale_m = re.search(r'scale="([^"]+)"', attrs)
        new_w_m = re.search(r'width="([^"]+)"', attrs)
        new_h_m = re.search(r'height="([^"]+)"', attrs)
        if not name_m or not bid_m:
            continue

        name_token = name_m.group(1).replace('.png', '').replace('.jpg', '')
        new_scale = new_scale_m.group(1) if new_scale_m else "1.000000"
        new_w = new_w_m.group(1) if new_w_m else ""
        new_h = new_h_m.group(1) if new_h_m else ""

        # 找匹配的源 src（前缀匹配）
        matched = None
        for src_token, sdata in src_attrs.items():
            if name_token.startswith(src_token):
                matched = sdata
                break

        if not matched:
            continue
        # 三者都已一致才跳过（width/height 缺失则不参与比较）
        if (matched["scale"] == new_scale
                and (not matched["w"] or matched["w"] == new_w)
                and (not matched["h"] or matched["h"] == new_h)):
            continue

        # 构造新 img 标签，替换 width/height/scale（缺失的属性补上）
        new_attrs = attrs
        for prop, val in (("scale", matched["scale"]), ("width", matched["w"]), ("height", matched["h"])):
            if not val:
                continue
            if re.search(rf'\s{prop}="[^"]*"', new_attrs):
                new_attrs = re.sub(rf'\s{prop}="[^"]*"', f' {prop}="{val}"', new_attrs)
            else:
                new_attrs = new_attrs.rstrip() + f' {prop}="{val}"'
        new_tag = f'<img {new_attrs}/>'

        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_replace",
            "--block-id", bid_m.group(1),
            "--content", new_tag,
        ], timeout=60)

        if result and result.get("ok"):
            success += 1
        else:
            err = result.get("error", {}) if result else {}
            print_progress(f"更新图片 {bid_m.group(1)[:20]} scale 失败: {err.get('message', '')[:80]}")

    print_progress(f"图片尺寸修复: 成功 {success}")
    return success


def _list_doc_image_aligns(doc_id: str) -> Dict[str, int]:
    """用原生 docx blocks API 列出文档所有图片块的非居中对齐。

    返回 {image_token: align}，只收 align 1（左）/ 3（右）；居中（2 或缺省）省略。
    分页拉取（page_size 500），有上限兜底防失控。
    """
    aligns = {}
    page_token = None
    for _ in range(50):
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        result = run_lark_cli_json([
            "api", "GET",
            f"/open-apis/docx/v1/documents/{doc_id}/blocks",
            "--params", json.dumps(params),
        ], timeout=120)
        data = (result or {}).get("data")
        if not data:
            break
        for b in data.get("items", []):
            if b.get("block_type") == 27:  # image block
                img = b.get("image", {})
                if img.get("align") in (1, 3):
                    aligns[img.get("token")] = img["align"]
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return aligns


def fix_image_align(state: Dict) -> int:
    """第 7.55 步：还原图片对齐（左/右）。

    飞书图片对齐（align：1=左 2=中 3=右）是 docx 原生 block 属性，XML 接口不保留，
    media-insert 上传默认居中——源文档里左/右对齐的图全变居中（实测：OpenClaw 指南
    4 张左对齐截图变居中）。本步骤用原生 blocks API 读源图 align，对非居中的图用
    `replace_image`（同 token、带 align）设回。

    **必须在 fix_image_sizes 之后跑**：XML block_replace 改 scale 会清掉 align；
    且 `replace_image` 不带 scale 会把 scale 重置成 1。故这里读新图**当前**
    width/height/scale 一并随 align 传给 `replace_image`，align 和 scale 都不丢。
    """
    print_step("第 7.55 步：还原图片对齐（左/右）")

    from cite_lib import _inspect_token  # 复用 wiki→docx 解析
    src = _inspect_token(state["source_url"])
    if not src:
        print_progress("⚠ 无法解析源文档 token，跳过对齐还原")
        return 0
    src_aligns = _list_doc_image_aligns(src[0])
    if not src_aligns:
        print_progress("源文档无左/右对齐图片，跳过")
        update_state(image_align_fixed={"src": 0, "done": 0})
        return 0
    print_progress(f"源文档非居中对齐图片: {len(src_aligns)} 张")

    new_doc_id = state["new_doc_id"]
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
    success = 0
    for tok, al in src_aligns.items():
        m = re.search(rf'<img\b[^>]*name="{re.escape(tok)}\.png"[^>]*/>', new_xml)
        if not m:
            continue
        tag = m.group(0)
        bid = re.search(r'\bid="([^"]+)"', tag)
        nsrc = re.search(r'\bsrc="([^"]+)"', tag)
        w = re.search(r'\bwidth="([^"]+)"', tag)
        h = re.search(r'\bheight="([^"]+)"', tag)
        if not (bid and nsrc and w and h):
            continue
        sc = re.search(r'\bscale="([^"]+)"', tag)
        img_body = {"token": nsrc.group(1), "width": int(w.group(1)),
                    "height": int(h.group(1)), "align": al}
        if sc:
            img_body["scale"] = float(sc.group(1))  # 保住 fix_image_sizes 设好的 scale
        r = run_lark_cli_json([
            "api", "PATCH",
            f"/open-apis/docx/v1/documents/{new_doc_id}/blocks/{bid.group(1)}",
            "--data", json.dumps({"replace_image": img_body}),
        ], timeout=60)
        if r and r.get("ok"):
            success += 1

    print_progress(f"对齐还原: {success}/{len(src_aligns)}")
    update_state(image_align_fixed={"src": len(src_aligns), "done": success})
    return success


def fix_list_seq(state: Dict, mapping: Dict[str, str]) -> int:
    """
    第 8 步：上下文感知的有序列表 seq 修复

    飞书 create 时把所有 li seq 设为 "1"，且无法去除。
    通过分析源文档的 ol 结构（per-ol + 跨 ol 隐式续号 混合算法），
    为每个 li 计算期望的 seq。

    关键逻辑：
    - 维护一个 last_seq 追踪器，记录上一个 ol 最后一项的 seq
    - 每个顶级 ol 根据第一个 li 是否显式 seq 决定模式：
      - 显式 seq → 显式新列表：per-ol 计数器从 0 开始
      - 无显式 seq → 隐式续号：从 last_seq+1 继续
    - 源 li 有显式 seq：使用该 seq，并覆盖当前计数器
    - 源 li 无 seq：计数器 +1，使用新值
    - 嵌套 ol 不参与外层 ol 计数器，独立编号（从 1 开始）

    为什么用混合而不是单一计数器：
    - 纯全局计数器：第一个 li 有显式 seq 时不更新，后续 li 全部 -1
      （实测：9 项 OL 末尾「……」被赋成 5 而不是 9）
    - 纯 per-ol 计数器：忽略「隐式续号」场景
      （实测：OL(灰豚=1) + OL(Kimi 无 seq) 的 Kimi 被赋成 1 而不是 2）
    - 混合模型：源文档用「第一个 li 显式 seq」标记新列表，用「无 seq」标记续号

    历史 bug：
    - 2026-06-17 v1 全局计数器 → 9 项 OL 末尾「……」错赋为 5
    - 2026-06-17 v2 per-ol 计数器 → Kimi 错赋为 1
    - 2026-06-17 v3 混合模型（当前）→ 全部正确
    """
    print_step("第 8 步：上下文感知的 seq 修复")

    new_doc_id = state["new_doc_id"]

    # 重新读取新文档（结构已变化）
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not new_xml:
        return 0

    # 读取源文档
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()

    # 计算期望 seq（基于源文档的 ol 序列和 li 文本）
    # 注：用文本前缀作为 key（因为 block_replace 后 ID 会变），value 是该文本在
    # 文档里依次出现的 seq 列表 —— 同名 li（如多处「……」）按文档顺序逐个消费，
    # 避免纯文本 key 互相覆盖（实测 bug：3 个「……」末项被覆盖成 5 而非 9）。
    expected_text_seqs = _compute_expected_seqs(source_xml)
    total_li = sum(len(v) for v in expected_text_seqs.values())
    print_progress(f"源文档共 {total_li} 个 li 需要设置期望 seq")

    # Step 2: 在新文档中按文档顺序找对应 li，逐个消费同名文本的 seq
    import xml.etree.ElementTree as ET
    wrapped = f"<root>{new_xml}</root>"
    root = ET.fromstring(wrapped)

    success = 0
    skipped = 0
    failed = 0
    consume = {}  # 文本 key → 已消费到的下标（处理重复文本）

    for elem in root.iter("li"):
        text = "".join(elem.itertext()).strip()
        if not text:
            continue

        key = text[:40]
        seqs = expected_text_seqs.get(key)
        if not seqs:
            skipped += 1
            continue

        idx = consume.get(key, 0)
        if idx >= len(seqs):
            # 新文档该文本的 li 比源文档多（异常），跳过多余的
            skipped += 1
            continue
        consume[key] = idx + 1

        expected_seq = seqs[idx]
        current_seq = elem.get("seq")
        li_id = elem.get("id")

        if not li_id or current_seq == expected_seq:
            skipped += 1
            continue

        # 构造 block_replace 内容（保留 li 的所有内部内容）
        li_xml = ET.tostring(elem, encoding="unicode")
        li_xml = re.sub(r'\s+seq="[^"]*"', '', li_xml)
        li_xml = re.sub(r'(<li\b[^>]*)', rf'\1 seq="{expected_seq}"', li_xml, count=1)

        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_replace",
            "--block-id", li_id,
            "--content", li_xml,
        ], timeout=60)

        if result and result.get("ok"):
            success += 1
        else:
            failed += 1
            err = result.get("error", {}) if result else {}
            if failed <= 3:
                print_progress(f"修复 {li_id} 失败: {err.get('message', '')[:80]}")

    print_progress(f"seq 修复成功: {success} / 跳过: {skipped} / 失败: {failed}")
    return success


def _compute_expected_seqs(source_xml: str) -> Dict[str, List[str]]:
    """
    计算源文档中每个 li 的期望 seq 值

    算法（per-ol + 跨 ol 自动续号 混合）：
    1. 遍历源文档的顶级 ol（按文档顺序）
    2. 维护一个 last_seq 追踪器，记录上一个 ol 最后一项的 seq
    3. 对每个顶级 ol，根据其**第一个 li 是否有显式 seq** 决定模式：
       - 第一个 li 有显式 seq → 「显式新列表」：per-ol 计数器从 0 开始
         （如 ol 内 li seq=1, 2, 3, ... 或 seq=1 + 无 seq 的续号）
       - 第一个 li 无显式 seq → 「隐式续号」：从 last_seq+1 开始累加
         （如 OL(灰豚=1) → OL(Kimi 无 seq) → Kimi=2）
    4. 显式 seq 总是覆盖：遇到显式 seq 时，把当前计数器更新为该值
    5. 嵌套 ol（li 内的 ol）不参与外层 ol 计数器，独立从 1 开始

    判别示例（用户文档实测）：
      OL「拆解对标的维度」9 项，第一项 seq=1 → 显式新列表 → 1,2,...,9
      OL「灰豚」单项 seq=1 → 显式新列表 → 1
      OL「Kimi」单项无 seq → 隐式续号 → 2（接 灰豚=1）
      OL「对标选题」seq=1 → 显式新列表 → 1
      OL「关键词选题」seq=2 → 显式新列表（用显式 2 覆盖计数器） → 2
      OL「评论区选题」seq=3 → 显式新列表 → 3
      OL「其他平台选题」seq=4 → 显式新列表 → 4

    返回 `{文本前缀: [seq, seq, ...]}`：同一文本在多个列表里重复出现时（如多处
    「……」占位项），按文档顺序保存多个 seq，由 fix_list_seq 逐个消费，避免覆盖。

    历史 bug：
    - v1 全局计数器：li 有显式 seq 时不更新计数器，导致 9 项 OL 末尾
      「……」被赋值 5 而不是 9（少 1）
    - v2 per-ol 计数器：忽略了「隐式续号」场景，导致 Kimi 被赋值 1
      而不是 2（接 灰豚=1）
    - v3 混合模型：显式 vs 隐式分别处理，覆盖所有场景
    - v4（当前）：value 改为 seq 列表，修复「重复文本互相覆盖」——3 个「……」
      末项被覆盖成 5 而非 9（2026-06-18 用户报告）
    """
    import xml.etree.ElementTree as ET
    wrapped = f"<root>{source_xml}</root>"
    root = ET.fromstring(wrapped)

    expected_seqs: Dict[str, List[str]] = {}

    # 找出所有「独立有序列表」：没有 ol 祖先的 ol —— 即文档级的有序列表。
    # 不能只用 root.findall("ol")（只取 root 直接子节点），因为源文档常把 ol
    # 包在 <p>/<callout> 等容器里（如 <p><b>其他</b><ol>...</ol></p>），这些
    # ol 不是 root 直接子节点会被漏掉，导致整段列表不编号、且打乱重复文本
    # （如「……」）的消费对齐（实测 bug：p 包裹的「其他」列表被漏，拆解列表
    # 末项「……」错位）。用文档顺序的前序遍历收集，嵌套 ol 由 _process_nested_ol 处理。
    # 每个顶级 ol 记录是否处于引用块（blockquote）内。引用块在飞书里是独立的
    # 编号作用域：块内列表不接外层续号、也不影响外层后续列表的续号（实测 bug
    # 2026-06-18：《虚拟电商手册》顶级「指令」列表第一项内嵌一个引用块包裹的
    # 1-5 子列表，旧逻辑把它当顶级列表纳入续号链，导致后续「爆款标题」等项被
    # 错赋成 6/7/8/9 而非 2/3/4/5）。
    top_ols: List = []

    def _collect_top_ols(node, inside_ol, in_bq):
        for ch in list(node):
            if ch.tag == "ol":
                if not inside_ol:
                    top_ols.append((ch, in_bq))
                _collect_top_ols(ch, True, in_bq)
            elif ch.tag == "blockquote":
                _collect_top_ols(ch, inside_ol, True)
            else:
                _collect_top_ols(ch, inside_ol, in_bq)

    _collect_top_ols(root, False, False)

    last_seq = 0     # 追踪文档级上一个 ol 最后一项的 seq（用于隐式续号）
    bq_last_seq = 0  # 追踪引用块内上一个 ol 最后一项的 seq（独立作用域）

    for ol, in_bq in top_ols:
        lis = ol.findall("li")
        if not lis:
            continue

        # 离开引用块作用域时重置块内计数（不同引用块互不续号）
        if not in_bq:
            bq_last_seq = 0

        # 决定这个 ol 的模式：第一个 li 有显式 seq → 显式新列表
        first_explicit = lis[0].get("seq")
        if first_explicit:
            counter = 0
        else:
            # 隐式续号：引用块内接 bq_last_seq，文档级接 last_seq
            counter = bq_last_seq if in_bq else last_seq

        for li in lis:
            text = "".join(li.itertext()).strip()
            if not text:
                continue
            explicit_seq = li.get("seq")
            has_nested = li.find("ol") is not None

            if explicit_seq:
                current_seq = int(explicit_seq)
                counter = current_seq
            else:
                counter += 1
                current_seq = counter
            # 同一文本可能在多个列表里重复出现（如多处「……」占位项），用
            # 「文本 → seq 列表（文档顺序）」而非「文本 → seq」，避免后写覆盖
            # 前者（实测 bug：3 个「……」互相覆盖，9 项 OL 末尾「……」被覆盖成 5）。
            expected_seqs.setdefault(text[:40], []).append(str(current_seq))
            # 引用块内的列表不更新文档级 last_seq，避免污染外层续号
            if in_bq:
                bq_last_seq = current_seq
            else:
                last_seq = current_seq

            # 递归处理嵌套 ol（如果有），独立从 1 开始
            if has_nested:
                _process_nested_ol(li.find("ol"), expected_seqs)

    return expected_seqs


def _process_nested_ol(nested_ol, expected_seqs: Dict[str, List[str]]):
    """处理嵌套 ol，从 1 开始独立编号"""
    counter = 0
    for li in nested_ol.findall("li"):
        text = "".join(li.itertext()).strip()
        if not text:
            continue
        explicit_seq = li.get("seq")
        has_nested = li.find("ol") is not None

        if explicit_seq:
            counter = int(explicit_seq)
        else:
            counter += 1
        # 与 _compute_expected_seqs 一致：追加到列表（文档顺序），不覆盖
        expected_seqs.setdefault(text[:40], []).append(str(counter))

        if has_nested:
            _process_nested_ol(li.find("ol"), expected_seqs)


def merge_consecutive_blockquotes(state: Dict) -> int:
    """
    合并连续 blockquote 为统一的盒子

    飞书 create 时会给所有 <p> 加 id，导致连续 blockquote 渲染为独立盒子。
    解决方案：把连续的多个 blockquote 合并为 1 个 blockquote，里面包含多个 <p>。

    详见 references/api-limitations.md 限制 11。
    """
    print_step("第 9 步：合并连续 blockquote")

    new_doc_id = state["new_doc_id"]

    # 重新读取新文档
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not new_xml:
        print_progress("无法读取新文档，跳过")
        return 0

    # 找到连续的 blockquote 组
    # 模式：</blockquote><blockquote> 中间没有其他 top-level block
    bq_pattern = re.compile(r'<blockquote\s+id="([^"]+)"[^>]*>(.*?)</blockquote>', re.DOTALL)

    # 找到所有 blockquote 及其结束位置
    bq_matches = list(bq_pattern.finditer(new_xml))
    if len(bq_matches) < 2:
        print_progress("没有连续 blockquote 需要合并")
        return 0

    # 按结束位置排序（finditer 已经按这个顺序）
    # 找出连续组（每组的第一个保留，其他删除并合并到第一个）
    groups = []  # [(start_idx, [bq_match, bq_match, ...]), ...]
    current_group = []

    for i, m in enumerate(bq_matches):
        if i == 0:
            current_group.append(m)
        else:
            # 检查当前 bq 和上一个 bq 之间是否有其他 top-level block
            prev_end = bq_matches[i - 1].end()
            curr_start = m.start()
            between = new_xml[prev_end:curr_start]
            # 去除空白和简单换行，看是否还有其他 block 元素
            between_clean = re.sub(r'<p\s+id="[^"]*"\s*></p>', '', between)
            between_clean = between_clean.strip()

            # 如果中间没有其他 block（如 h1, p, img, ol, ul），则是连续的
            has_other_block = bool(re.search(
                r'<(h[1-9]|p\b|img\b|ol\b|ul\b|table|grid)',
                between_clean
            ))

            if not has_other_block:
                current_group.append(m)
            else:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = [m]

    # 处理最后一组
    if len(current_group) >= 2:
        groups.append(current_group)

    print_progress(f"找到 {len(groups)} 组连续 blockquote")

    if not groups:
        print_progress("没有需要合并的连续 blockquote")
        return 0

    success = 0
    for group in groups:
        if len(group) < 2:
            continue

        first_bq = group[0]
        first_bq_id = first_bq.group(1)

        # 提取每个 blockquote 的内容（保留所有格式化）
        contents = []
        for m in group:
            # 提取 <p> 的内容
            p_match = re.search(r'<p[^>]*>(.*?)</p>', m.group(2), re.DOTALL)
            if p_match:
                contents.append(p_match.group(1))
            else:
                # 如果没有 p，使用整个内部
                contents.append(m.group(2))

        # 构造合并后的 blockquote
        merged = '<blockquote>'
        for c in contents:
            merged += f'<p>{c}</p>'
        merged += '</blockquote>'

        # 替换第一个
        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_replace",
            "--block-id", first_bq_id,
            "--content", merged,
        ], timeout=60)

        if not (result and result.get("ok")):
            err = result.get("error", {}) if result else {}
            print_progress(f"替换 {first_bq_id} 失败: {err.get('message', '')[:100]}")
            continue

        # 删除其他
        for m in group[1:]:
            other_id = m.group(1)
            del_result = run_lark_cli_json([
                "docs", "+update", "--api-version", "v2",
                "--doc", new_doc_id,
                "--command", "block_delete",
                "--block-id", other_id,
            ], timeout=60)

        success += 1
        print_progress(f"合并 {len(group)} 个 blockquote 为 1 个（保留 first_bq {first_bq_id[:15]}）")

    print_progress(f"合并成功: {success} 组")
    return success


# 引用块里需要清除的「灰底」背景色：
# - rgb(229,230,233) 源文档原值（非标准浅灰，源端几乎不可见）
# - rgb(242,243,245) 飞书 create 归一化后的标准灰色高亮（渲染成明显灰盒子）
# 只清这两个灰值，保留黄/蓝等有意高亮（如 rgba(255,246,122,0.8) 荧光笔）。
_BQ_GRAY_BG = ("rgb(229,230,233)", "rgb(242,243,245)")


def _preceding_sibling_id(new_xml: str, target_id: str):
    """返回 new_xml 中 target_id 这个 block 的「前一个有 id 的同级兄弟」的 id。

    用于给 rebuild_grids 找 grid 应插入的锚点（grid 第一张图前面的那个块）。
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(f"<root>{new_xml}</root>")
    except ET.ParseError:
        return None
    parent_map = {c: p for p in root.iter() for c in p}
    for el in root.iter():
        if el.get("id") == target_id:
            parent = parent_map.get(el)
            if parent is None:
                return None
            kids = list(parent)
            idx = kids.index(el)
            for j in range(idx - 1, -1, -1):
                if kids[j].get("id"):
                    return kids[j].get("id")
            return None
    return None


def _parse_source_image_grids(source_xml: str) -> List[List[tuple]]:
    """解析源文档里「每列都含一张图」的 grid，返回每个 grid 的
    [(width-ratio, img_src_token), ...]（按列顺序）。

    只处理图片 grid（并排图布局）——这类 grid 在 create 时图片被剥离、空 grid
    被 clean_xml 删除，导致并排图变竖排，需要 rebuild_grids 还原。文本列 grid
    在 create 时能保留，不在此列。
    """
    grids = []
    for g in re.finditer(r"<grid\b[^>]*>(.*?)</grid>", source_xml, re.DOTALL):
        cols = re.findall(r"<column\b([^>]*)>(.*?)</column>", g.group(1), re.DOTALL)
        items = []
        ok = True
        for attrs, body in cols:
            img_m = re.search(r'<img\b[^>]*?\bsrc="([^"]+)"', body)
            if not img_m:
                ok = False  # 有列不是单图 → 跳过这个 grid（交给人工）
                break
            ratio_m = re.search(r'width-ratio="([^"]+)"', attrs)
            items.append((ratio_m.group(1) if ratio_m else "", img_m.group(1)))
        if ok and len(items) >= 2:
            grids.append(items)
    return grids


def _ratios_to_percents(ratios):
    """把源 grid 的分数列宽（如 [0.24, 0.22, 0.54]）换算成整数百分比列表，
    且保证和为 100（最大余数法）。任一比例缺失（None/空）则返回 None，
    表示无法精确还原，调用方应保持等宽。
    """
    vals = []
    for r in ratios:
        try:
            vals.append(float(r))
        except (TypeError, ValueError):
            return None
    total = sum(vals)
    if total <= 0:
        return None
    scaled = [v / total * 100 for v in vals]
    floors = [int(x) for x in scaled]
    remainder = 100 - sum(floors)
    # 把剩余的整数额度按小数部分从大到小补给各列
    order = sorted(range(len(scaled)), key=lambda i: scaled[i] - floors[i], reverse=True)
    for i in range(remainder):
        floors[order[i % len(floors)]] += 1
    # 飞书要求每列至少 1
    if any(p < 1 for p in floors):
        return None
    return floors


def _set_grid_ratios(doc_id: str, grid_block_id: str, ratios) -> bool:
    """用飞书 docx 原生 API 设置 grid 各列宽（整数百分比）。

    `block_insert_after` 建 grid 时飞书会忽略 width-ratio、强制等宽（限制 16），
    必须建好后用 update_grid_column_width_ratio 二次设置才能还原非等宽布局。
    """
    percents = _ratios_to_percents(ratios)
    if not percents:
        return False
    import json as _json
    res = run_lark_cli_json([
        "api", "PATCH",
        f"/open-apis/docx/v1/documents/{doc_id}/blocks/{grid_block_id}",
        "--as", "user",
        "--data", _json.dumps({"update_grid_column_width_ratio": {"width_ratios": percents}}),
    ], timeout=60)
    return bool(res and res.get("code") == 0)


def rebuild_grids(state: Dict) -> int:
    """
    第 7.6 步：还原并排图 grid 布局

    源文档用 <grid><column><img/></column>... 让多张图并排显示。但 create 时
    图片被剥离、空 grid 被 clean_xml 删除，图片变成竖排的独立 block（限制 16）。
    图片是 token 存储的、无法在 XML 里重建（insert 含 token 的 grid 会被飞书换成
    占位图），所以必须把**已上传的 img block 移进新建 grid 的列**。

    可行手法（已验证）：
      1. block_insert_after 在锚点后插入带占位 <p> 的 grid：
         <grid><column width-ratio><p>__GSn__</p></column>...</grid>
      2. block_move_after(占位 p, img) 把每张图移进对应列（移到列内 p 之后即落入列）
      3. block_delete 删掉占位 p

    注意：width-ratio 会被飞书归一化（多列趋于等宽），无法保留精确比例（小差异，
    类似灰底归一化）。本步在 fix_image_sizes 之后运行（图片已就位、scale 已修），
    每个 grid 处理前都重新 fetch（block_replace/move 会改 id）。
    """
    print_step("第 7.6 步：还原并排图 grid 布局")

    new_doc_id = state["new_doc_id"]
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()

    grids = _parse_source_image_grids(source_xml)
    if not grids:
        print_progress("源文档无并排图 grid，跳过")
        return 0
    print_progress(f"源文档并排图 grid: {len(grids)} 个")

    def upd(cmd, **kw):
        args = ["docs", "+update", "--api-version", "v2", "--doc", new_doc_id,
                "--command", cmd]
        for k, v in kw.items():
            args += ["--" + k.replace("_", "-"), v]
        return run_lark_cli_json(args, timeout=60)

    success = 0
    ratio_fixed = 0
    for gi, items in enumerate(grids):
        new_xml = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
        # 用 name="<token>.png" 找新文档里对应的 img block
        img_ids = []
        for _ratio, token in items:
            m = re.search(rf'<img id="([^"]+)" name="{re.escape(token)}\.png"', new_xml)
            if m:
                img_ids.append(m.group(1))
        if len(img_ids) != len(items):
            print_progress(f"grid#{gi} 图片未全找到（{len(img_ids)}/{len(items)}），跳过")
            continue

        # 幂等：图片已在 <column> 内说明该 grid 已还原，跳过（避免重复建 grid）
        if re.search(rf'<column\b[^>]*>(?:(?!</column>).)*?<img id="{re.escape(img_ids[0])}"',
                     new_xml, re.DOTALL):
            print_progress(f"grid#{gi} 已还原，跳过")
            continue

        anchor = _preceding_sibling_id(new_xml, img_ids[0])
        if not anchor:
            print_progress(f"grid#{gi} 找不到锚点，跳过")
            continue

        cols_xml = "".join(
            f'<column width-ratio="{(r or "0.5")}"><p>__GS{i}__</p></column>'
            for i, (r, _t) in enumerate(items)
        )
        ins = upd("block_insert_after", block_id=anchor, content=f"<grid>{cols_xml}</grid>")
        if not (ins and ins.get("ok")):
            print_progress(f"grid#{gi} 插入失败: {(ins or {}).get('error', {}).get('message', '')[:60]}")
            continue

        # 重新 fetch 定位占位 p（在 anchor 之后那段里）
        nx2 = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
        seg = nx2[nx2.find(f'id="{anchor}"'):]
        ph_ids = []
        moved = True
        for i, img_id in enumerate(img_ids):
            pm = re.search(rf'<p id="([^"]+)">__GS{i}__</p>', seg)
            if not pm:
                moved = False
                break
            ph_id = pm.group(1)
            ph_ids.append(ph_id)
            r = upd("block_move_after", block_id=ph_id, src_block_ids=img_id)
            if not (r and r.get("ok")):
                moved = False
                break
        if ph_ids:
            upd("block_delete", block_id=",".join(ph_ids))
        if moved:
            success += 1
            # 图片已就位后，用原生 API 还原非等宽列宽（block_insert_after 会强制等宽）
            nx3 = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
            gm = re.search(
                rf'<grid id="([^"]+)">(?:(?!</grid>).)*?<img id="{re.escape(img_ids[0])}"',
                nx3, re.DOTALL)
            if gm:
                ratios = [r for r, _t in items]
                if _set_grid_ratios(new_doc_id, gm.group(1), ratios):
                    ratio_fixed += 1
        else:
            print_progress(f"grid#{gi} 移动图片未完成，请人工检查")

    print_progress(f"还原 grid: {success}/{len(grids)}（列宽还原 {ratio_fixed}）")
    return success


def _get_all_api_blocks(new_doc_id: str) -> List[Dict]:
    """用飞书 docx blocks API 全量分页拉取新文档块。"""
    blocks: List[Dict] = []
    pt = None
    while True:
        params = {"page_size": 500}
        if pt:
            params["page_token"] = pt
        r = run_lark_cli_json([
            "api", "GET",
            f"/open-apis/docx/v1/documents/{new_doc_id}/blocks",
            "--params", json.dumps(params),
        ], timeout=120)
        if not r or not r.get("ok"):
            break
        data = r.get("data", {})
        blocks.extend(data.get("items", []))
        pt = data.get("page_token")
        if not data.get("has_more"):
            break
    return blocks


def fix_callout_imgs(state: Dict) -> int:
    """第 7.85 步：修复 callout 边界被破坏导致的图片被错误纳入。

    根因：`02_create_doc.py` 的 `clean_xml` 解析源 XML 时，飞书 docx 的 callout
    元素（含 emoji、backcolor、bordercolor 等扩展属性）边界识别不完整——`</callout>`
    在重建时被忽略，导致 callout 之外的 img 被错误吸进 callout children。视觉
    上 callout 边框会包住本应在外的图，且后续空 p 也错位（实测：OpenClaw 指南
    「产品使用教程」段后的 XpaLbjFBMo0Q 教程大图被 callout 吞掉、callout 后
    缺空 p 隔开）。

    修法：用飞书 docx blocks API 列出新文档所有 callout 块，扫描其 children，
    把 `block_type=27`（img）的 child 用 `block_move_after` 移出到 callout 之后
    —— 飞书 API 用 callout id 作 anchor 时会把 src 移到 callout 之外、成为
    顶级 block（parent=doc root）。同时在 callout 之后插入 1 个空 p，避免图紧贴
    callout 边框。
    """
    print_step("第 7.85 步：修复 callout 边界（移出错误纳入的图）")

    new_doc_id = state["new_doc_id"]
    blocks = _get_all_api_blocks(new_doc_id)
    by_id = {b.get("block_id"): b for b in blocks}
    moved = 0
    inserted = 0
    for b in blocks:
        if not b.get("callout"):
            continue
        cal_id = b.get("block_id")
        # 找出 callout children 里的 img
        img_children = []
        for ch in b.get("children", []) or []:
            cb = by_id.get(ch)
            if cb and cb.get("image") and cb.get("block_type") == 27:
                img_children.append((ch, cb["image"].get("name", "")))
        for img_id, name in img_children:
            r = run_lark_cli_json([
                "docs", "+update", "--api-version", "v2", "--doc", new_doc_id,
                "--command", "block_move_after",
                "--block-id", cal_id,
                "--src-block-ids", img_id,
            ], timeout=60)
            if r and r.get("ok"):
                moved += 1
                print_progress(f"  ✓ 移出 img {name[:24]} 自 callout {cal_id[:14]}")
            else:
                print_progress(f"  ⚠ 移出 img {img_id[:14]} 失败: {(r or {}).get('msg') or 'unknown'}")
        if img_children:
            # callout 后补 1 个空 p（避免图紧贴 callout 边框）
            # 重要：用 callout id 作 anchor，block_insert_after 把它插到 callout 之后
            r = run_lark_cli_json([
                "docs", "+update", "--api-version", "v2", "--doc", new_doc_id,
                "--command", "block_insert_after",
                "--block-id", cal_id,
                "--content", "<p></p>",
                "--doc-format", "xml",
            ], timeout=60)
            if r and r.get("ok"):
                inserted += 1
                print_progress(f"  ✓ callout {cal_id[:14]} 之后插入空 p")
    print_progress(f"callout 边界修复: 移出 {moved} 张图, 插入 {inserted} 个空 p")
    update_state(callout_fixed={"moved": moved, "inserted": inserted})
    return moved


def _preceding_mapped_anchor(src_blocks: List[Dict], i: int, mapping: Dict[str, str]):
    """为 src_blocks[i]（whiteboard / sheet 等需重建的对象）找新文档里的插入锚点。

    向前找最近的「已映射顶级块」：跳过空 p 和所有不保留/单独迁移的对象块
    （img、whiteboard、sheet、synced-source、grid）；前驱是 ol/ul 时容器 id
    不稳定，改用其末项已映射的 li 作锚点。找不到返回 None。
    """
    for j in range(i - 1, -1, -1):
        blk = src_blocks[j]
        if blk["depth"] != 0:
            continue
        if blk["tag"] in ("img", "whiteboard", "sheet", "synced-source", "grid"):
            continue
        if blk["tag"] == "p" and not blk["all_text"]:
            continue
        if blk["tag"] in ("ol", "ul"):
            last_li = None
            for k in range(j + 1, len(src_blocks)):
                if src_blocks[k]["depth"] == 0:
                    break
                sid = src_blocks[k]["id"]
                if src_blocks[k]["tag"] == "li" and sid and mapping.get(sid):
                    last_li = mapping[sid]
            if last_li:
                return last_li
            continue
        if blk["id"] and mapping.get(blk["id"]):
            return mapping[blk["id"]]
    return None


def _count_around_empty_p(flat, img_idx):
    """返回 (before, after)：img 前后紧邻的连续空 <p> 数。"""
    bk = 0
    for j in range(img_idx - 1, -1, -1):
        e = flat[j]
        if e.tag == "p" and not "".join(e.itertext()).strip():
            bk += 1
        else:
            break
    ak = 0
    for j in range(img_idx + 1, len(flat)):
        e = flat[j]
        if e.tag == "p" and not "".join(e.itertext()).strip():
            ak += 1
        else:
            break
    return bk, ak


def _img_token_from_el(el) -> list:
    """返回一个 img 元素可能的源 token 列表（src + 去掉后缀的 name）。
    源/新文档对源 token 的存放位置不一致（源用 src，新用 name；新 src 是飞书新 token），
    所以把两个候选都收下，调用方用 set 求交集。
    """
    out = []
    src = el.get("src") or ""
    if src:
        out.append(src)
    n = el.get("name") or ""
    if n.endswith((".png", ".jpg")):
        out.append(n[:-4])
    elif n:
        out.append(n)
    return out


def normalize_image_empty_p_around(state: Dict) -> int:
    """第 7.65 步：校准每张图前后空 <p> 数量与源一致。

    根因：`move_nested_images` 把嵌套在折叠标题里的图用 `block_move_after(前驱文本块, img)`
    移到位，**没有继承** `move_images` 的 `blank_gap` 机制。`block_move_after` 把图紧贴
    anchor 之后，**原本夹在 anchor 和图之间的图前空 p 被反吸到图后**——出现「互换」：
    源 (图前 1, 图后 0) → 新 (图前 0, 图后 1)。本步骤用源/新逐图比对 (before, after)，
    自动修正：
    - **互换** `(B_s, A_s) == (A_n, B_n)`：从图后挪一个空 p 到图前
    - **图前少** `B_s > B_n`：把图后一个空 p 挪到图前
    - **图后少** `A_s > A_n`：把图前一个空 p 挪到图后
    - 其他：记日志，留给人工
    """
    print_step("第 7.65 步：校准图片前后空 p 数量")

    import xml.etree.ElementTree as ET

    new_doc_id = state["new_doc_id"]
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    sroot = ET.fromstring(f"<root>{source_xml}</root>")
    sflat = [c for c in sroot.iter() if c is not sroot]

    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
    nroot = ET.fromstring(f"<root>{new_xml}</root>")
    nflat = [c for c in nroot.iter() if c is not nroot]

    # 源/新 token 索引：每个元素可能贡献多个候选 token（src + name 去后缀）
    s_tokens: Dict[str, tuple] = {}  # token -> (flat_idx, el)
    n_tokens: Dict[str, tuple] = {}
    for i, e in enumerate(sflat):
        if e.tag != "img":
            continue
        for t in _img_token_from_el(e):
            if t and t not in s_tokens:
                s_tokens[t] = (i, e)
    for i, e in enumerate(nflat):
        if e.tag != "img":
            continue
        for t in _img_token_from_el(e):
            if t and t not in n_tokens:
                n_tokens[t] = (i, e)

    common = set(s_tokens) & set(n_tokens)
    print_progress(f"源 {len(s_tokens)} / 新 {len(n_tokens)} token 候选, 交集 {len(common)}")

    fixed = skipped = failed = 0
    for tok, (si, _) in s_tokens.items():
        if tok not in n_tokens:
            continue
        sb, sa = _count_around_empty_p(sflat, si)
        ni, ne = n_tokens[tok]
        # 新文档可能已被前一步改动，重新 fetch 拉最新结构
        nb, na = _count_around_empty_p(nflat, ni)
        if (sb, sa) == (nb, na):
            continue

        # 用最新的 new_xml 重新定位 img 和空 p 的 id（首次 fetch 已够，
        # 后续操作可能改结构；为简单起见这里只复用首次结果——失败再 re-fetch）
        latest_new = fetch_doc_xml(new_doc_id, detail="with-ids") or ""
        nflat2 = nflat  # 默认复用首次结果
        if latest_new:
            try:
                nroot2 = ET.fromstring(f"<root>{latest_new}</root>")
                nflat2 = [c for c in nroot2.iter() if c is not nroot2]
            except ET.ParseError:
                pass
        # 优先用首次 nflat 里已经定位好的元素（已知 id 是有效字符串）
        new_img = ne  # ne = n_tokens[tok][1]
        # 验证在最新 nflat2 里也找得到（防御性）
        if any(e is new_img for e in nflat2):
            pass  # 同一个对象，直接用
        else:
            # 在 nflat2 里重新找
            for e in nflat2:
                if e.tag == "img" and tok in _img_token_from_el(e):
                    new_img = e
                    break
        if not new_img.get("id"):
            skipped += 1
            print_progress(f"  ⚠ {tok[:12]} 拿不到 id，跳过 (new_img type={type(new_img).__name__}, ne.id={ne.get('id')!r})")
            continue

        # 重新数新文档的 (before, after)
        idx2 = nflat2.index(new_img)
        nb2, na2 = _count_around_empty_p(nflat2, idx2)

        def mv_block(src_id, anchor_id):
            return run_lark_cli_json([
                "docs", "+update", "--api-version", "v2", "--doc", new_doc_id,
                "--command", "block_move_after", "--block-id", anchor_id,
                "--src-block-ids", src_id,
            ], timeout=60)

        def find_preceding_text_block(flat, img_idx):
            for j in range(img_idx - 1, -1, -1):
                e = flat[j]
                if e.tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6",
                             "callout", "blockquote", "pre", "li"):
                    t = "".join(e.itertext()).strip()
                    if t:
                        return e
            return None

        def find_trailing_empty_p(flat, img_idx):
            for j in range(img_idx + 1, len(flat)):
                e = flat[j]
                if e.tag == "p" and not "".join(e.itertext()).strip():
                    return e
                # 任何非空块出现就停
                if "".join(e.itertext()).strip():
                    return None
            return None

        def find_preceding_empty_p(flat, img_idx):
            for j in range(img_idx - 1, -1, -1):
                e = flat[j]
                if e.tag == "p" and not "".join(e.itertext()).strip():
                    return e
                if "".join(e.itertext()).strip():
                    return None
            return None

        img_id = new_img.get("id")
        diff_b = sb - nb2  # > 0 表示新文档图前少
        diff_a = sa - na2  # > 0 表示新文档图后少

        # 策略 1: 互换 → 找一个空 p 互换位置
        if (sb, sa) == (na2, nb2) and (sb + sa) > 0:
            # 互换：把图后的空 p 挪到图前
            # 即：先 block_move_after(前驱文本块, 空p) 让空 p 回到图前，
            # 然后 block_move_after(空p, img) 把图放回空 p 之后
            anchor_e = find_preceding_text_block(nflat2, idx2)
            ep_e = find_trailing_empty_p(nflat2, idx2)
            # 注：xml.etree.Element 无 __bool__ 重载，bool(e) 恒为 False，
            # 不能用 `if e` / `e and ...`，必须 `e is not None`
            cond = (anchor_e is not None and ep_e is not None
                    and anchor_e.get("id") and ep_e.get("id"))
            if not cond:
                print_progress(f"  ⚠ {tok[:12]} 互换但缺 anchor/空p "
                               f"(anchor={anchor_e.tag if anchor_e is not None else 'None'}(id={anchor_e.get('id') if anchor_e is not None else 'None'}) "
                               f"ep={ep_e.tag if ep_e is not None else 'None'}(id={ep_e.get('id') if ep_e is not None else 'None'}))")
            if cond:
                # 1. 把图后的空 p 移到前驱文本块之后
                r1 = mv_block(ep_e.get("id"), anchor_e.get("id"))
                # 2. 把图移到刚放回的空 p 之后
                if r1 and r1.get("ok"):
                    r2 = mv_block(img_id, ep_e.get("id"))
                    if r2 and r2.get("ok"):
                        fixed += 1
                        print_progress(f"  ✓ 互换 {tok[:12]}: src({sb},{sa}) -> 校准")
                    else:
                        failed += 1
                        print_progress(f"  ✗ {tok[:12]} 互换第二步失败")
                else:
                    failed += 1
                    print_progress(f"  ✗ {tok[:12]} 互换第一步失败")
            else:
                skipped += 1
            continue

        # 策略 2: 图前少 → 找图后的空 p 挪到图前
        if diff_b > 0 and diff_a == 0:
            anchor_e = find_preceding_text_block(nflat2, idx2)
            ep_e = find_trailing_empty_p(nflat2, idx2)
            if (anchor_e is not None and ep_e is not None
                    and anchor_e.get("id") and ep_e.get("id")):
                r1 = mv_block(ep_e.get("id"), anchor_e.get("id"))
                if r1 and r1.get("ok"):
                    r2 = mv_block(img_id, ep_e.get("id"))
                    if r2 and r2.get("ok"):
                        fixed += 1
                        print_progress(f"  ✓ 图前补空p {tok[:12]}: src({sb},{sa}) new->?")
                    else:
                        failed += 1
                else:
                    failed += 1
            else:
                skipped += 1
            continue

        # 策略 3: 图后少 → 找图前的空 p 挪到图后
        if diff_a > 0 and diff_b == 0:
            ep_e = find_preceding_empty_p(nflat2, idx2)
            if ep_e is not None and ep_e.get("id"):
                # block_move_after(img, ep_e) 即可把空 p 挪到图后
                r = mv_block(ep_e.get("id"), img_id)
                if r and r.get("ok"):
                    fixed += 1
                    print_progress(f"  ✓ 图后补空p {tok[:12]}: src({sb},{sa}) new->?")
                else:
                    failed += 1
            else:
                skipped += 1
            continue

        # 其他复杂情况：跳过，记日志
        skipped += 1
        print_progress(f"  ⚠ {tok[:12]} 复杂 case src({sb},{sa}) new({nb2},{na2})，未修")

    print_progress(f"空 p 校准: 成功 {fixed}, 跳过 {skipped}, 失败 {failed}")
    update_state(empty_p_normalized={"fixed": fixed, "skipped": skipped, "failed": failed})
    return fixed


def migrate_whiteboards(state: Dict, mapping: Dict[str, str]) -> int:
    """第 7.7 步：迁移源文档画板（whiteboard）。

    画板是 token 对象，`docs +create` 无法从跨租户 token 重建，会被静默丢弃。
    本步骤逐个还原（与图片迁移同思路）：
      1. 读源画板 raw 节点（`whiteboard +query --output_as raw`）
      2. 在对应锚点后插入空白画板块（`<whiteboard type="blank">`），拿 block_token
      3. 用 raw 覆盖写入（`whiteboard +update --input_format raw --overwrite`）
    **必须用 raw 而非 mermaid**：raw 保留原始坐标/尺寸/样式/连接器，布局逐字节一致；
    mermaid 会让飞书重新自动布局，丢掉原版排布。
    源画板无读取权限（跨租户禁读）时跳过该画板并告警。
    """
    print_step("第 7.7 步：迁移画板（whiteboard）")

    new_doc_id = state["new_doc_id"]
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    src_blocks = xml_to_blocks(source_xml)

    boards = []
    for i, b in enumerate(src_blocks):
        if b["tag"] != "whiteboard" or not b.get("id"):
            continue
        m = re.search(
            rf'<whiteboard\b[^>]*?id="{re.escape(b["id"])}"[^>]*?>', source_xml
        )
        tok = None
        if m:
            tm = re.search(r'\stoken="([^"]+)"', m.group(0))
            if tm:
                tok = tm.group(1)
        if not tok:
            continue
        boards.append({"src_token": tok, "anchor_new_id": _preceding_mapped_anchor(src_blocks, i, mapping)})

    if not boards:
        print_progress("源文档无画板，跳过")
        return 0

    print_progress(f"源文档画板: {len(boards)} 个")

    output_dir = Path(state.get("output_dir", "."))
    success = 0
    # 反向插入：同一 anchor 多个画板时 block_insert_after 会反序，反向遍历抵消
    for bd in reversed(boards):
        tok = bd["src_token"]
        anchor = bd["anchor_new_id"]
        if not anchor:
            print_progress(f"  ✗ 画板 {tok[:12]} 找不到锚点，跳过")
            continue

        # 1) 读源画板 raw 节点
        q = run_lark_cli_json([
            "whiteboard", "+query",
            "--whiteboard-token", tok,
            "--output_as", "raw",
        ], timeout=120)
        nodes = (q or {}).get("data", {}).get("nodes") if q and q.get("ok") else None
        if not nodes:
            print_progress(f"  ✗ 画板 {tok[:12]} raw 读取失败（无权限/跨租户），跳过")
            continue
        payload_path = output_dir / f"_wb_{tok[:12]}.json"
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump({"nodes": nodes}, f, ensure_ascii=False)

        # 2) 在锚点后插入空白画板，拿 block_token
        ins = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_insert_after",
            "--block-id", anchor,
            "--content", '<whiteboard type="blank"></whiteboard>',
            "--doc-format", "xml",
        ], timeout=60)
        new_token = None
        if ins and ins.get("ok"):
            for nb in ins.get("data", {}).get("document", {}).get("new_blocks", []):
                if nb.get("block_type") == "whiteboard":
                    new_token = nb.get("block_token")
                    break
        if not new_token:
            print_progress(f"  ✗ 画板 {tok[:12]} 空白块创建失败，跳过")
            payload_path.unlink(missing_ok=True)
            continue

        # 3) raw 覆盖写入（idempotent-token 需 ≥10 字符）
        upd = run_lark_cli_json([
            "whiteboard", "+update",
            "--whiteboard-token", new_token,
            "--input_format", "raw",
            "--overwrite",
            "--idempotent-token", f"wb{new_token[:14]}",
            "--source", f"@{payload_path.name}",
        ], timeout=120)
        payload_path.unlink(missing_ok=True)
        if upd and upd.get("ok"):
            success += 1
            print_progress(f"  ✓ 画板 {tok[:12]} 已还原（raw 保布局）")
        else:
            print_progress(f"  ⚠ 画板 {tok[:12]} 空白块已插入但 raw 写入失败，请人工补救")

    print_progress(f"画板迁移成功: {success}/{len(boards)}")
    return success


def _xml_escape(s: str) -> str:
    """转义单元格文本里的 XML 特殊字符（& < >），text() 内容只需这三个。"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _read_sheet_grid(token: str, sheet_id: str) -> List[List[str]]:
    """读内嵌电子表格的已用区域，返回去掉尾部空行/空列后的二维文本网格。"""
    result = run_lark_cli_json([
        "sheets", "+cells-get",
        "--spreadsheet-token", token,
        "--sheet-id", sheet_id,
        "--range", "A1:Z200",
    ], timeout=120)
    if not result or not result.get("ok"):
        return []
    rows = []
    for rg in result.get("data", {}).get("ranges", []):
        for row in rg.get("cells", []):
            rows.append(["" if c.get("value") in (None, "") else str(c.get("value")) for c in row])
    # 去尾部全空行
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        return []
    # 去尾部全空列
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    while ncol > 1 and all(not r[ncol - 1].strip() for r in rows):
        ncol -= 1
        rows = [r[:ncol] for r in rows]
    return rows


def _grid_to_table_xml(rows: List[List[str]]) -> str:
    """把二维文本网格渲染成飞书原生 table XML（首行作表头）。"""
    ncol = len(rows[0])
    cols = "".join("<col/>" for _ in range(ncol))
    head = "".join(f'<th vertical-align="top"><p>{_xml_escape(c)}</p></th>' for c in rows[0])
    body = ""
    for r in rows[1:]:
        body += "<tr>" + "".join(
            f'<td vertical-align="top"><p>{_xml_escape(c)}</p></td>' for c in r
        ) + "</tr>"
    return f'<table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def migrate_sheets(state: Dict, mapping: Dict[str, str]) -> int:
    """第 7.8 步：迁移源文档内嵌电子表格（sheet），渲染成原生 table。

    `<sheet token=... sheet-id=...>` 是 token 对象，`docs +create` 无法从跨租户
    token 重建、会被静默丢弃（实测：OpenClaw 指南 3 张内嵌表全部丢失）。本步骤读
    内嵌表格的单元格内容，渲染成飞书原生 table（首行作表头）插到对应锚点后——
    内容逐字一致、视觉接近，且不依赖跨租户复制权限。无读取权限时跳过并告警。
    """
    print_step("第 7.8 步：迁移内嵌表格（sheet）")

    new_doc_id = state["new_doc_id"]
    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    src_blocks = xml_to_blocks(source_xml)

    sheets = []
    for i, b in enumerate(src_blocks):
        if b["tag"] != "sheet" or not b.get("id"):
            continue
        m = re.search(rf'<sheet\b[^>]*?id="{re.escape(b["id"])}"[^>]*?>', source_xml)
        if not m:
            continue
        tok = re.search(r'\stoken="([^"]+)"', m.group(0))
        sid = re.search(r'\ssheet-id="([^"]+)"', m.group(0))
        if not tok or not sid:
            continue
        sheets.append({
            "token": tok.group(1), "sheet_id": sid.group(1),
            "anchor_new_id": _preceding_mapped_anchor(src_blocks, i, mapping),
        })

    if not sheets:
        print_progress("源文档无内嵌表格，跳过")
        return 0

    print_progress(f"源文档内嵌表格: {len(sheets)} 个")
    success = 0
    # 反向插入：同一 anchor 多个表时 block_insert_after 会反序，反向遍历抵消
    for sh in reversed(sheets):
        anchor = sh["anchor_new_id"]
        sig = f"{sh['token'][:10]}/{sh['sheet_id']}"
        if not anchor:
            print_progress(f"  ✗ 内嵌表 {sig} 找不到锚点，跳过")
            continue
        rows = _read_sheet_grid(sh["token"], sh["sheet_id"])
        if not rows:
            print_progress(f"  ✗ 内嵌表 {sig} 读取失败（无权限/空表），跳过")
            continue
        ins = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_insert_after",
            "--block-id", anchor,
            "--content", _grid_to_table_xml(rows),
            "--doc-format", "xml",
        ], timeout=60)
        if ins and ins.get("ok") and ins.get("data", {}).get("result") == "success":
            success += 1
            print_progress(f"  ✓ 内嵌表 {sig} 已还原为原生 table（{len(rows)} 行）")
        else:
            print_progress(f"  ⚠ 内嵌表 {sig} 插入失败，请人工补救")

    print_progress(f"内嵌表格迁移成功: {success}/{len(sheets)}")
    update_state(migrated_sheets={"src": len(sheets), "done": success})
    return success


def strip_blockquote_bg(state: Dict) -> int:
    """
    第 9.5 步：去除引用块（blockquote）文字的归一化灰色背景

    飞书 `docs +create` 会把源文档引用块里几乎不可见的浅灰 rgb(229,230,233)
    吸附成标准灰色高亮 rgb(242,243,245)，导致每行文字套上明显的灰盒子，与源
    文档视觉不一致（详见 references/api-limitations.md 限制 1）。

    本函数在合并连续 blockquote **之后**运行（操作最终的引用块），把引用块内
    spans 上的灰色 background-color 去掉，恢复成纯文字 + 左侧竖线的引用样式。
    仅清灰值，黄/蓝高亮保留。
    """
    print_step("第 9.5 步：去除引用块灰色背景")

    new_doc_id = state["new_doc_id"]
    new_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not new_xml:
        print_progress("无法读取新文档，跳过")
        return 0

    bq_pattern = re.compile(r'<blockquote\s+id="([^"]+)"[^>]*>(.*?)</blockquote>', re.DOTALL)

    success = 0
    for m in bq_pattern.finditer(new_xml):
        bq_id, inner = m.group(1), m.group(2)
        if not any(g in inner for g in _BQ_GRAY_BG):
            continue
        # 去内部 id（block_replace 要求无 id）+ 去灰色 background-color 属性
        new_inner = re.sub(r'\s+id="[^"]*"', '', inner)
        gray_alt = "|".join(re.escape(g) for g in _BQ_GRAY_BG)
        new_inner = re.sub(rf'\s+background-color="(?:{gray_alt})"', '', new_inner)
        content = f"<blockquote>{new_inner}</blockquote>"

        result = run_lark_cli_json([
            "docs", "+update", "--api-version", "v2",
            "--doc", new_doc_id,
            "--command", "block_replace",
            "--block-id", bq_id,
            "--content", content,
        ], timeout=60)

        if result and result.get("ok"):
            success += 1
        else:
            err = result.get("error", {}) if result else {}
            print_progress(f"去背景 {bq_id[:15]} 失败: {err.get('message', '')[:80]}")

    print_progress(f"去除灰底引用块: {success} 个")
    return success


def main():
    state = load_state()
    if not state.get("source_url") or not state.get("new_doc_id"):
        print("❌ 缺少必要状态")
        print("请先运行 01_fetch_source.py 和 02_create_doc.py")
        sys.exit(1)

    # 第 4 步：构建映射
    mapping = build_id_mapping(state)

    # 第 5 步：更新目录锚点
    update_anchors(state, mapping)

    # 第 6 步：上传图片
    upload_images(state)
    # 重新加载 state：update_state() 返回新 dict 而不修改入参，因此 main 里的
    # state 不会自动带上 uploaded_images，必须重新读盘，否则后续 move_images /
    # fix_image_sizes 拿到的是空的 uploaded_images（曾导致图片移动 0/33）。
    state = load_state()

    # 第 7 步：移动图片
    image_anchors = compute_image_anchors(state, mapping)
    move_images(state, image_anchors)

    # 第 7.05 步：移动嵌套在折叠标题/段落里的图片（xml_to_blocks 不可见）
    move_nested_images(state, mapping)

    # 第 7.5 步：修复图片显示尺寸（scale）
    fix_image_sizes(state)

    # 第 7.55 步：还原图片对齐（左/右）— 须在 fix_image_sizes 之后
    fix_image_align(state)

    # 第 7.6 步：还原并排图 grid 布局
    rebuild_grids(state)

    # 第 7.85 步：修复 callout 边界（move_nested_images / rebuild_grids 可能
    # 让图被错误吸进 callout children）
    fix_callout_imgs(state)

    # 第 7.65 步：校准图片前后空 p 数量（move_nested_images 没继承 blank_gap）
    normalize_image_empty_p_around(state)

    # 第 7.7 步：迁移画板（whiteboard）
    migrate_whiteboards(state, mapping)

    # 第 7.8 步：迁移内嵌表格（sheet → 原生 table）
    migrate_sheets(state, mapping)

    # 第 8 步：修复 seq
    fix_list_seq(state, mapping)

    # 第 9 步：合并连续 blockquote
    merge_consecutive_blockquotes(state)

    # 第 9.5 步：去除引用块归一化灰底
    strip_blockquote_bg(state)

    print_step("后处理完成", "所有迁移和修复已完成。请运行 04_verify.py 进行核验。")


if __name__ == "__main__":
    main()
