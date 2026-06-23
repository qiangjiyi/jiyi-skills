#!/usr/bin/env python3
"""
第 5.5 步：处理被引用的其它飞书文档（cite @文档 / 文档链接）

在 03_post_process 之后、04_verify 之前运行。流程：
1. 把当前主文档登记进共享 registry（防止子文档回引时无限递归）
2. 解析主文档里引用的其它飞书文档，逐个：
   - 探测阅读权限
   - 有权限 → 优先原生 `drive files copy` 创建副本
   - 原生复制失败 → 兜底递归 run_all.sh 完整扒取
3. 把主文档里所有命中的 cite/链接重指向到副本
4. 把 cite_mapping 写回 state.json 供 04_verify 与最终报告使用

无引用时本步骤也会运行（仅登记 self），开销极小。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    fetch_doc_xml,
    load_state,
    print_step,
    update_state,
    print_progress,
    run_lark_cli_json,
)
from cite_lib import (
    get_depth,
    get_registry_path,
    get_root_folder_token,
    process_cites_for_doc,
    dedup_orphan_copies,
    register,
    _inspect_token,
)


def _self_token(url: str) -> str:
    """主文档的 canonical docx token。wiki URL 先解包，保证子文档回引时能命中
    self 登记、避免把主文档当成新引用再复制一遍。"""
    m = re.search(r'/(docx|wiki|doc)/([A-Za-z0-9]+)', url or "")
    if not m:
        return url or ""
    kind, tok = m.group(1), m.group(2)
    if kind == "wiki":
        unwrapped = _inspect_token(url)
        if unwrapped:
            return unwrapped[0]
    return tok


def _doc_title(doc_id: str) -> str:
    """读取 docx 文档真实标题（self 登记用）。失败返回空串。"""
    r = run_lark_cli_json([
        "api", "GET", f"/open-apis/docx/v1/documents/{doc_id}",
    ], timeout=60)
    return ((r or {}).get("data", {}).get("document", {}) or {}).get("title", "") or ""


def main():
    state = load_state()
    new_doc_id = state.get("new_doc_id")
    if not new_doc_id:
        print("❌ 缺少 new_doc_id，请先运行 02/03")
        sys.exit(1)

    print_step("第 5.5 步：处理被引用文档（cite 递归）")
    print_progress(f"registry: {get_registry_path()}  depth={get_depth()}")

    # 1) 登记 self（防回引死循环）
    #
    # 关键：self 登记的 title 必须用文档**真实标题**，不能用通用占位「（主文档）」。
    # registry 是跨递归层共享、按 token 后写覆盖的。被引用文档 X 若走兜底递归扒取，
    # 其子进程会以 self 身份把 X 登记进同一 registry；若 title 写死「（主文档）」，
    # 就会把父进程本应给 X 记录的真实标题污染掉——父进程最终报告里这个引用就显示成
    # 「（主文档）→ X 副本链接」，既看不出真实标题、又像是把主文档指错（实测：
    # 主文档引用的「多账号管理工具。」被显示成「（主文档）」）。改为取真实标题即可。
    self_tok = _self_token(state.get("source_url", ""))
    if self_tok:
        register(self_tok, {
            "status": "done", "method": "self",
            "title": _doc_title(new_doc_id) or "（主文档）",
            "new_token": new_doc_id,
            "new_url": state.get("new_doc_url"),
            "raw_token": self_tok,
        })

    # 2) 目标文件夹：与主文档同目录平铺
    folder_token = state.get("target_dir_token") or ""
    if not folder_token:
        root = get_root_folder_token()
        if root:
            folder_token = root
        else:
            print_progress("⚠ 无法获取根目录 token，原生复制可能失败（将走兜底递归）")

    # 3) 处理主文档引用
    doc_xml = fetch_doc_xml(new_doc_id, detail="with-ids")
    if not doc_xml:
        print("❌ 无法读取新文档")
        sys.exit(1)

    mapping = process_cites_for_doc(new_doc_id, doc_xml, folder_token,
                                    get_depth(), Path(state.get("output_dir", ".")))

    # 4) 写回 state
    summary = [
        {
            "title": v.get("title"),
            "old_token": k,
            "status": v.get("status"),
            "method": v.get("method"),
            "new_url": v.get("new_url"),
        }
        for k, v in mapping.items()
    ]
    update_state(cite_mapping=summary)

    if not summary:
        print_step("完成", "主文档未引用其它飞书文档")
    else:
        done = sum(1 for s in summary if s["status"] == "done")
        print_step("完成", f"引用文档处理完毕：{done}/{len(summary)} 已复制并重指向")
        for s in summary:
            mark = {"done": "✓", "no_permission": "✗(无权限)",
                    "skill_failed": "✗(扒取失败)", "depth_exceeded": "⚠(超深度)"}.get(
                        s["status"], s["status"])
            print(f"  {mark} {s['title']} → {s.get('new_url') or '保留原链接'}")

    # 5) 收尾去重核验（仅顶层做一次）：清理同源多副本里没人引用的孤儿
    if get_depth() == 0:
        dup = dedup_orphan_copies(new_doc_id, delete=True)
        if dup:
            print_step("收尾去重核验", f"发现 {len(dup)} 组同源多副本")
            for g in dup:
                if g["deleted"]:
                    print_progress(f"  🧹 {g['title']}：保留 {g['keep'][:12]}，"
                                   f"清理孤儿 {len(g['deleted'])} 份 {[t[:12] for t in g['deleted']]}")
                elif g["orphans"]:
                    print_progress(f"  ⚠ {g['title']}：有孤儿 {[t[:12] for t in g['orphans']]} "
                                   f"未能删除，请手动检查")
                else:
                    print_progress(f"  ✓ {g['title']}：{len(g['copies'])} 份副本均被引用，保留")


if __name__ == "__main__":
    main()
