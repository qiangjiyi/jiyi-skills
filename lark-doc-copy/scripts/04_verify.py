#!/usr/bin/env python3
"""
第 9-10 步：内容与图片位置核验

核验：
1. 文字内容是否完全一致（用 difflib SequenceMatcher）
2. 样式标签数量是否一致（b, em, code, a, cite, span, br）
3. 图片数量是否一致
4. Emoji 数量是否一致
5. 链接数量是否一致
6. 每张图片的位置 signature 是否一致

输出：
  - 核验报告（stdout）
  - state.json: 更新 verification_results
"""

import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    extract_text_blocks,
    fetch_doc_xml,
    get_image_context,
    load_state,
    print_progress,
    print_step,
    update_state,
    xml_to_blocks,
)


def verify_text_content(state: dict) -> dict:
    """第 9 步：内容完整性核验"""
    print_step("第 9 步：内容完整性核验")

    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids")
    if not new_xml:
        return {"text_diff_count": -1, "issues": ["无法读取新文档"]}

    # 文字块对比
    src_blocks = extract_text_blocks(source_xml)
    new_blocks = extract_text_blocks(new_xml)
    src_texts = [b["text"] for b in src_blocks]
    new_texts = [b["text"] for b in new_blocks]

    sm = SequenceMatcher(None, src_texts, new_texts)
    text_diff_count = sum(1 for tag, _, _, _, _ in sm.get_opcodes() if tag != "equal")

    print_progress(f"源文档文本块: {len(src_blocks)}")
    print_progress(f"新文档文本块: {len(new_blocks)}")
    print_progress(f"文本差异数: {text_diff_count}")

    # 样式标签数量对比
    def count_styles(content):
        counts = {}
        for tag in ['b', 'em', 'i', 'u', 's', 'del', 'code', 'a', 'cite']:
            counts[tag] = len(re.findall(f'<{tag}(?:\\s[^>]*)?>', content))
        counts['span'] = len(re.findall(r'<span\b', content))
        counts['br'] = len(re.findall(r'<br/?>', content))
        return counts

    src_styles = count_styles(source_xml)
    new_styles = count_styles(new_xml)

    # 校正 span 计数：strip_blockquote_bg 会去掉引用块内的灰底，去掉后只剩灰底
    # 的裸 <span> 被飞书折叠成纯文字（span 消失）。这是预期行为，比对时把源文档
    # 「引用块内仅灰底」的 span 从源计数里扣除，避免误报 span 数量差异。
    GRAY_BG = ("rgb(229,230,233)", "rgb(242,243,245)")
    collapsed = 0
    for bq in re.findall(r'<blockquote\b[^>]*>.*?</blockquote>', source_xml, re.DOTALL):
        for attrs in re.findall(r'<span\b([^>]*)>', bq):
            a = attrs.strip()
            if a.count('=') == 1 and a.startswith('background-color=') \
                    and any(g in a for g in GRAY_BG):
                collapsed += 1
    if collapsed:
        src_styles['span'] = max(0, src_styles['span'] - collapsed)
        print_progress(f"span 计数校正：扣除引用块灰底折叠 {collapsed} 个")

    style_diffs = []
    for tag in src_styles:
        if src_styles[tag] != new_styles.get(tag, 0):
            style_diffs.append({
                "tag": tag,
                "src": src_styles[tag],
                "new": new_styles.get(tag, 0),
            })

    print_progress(f"样式标签差异: {len(style_diffs)}")
    for d in style_diffs:
        print_progress(f"  {d['tag']}: src={d['src']}, new={d['new']}")

    # Emoji 数量
    emoji_pattern = re.compile(r'[\U0001F000-\U0001FFFF\U00002600-\U000027BF]')
    src_emojis = Counter(emoji_pattern.findall(source_xml))
    new_emojis = Counter(emoji_pattern.findall(new_xml))

    print_progress(f"源 emoji: {dict(src_emojis)}")
    print_progress(f"新 emoji: {dict(new_emojis)}")

    # 图片数量
    src_imgs = len(re.findall(r'<img[^>]*?/>', source_xml))
    new_imgs = len(re.findall(r'<img[^>]*?/>', new_xml))
    print_progress(f"图片数量: src={src_imgs}, new={new_imgs}")

    # 链接数量（排除图片 CDN）
    src_links = len([h for h in re.findall(r'href="([^"]+)"', source_xml) if 'feishu.cn' in h and 'internal-api-drive-stream' not in h])
    new_links = len([h for h in re.findall(r'href="([^"]+)"', new_xml) if 'feishu.cn' in h and 'internal-api-drive-stream' not in h])
    print_progress(f"飞书链接: src={src_links}, new={new_links}")

    return {
        "text_diff_count": text_diff_count,
        "text_blocks_src": len(src_blocks),
        "text_blocks_new": len(new_blocks),
        "style_diffs": style_diffs,
        "emoji_src": dict(src_emojis),
        "emoji_new": dict(new_emojis),
        "image_count_src": src_imgs,
        "image_count_new": new_imgs,
        "link_count_src": src_links,
        "link_count_new": new_links,
    }


def verify_image_positions(state: dict) -> dict:
    """第 10 步：图片位置核验"""
    print_step("第 10 步：图片位置核验")

    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids")
    if not new_xml:
        return {"mismatches": ["无法读取新文档"]}

    # 提取图片的 signature
    src_blocks = xml_to_blocks(source_xml)
    new_blocks = xml_to_blocks(new_xml)

    src_imgs = [(i, b) for i, b in enumerate(src_blocks) if b["tag"] == "img"]
    new_imgs = [(i, b) for i, b in enumerate(new_blocks) if b["tag"] == "img"]

    print_progress(f"源文档图片: {len(src_imgs)}, 新文档图片: {len(new_imgs)}")

    # 按原始 token 匹配图片
    src_by_token = {}
    for i, b in src_imgs:
        # 提取 src 或 name 作为 token
        img_match = re.search(rf'<img[^>]*?id="{re.escape(b["id"])}"[^>]*?>', source_xml)
        if img_match:
            tag_str = img_match.group(0)
            src_m = re.search(r'src="([^"]+)"', tag_str)
            name_m = re.search(r'name="([^"]+)"', tag_str)
            if src_m:
                src_by_token[src_m.group(1)] = (i, b)
            elif name_m:
                name = name_m.group(1)
                token = name[:-4] if name.endswith(".png") else name
                src_by_token[token] = (i, b)

    new_by_token = {}
    for i, b in new_imgs:
        img_match = re.search(rf'<img[^>]*?id="{re.escape(b["id"])}"[^>]*?>', new_xml)
        if img_match:
            tag_str = img_match.group(0)
            src_m = re.search(r'src="([^"]+)"', tag_str)
            name_m = re.search(r'name="([^"]+)"', tag_str)
            if name_m:
                name = name_m.group(1)
                token = name[:-4] if name.endswith(".png") else name
                new_by_token[token] = (i, b)

    mismatches = []
    for token, (src_idx, src_b) in src_by_token.items():
        if token not in new_by_token:
            mismatches.append({"token": token, "reason": "missing_in_new"})
            continue

        new_idx, new_b = new_by_token[token]

        src_sig = get_image_context(src_blocks, src_idx)
        new_sig = get_image_context(new_blocks, new_idx)

        if src_sig != new_sig:
            mismatches.append({
                "token": token,
                "reason": "context_mismatch",
                "src_prev": src_sig["prev"],
                "src_next": src_sig["next"],
                "new_prev": new_sig["prev"],
                "new_next": new_sig["next"],
            })

    print_progress(f"图片位置不匹配: {len(mismatches)}")
    for m in mismatches[:5]:
        print_progress(f"  {m.get('token', '?')[:20]}: {m.get('reason')}")
        if 'src_prev' in m:
            print_progress(f"    src: prev={m['src_prev'][:50]}, next={m['src_next'][:50]}")
            print_progress(f"    new: prev={m['new_prev'][:50]}, next={m['new_next'][:50]}")

    return {
        "image_count_src": len(src_imgs),
        "image_count_new": len(new_imgs),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def verify_ol_separation(state: dict) -> dict:
    """核验 ol 块是否被飞书解析器合并

    比较源文档和新文档的顶级 ol 数量，以及 li 文本签名。
    如果新文档的 ol 数量少于源文档，且 li 文本匹配多个源 ol，
    说明发生了 ol 合并 bug（飞书 ol 合并限制 5）。

    Returns: {"merged_count": int, "issues": [...]}
    """
    print_step("第 9.5 步：ol 分离核验")

    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids")
    if not new_xml:
        return {"merged_count": -1, "issues": ["无法读取新文档"]}

    import xml.etree.ElementTree as ET

    def count_top_ols_and_li_texts(xml: str):
        """统计顶级 ol 数量，并返回每个 ol 的 li 文本前缀列表"""
        wrapped = f"<root>{xml}</root>"
        try:
            root = ET.fromstring(wrapped)
        except ET.ParseError:
            return 0, []
        ols = root.findall("ol")
        result = []
        for ol in ols:
            lis = ol.findall("li")
            texts = []
            for li in lis:
                t = "".join(li.itertext()).strip()
                if t:
                    texts.append(t[:30])
            result.append(texts)
        return len(ols), result

    src_count, src_ols = count_top_ols_and_li_texts(source_xml)
    new_count, new_ols = count_top_ols_and_li_texts(new_xml)

    issues = []
    if src_count == 0:
        return {"merged_count": 0, "issues": []}

    # 合并的唯一可靠信号是「顶级 ol 数量减少」：N 个相邻 ol 被合成 1 个，
    # 顶级 ol 总数就会下降。数量持平或增多即说明没有发生合并。
    # （旧实现用「新 ol 含某源 ol 首项 li」做启发式，会把每个单项 ol 都误判成
    #   合并，在本例产生 66/67 的假阳性。）
    merged_count = max(0, src_count - new_count)

    # 仅在确实检测到合并时，才进一步列出可疑的新 ol 供人工核对
    if merged_count > 0:
        src_first_li = [ol[0] for ol in src_ols if ol]
        for new_ol_texts in new_ols:
            boundary_hits = [t for t in new_ol_texts if t in src_first_li[1:]]
            if boundary_hits:
                issues.append({
                    "type": "ol_merged",
                    "li_count": len(new_ol_texts),
                    "li_preview": new_ol_texts[:3],
                })

    print_progress(f"源文档顶级 ol: {src_count} 个 / 新文档顶级 ol: {new_count} 个 / 检测到合并: {merged_count} 个")
    return {"merged_count": merged_count, "issues": issues}


def verify_duplicate_li(state: dict) -> dict:
    """检查同一个 ol 内是否有重复的 li 内容

    这种问题通常出现在 block_replace 嵌套结构时：
    - block_replace 一个 li 可能导致相邻 li 出现内容重复
    - 需要提示用户并清理重复项
    """
    print_step("第 10.5 步：重复 li 内容检测")

    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids")
    if not new_xml:
        return {"duplicate_count": -1, "issues": ["无法读取新文档"]}

    import xml.etree.ElementTree as ET
    wrapped = f"<root>{new_xml}</root>"
    root = ET.fromstring(wrapped)

    issues = []
    for ol in root.iter("ol"):
        # Collect text for each direct li in this ol
        li_data = []
        for li in ol.findall("li"):
            text = "".join(li.itertext()).strip()
            li_data.append({
                "id": li.get("id"),
                "text": text,
            })

        # Check for duplicates within this ol
        text_to_ids = {}
        for li in li_data:
            if not li["text"] or len(li["text"]) <= 20:
                continue  # 跳过空文本或太短的文本
            if li["text"] in text_to_ids:
                text_to_ids[li["text"]].append(li["id"])
            else:
                text_to_ids[li["text"]] = [li["id"]]

        # Report duplicates
        for text, ids in text_to_ids.items():
            if len(ids) > 1:
                ol_preview = "".join(ol.itertext())[:80]
                issues.append({
                    "ol_preview": ol_preview,
                    "duplicate_text": text[:100],
                    "duplicate_ids": ids,
                })

    print_progress(f"重复 li 检测: {len(issues)} 处问题")

    for i, issue in enumerate(issues):
        print(f"\n  ⚠ 问题 {i+1}:")
        print(f"    ol 上下文: {issue['ol_preview']}")
        print(f"    重复文本: {issue['duplicate_text']}")
        print(f"    重复的 block_id: {issue['duplicate_ids']}")
        print(f"    建议: 保留第一个，删除其余 block_id")

    return {
        "duplicate_count": len(issues),
        "issues": issues,
    }


def verify_grids(state: dict) -> dict:
    """核验并排图 grid 是否还原（限制 16 / rebuild_grids）

    源文档「每列含一张图」的 grid 在 create 时会被拆成竖排独立图，由 rebuild_grids
    还原。这里对比源文档并排图 grid 数量与新文档 grid 数量，并检查新文档每个 grid
    的列里确实有图。
    """
    print_step("第 10.6 步：并排图 grid 还原核验")

    with open(state["source_xml_path"], "r", encoding="utf-8") as f:
        source_xml = f.read()
    new_xml = fetch_doc_xml(state["new_doc_id"]) or ""

    # 源文档「每列含一张图」的 grid 数
    src_img_grids = 0
    for g in re.finditer(r"<grid\b[^>]*>(.*?)</grid>", source_xml, re.DOTALL):
        cols = re.findall(r"<column\b[^>]*>(.*?)</column>", g.group(1), re.DOTALL)
        if cols and all(re.search(r"<img\b", c) for c in cols) and len(cols) >= 2:
            src_img_grids += 1

    # 新文档里「列内含图」的 grid 数
    new_img_grids = 0
    for g in re.finditer(r"<grid\b[^>]*>(.*?)</grid>", new_xml, re.DOTALL):
        cols = re.findall(r"<column\b[^>]*>(.*?)</column>", g.group(1), re.DOTALL)
        if cols and all(re.search(r"<img\b", c) for c in cols):
            new_img_grids += 1

    missing = max(0, src_img_grids - new_img_grids)
    print_progress(f"源并排图 grid: {src_img_grids} / 新文档图 grid: {new_img_grids}")
    if missing:
        print_progress(f"⚠ 有 {missing} 个并排图 grid 未还原，请检查 rebuild_grids 或人工补救")

    return {"src_img_grids": src_img_grids, "new_img_grids": new_img_grids, "missing": missing}


def verify_cites(state):
    """第 10.7 步：被引用文档（cite 递归）核验。

    校验两件事：
    1. cite_mapping 各状态统计（done / no_permission / skill_failed / depth_exceeded）
    2. 已成功复制（done）的引用，其旧 token 不应再残留在新文档里（重指向成功）
    """
    print_step("第 10.7 步：被引用文档（cite 递归）核验")

    mapping = state.get("cite_mapping") or []
    if not mapping:
        print_progress("主文档未引用其它飞书文档")
        return {"total": 0, "done": 0, "stale": 0}

    new_xml = fetch_doc_xml(state["new_doc_id"], detail="with-ids") or ""

    done = [m for m in mapping if m.get("status") == "done"]
    stale = []
    for m in done:
        old = m.get("old_token", "")
        # 旧 token 仍以 doc-id 或文档链接形式残留 → 重指向未完成
        if re.search(rf'doc-id="{re.escape(old)}"', new_xml) or \
           re.search(rf'/(?:docx|wiki|doc)/{re.escape(old)}\b', new_xml):
            stale.append(m.get("title", old))

    by_status = {}
    for m in mapping:
        by_status[m.get("status")] = by_status.get(m.get("status"), 0) + 1

    print_progress(f"引用文档：{len(mapping)} 个，状态分布 {by_status}")
    if stale:
        print_progress(f"⚠ {len(stale)} 个已复制引用仍残留旧链接（重指向未完成）：{stale}")

    return {
        "total": len(mapping),
        "done": len(done),
        "stale": len(stale),
        "by_status": by_status,
        "stale_titles": stale,
    }


def main():
    state = load_state()
    if not state.get("new_doc_id"):
        print("❌ 缺少新文档 ID")
        sys.exit(1)

    # 原生复制模式：副本由飞书端到端复制，结构与源逐字节一致，无扒取重建过程，
    # 故跳过文字/图片/列表/grid 核验（这些核验本就为「拆了重拼」的扒取路径设计，
    # 且原生路径没有 source_xml_path）。只核验 cite 重指向。
    if state.get("native_copy"):
        print_step("核验（原生复制模式）")
        print("  ✅ 原生复制：结构与源文档逐字节一致，跳过文字/图片/列表/grid 核验")
        cite_result = verify_cites(state)
        update_state(verification_results={"cites": cite_result, "native_copy": True})
        cite_total = cite_result.get("total", 0)
        if cite_total == 0:
            print("  ✅ 无被引用文档需处理")
        elif cite_result.get("stale", 0) == 0:
            print(f"  ✅ {cite_result.get('done', 0)}/{cite_total} 个被引用文档已复制并重指向")
        else:
            print(f"  ⚠ {cite_result['stale']} 个已复制引用仍残留旧链接，请重跑 process_cites.py")
        return

    text_result = verify_text_content(state)
    image_result = verify_image_positions(state)
    ol_result = verify_ol_separation(state)
    dup_result = verify_duplicate_li(state)
    grid_result = verify_grids(state)
    cite_result = verify_cites(state)

    results = {
        "text_content": text_result,
        "image_positions": image_result,
        "ol_separation": ol_result,
        "duplicate_li": dup_result,
        "grids": grid_result,
        "cites": cite_result,
    }

    update_state(verification_results=results)

    print_step("核验完成", "")
    if text_result.get("text_diff_count", -1) == 0:
        print("  ✅ 文字内容 100% 一致")
    elif text_result.get("text_diff_count", -1) > 0:
        print(f"  ⚠ 文字内容有 {text_result['text_diff_count']} 处差异")
    else:
        print(f"  ❌ 文字核验失败")

    if image_result.get("mismatch_count", -1) == 0:
        print("  ✅ 所有图片位置正确")
    elif image_result.get("mismatch_count", -1) > 0:
        print(f"  ⚠ {image_result['mismatch_count']} 张图片位置需要重新调整")
    else:
        print(f"  ❌ 图片核验失败")

    dup_count = dup_result.get("duplicate_count", -1)
    if dup_count == 0:
        print("  ✅ 无重复 li 内容")
    elif dup_count > 0:
        print(f"  ⚠ 发现 {dup_count} 处重复 li 内容（建议删除多余项）")
    else:
        print(f"  ❌ 重复 li 检测失败")

    ol_count = ol_result.get("merged_count", -1)
    if ol_count == 0:
        print("  ✅ ol 块全部独立（无合并）")
    elif ol_count > 0:
        print(f"  ⚠ {ol_count} 个 ol 块被合并（限制 5：飞书 ol 合并）")
        for i, issue in enumerate(ol_result.get("issues", []), 1):
            preview = " / ".join(issue.get("li_preview", []))[:80]
            print(f"    {i}. 含 {issue.get('li_count')} 个 li：{preview}...")
        print("    建议：手动拆分（见 SKILL.md 关键经验），或重跑 clean_xml 修复后的 03_post_process.py")
    else:
        print("  ❌ ol 分离核验失败")

    grid_missing = grid_result.get("missing", 0)
    if grid_result.get("src_img_grids", 0) == 0:
        pass
    elif grid_missing == 0:
        print("  ✅ 并排图 grid 全部还原")
    else:
        print(f"  ⚠ {grid_missing} 个并排图 grid 未还原（限制 16），请检查 rebuild_grids")

    cite_total = cite_result.get("total", 0)
    if cite_total == 0:
        pass
    elif cite_result.get("stale", 0) == 0:
        bs = cite_result.get("by_status", {})
        extra = ""
        if bs.get("no_permission") or bs.get("skill_failed") or bs.get("depth_exceeded"):
            extra = f"（其中 {bs.get('no_permission', 0)} 无权限 / " \
                    f"{bs.get('skill_failed', 0)} 扒取失败 / " \
                    f"{bs.get('depth_exceeded', 0)} 超深度，保留原链接）"
        print(f"  ✅ {cite_result.get('done', 0)}/{cite_total} 个被引用文档已复制并重指向{extra}")
    else:
        print(f"  ⚠ {cite_result['stale']} 个已复制引用仍残留旧链接，请重跑 process_cites.py")

    if image_result.get("mismatch_count", 0) > 0:
        print("\n建议：检查不匹配的图片，重新执行 03_post_process.py 的第 7 步")


if __name__ == "__main__":
    main()
