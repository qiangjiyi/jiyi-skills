#!/usr/bin/env python3
"""
cite 引用（@文档 / 飞书文档链接）递归处理库

负责把「主文档引用的其它飞书文档」也复制到当前登录用户的云空间，并把主文档里
指向原文档的 cite/链接重指向到副本。策略（用户确认）：

1. 检测引用文档的阅读权限（drive metas batch_query 探测）
2. 有权限 → **优先**用原生 `drive files copy` 创建副本（保真最高、最快）
3. 原生复制失败（跨租户 / 源文档禁止复制等）→ **兜底**递归调用 run_all.sh
   把该文档完整扒取重建
4. 全部完成后，把主文档里所有 cite 的 doc-id / 链接 URL 替换成副本的

跨多级递归（A→B→C）：每次 run_all.sh 内部都会再跑一遍本流程，天然递归。
靠**共享 registry**（环境变量 LARK_DOC_COPY_REGISTRY 指向的 JSON）去重防环，
靠 max-depth（LARK_DOC_COPY_MAX_DEPTH，默认 5）兜底防失控。

被复制文档与主文档同目录平铺（用户确认）。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from lib import (
    fetch_doc_xml,
    print_progress,
    run_lark_cli_json,
)

SCRIPT_DIR = Path(__file__).parent
DEFAULT_MAX_DEPTH = 5


# ===== 环境 / registry =====

def get_depth() -> int:
    try:
        return int(os.environ.get("LARK_DOC_COPY_DEPTH", "0"))
    except ValueError:
        return 0


def get_max_depth() -> int:
    try:
        return int(os.environ.get("LARK_DOC_COPY_MAX_DEPTH", str(DEFAULT_MAX_DEPTH)))
    except ValueError:
        return DEFAULT_MAX_DEPTH


def get_registry_path() -> Path:
    """共享 registry 路径。顶层未设置时落在当前 cwd，并写回环境供子进程继承。"""
    env = os.environ.get("LARK_DOC_COPY_REGISTRY")
    if env:
        return Path(env)
    p = (Path.cwd() / "cite_registry.json").resolve()
    os.environ["LARK_DOC_COPY_REGISTRY"] = str(p)
    return p


def load_registry() -> Dict[str, Dict]:
    p = get_registry_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_registry(reg: Dict[str, Dict]) -> None:
    p = get_registry_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def register(token: str, info: Dict) -> None:
    reg = load_registry()
    reg[token] = info
    save_registry(reg)


# ===== 副本台账（去重核验用） =====
#
# registry 按 canonical token 存储、后写覆盖先写，所以同一源文档被扒取多份时，
# registry 只留得下最后一份，看不到重复（实测：「小项目」被主文档 + 得物两处引用，
# 跨递归子进程去重没命中，多扒了一份孤儿副本）。台账是一个**只追加**的列表，
# 记录本次运行创建过的每一份副本，收尾核验据此发现「同源多副本」并清理零引用孤儿。
# 路径取 registry 的同目录兄弟文件，天然随 registry 的共享路径在递归各层间共享。

def get_copies_ledger_path() -> Path:
    return get_registry_path().with_name("cite_copies.json")


def load_copies_ledger() -> List[Dict]:
    p = get_copies_ledger_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def record_copy(canonical: str, new_token: str, title: str, method: str) -> None:
    """记录一份新建副本到台账（按 (canonical,new_token) 去重，幂等）。"""
    if not canonical or not new_token:
        return
    ledger = load_copies_ledger()
    if any(e.get("canonical") == canonical and e.get("new_token") == new_token
           for e in ledger):
        return
    ledger.append({
        "canonical": canonical, "new_token": new_token,
        "title": title, "method": method,
    })
    with open(get_copies_ledger_path(), "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


# ===== 引用提取 =====

FEISHU_DOC_URL_RE = re.compile(
    r'href="https://([^/"]+)/(docx|wiki|doc)/([A-Za-z0-9]+)[^"]*"'
)


def _parse_attrs(open_tag: str) -> Dict[str, str]:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', open_tag))


def extract_doc_references(xml: str, self_token: str) -> List[Dict]:
    """提取主文档里引用的「其它飞书文档」。

    覆盖两种形态：
    - `<cite type="doc" doc-id="TOKEN">`（@文档，不含 file-type 的纯文档引用）
    - `<a href="https://host/docx|wiki|doc/TOKEN">`（指向其它飞书文档的行内链接）

    返回去重后的引用列表：{raw_token, doc_type, host, url}。
    排除指向 self_token 的引用（自引用 / 目录锚点由 03 处理）。
    """
    refs: Dict[str, Dict] = {}

    # 1) cite @文档
    for m in re.finditer(r'<cite\b([^>]*)>', xml):
        attrs = _parse_attrs(m.group(1))
        if attrs.get("type") != "doc":
            continue
        ft = attrs.get("file-type")
        # 只排除真正的内嵌数据对象（sheet/bitable/mindnote 等），
        # docx/doc/wiki 都是合法的文档引用，必须处理。
        EMBED_TYPES = {"sheet", "sheets", "bitable", "mindnote", "file", "slides"}
        if ft in EMBED_TYPES:
            continue
        token = attrs.get("doc-id")
        if not token or token == self_token:
            continue
        dt = "wiki" if ft == "wiki" else "docx"
        refs.setdefault(token, {
            "raw_token": token,
            "doc_type": dt,
            "host": None,
            "url": None,
        })

    # 2) 指向其它飞书文档的链接
    for host, kind, token in FEISHU_DOC_URL_RE.findall(xml):
        if token == self_token:
            continue
        dt = "docx" if kind == "docx" else ("wiki" if kind == "wiki" else "doc")
        refs.setdefault(token, {
            "raw_token": token,
            "doc_type": dt,
            "host": host,
            "url": f"https://{host}/{kind}/{token}",
        })

    return list(refs.values())


# ===== 权限探测 / 解析 =====

def probe_permission(token: str, doc_type: str) -> Optional[Dict]:
    """用 drive metas batch_query 探测阅读权限。

    返回 {token, doc_type, title, url}（可读）或 None（无权限 / 不存在）。
    会自动把 wiki 解包到底层 docx token。
    """
    # wiki 先解包到 docx
    real_token, real_type = token, doc_type
    if doc_type == "wiki":
        unwrapped = _inspect_token(token)
        if unwrapped:
            real_token, real_type = unwrapped

    data = json.dumps({
        "request_docs": [{"doc_token": real_token, "doc_type": real_type}],
        "with_url": True,
    }, ensure_ascii=False)

    result = run_lark_cli_json([
        "drive", "metas", "batch_query",
        "--as", "user",
        "--data", data,
    ], timeout=60)

    if not result:
        return None
    body = result.get("data", result)
    metas = body.get("metas") or []
    if not metas:
        return None
    meta = metas[0]
    return {
        "token": meta.get("doc_token", real_token),
        "doc_type": meta.get("doc_type", real_type),
        "title": meta.get("title") or "未命名文档",
        "url": meta.get("url"),
    }


def _inspect_token(token_or_url: str) -> Optional[Tuple[str, str]]:
    """用 drive +inspect 解包 wiki / 规范化 token，返回 (token, type)。"""
    args = ["drive", "+inspect", "--as", "user"]
    if token_or_url.startswith("http"):
        args += ["--url", token_or_url]
    else:
        args += ["--url", token_or_url, "--type", "wiki"]
    result = run_lark_cli_json(args, timeout=60)
    if not result:
        return None
    data = result.get("data", result)
    tok = data.get("token") or data.get("obj_token")
    typ = data.get("type") or data.get("obj_type") or "docx"
    if tok:
        return tok, typ
    return None


# ===== 原生复制 =====

def try_native_copy(token: str, doc_type: str, folder_token: str, name: str) -> Optional[Dict]:
    """尝试 drive files copy 创建副本。成功返回 {token, url, name}，失败返回 None。

    跨租户 / 源文档禁止复制时会失败——交给调用方走兜底递归。
    """
    name = name[:200]  # 接口限制 256 字节，留余量
    params = json.dumps({"file_token": token}, ensure_ascii=False)
    data = json.dumps({
        "folder_token": folder_token,
        "name": name,
        "type": doc_type,
    }, ensure_ascii=False)

    result = run_lark_cli_json([
        "drive", "files", "copy",
        "--as", "user",
        "--params", params,
        "--data", data,
    ], timeout=120)

    if not result:
        return None
    body = result.get("data", result)
    f = body.get("file") or {}
    new_token = f.get("token")
    if not new_token:
        return None
    return {"token": new_token, "url": f.get("url"), "name": f.get("name", name)}


def get_root_folder_token() -> Optional[str]:
    result = run_lark_cli_json([
        "api", "GET", "/open-apis/drive/explorer/v2/root_folder/meta", "--as", "user",
    ], timeout=60)
    if not result:
        return None
    return (result.get("data") or {}).get("token")


# ===== 兜底：递归调用 run_all.sh =====

def recursive_skill_copy(child_url: str, folder_token: str, depth: int,
                         work_root: Path, tag: str) -> Optional[Dict]:
    """在独立子目录里递归跑完整 pipeline。返回 registry 里该文档的条目或 None。"""
    work_dir = work_root / f"_cite_{tag}"
    work_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["LARK_DOC_COPY_DEPTH"] = str(depth + 1)
    env["LARK_DOC_COPY_MAX_DEPTH"] = str(get_max_depth())
    env["LARK_DOC_COPY_REGISTRY"] = str(get_registry_path())
    env["LARK_DOC_COPY_SKIP_PREFLIGHT"] = "1"

    cmd = ["bash", str(SCRIPT_DIR / "run_all.sh"), child_url, folder_token or ""]
    print_progress(f"    ↳ 兜底递归扒取（depth={depth + 1}）：{child_url}")
    try:
        subprocess.run(cmd, cwd=str(work_dir), env=env, timeout=1800)
    except (subprocess.SubprocessError, OSError) as e:
        print_progress(f"    ⚠ 递归扒取异常：{e}")
        return None
    return load_registry()  # 调用方按 canonical token 取


# ===== cite / 链接重指向 =====

BLOCK_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "callout", "blockquote")


def apply_cite_mapping(doc_id: str, mapping: Dict[str, Dict]) -> int:
    """把 doc_id 文档里命中的 old_token 重指向到副本。返回成功替换的 block 数。

    mapping: {old_token: {new_token, new_url, status:'done', ...}}，只处理 done 项。
    """
    done = {k: v for k, v in mapping.items() if v.get("status") == "done"}
    if not done:
        return 0

    new_xml = fetch_doc_xml(doc_id, detail="with-ids")
    if not new_xml:
        return 0

    replaced = 0
    for tag in BLOCK_TAGS:
        pattern = re.compile(
            rf'<{tag}\b([^>]*?\bid="([^"]+)"[^>]*?)>(.*?)</{tag}>',
            re.DOTALL,
        )
        for m in pattern.finditer(new_xml):
            open_attrs = m.group(1)
            bid = m.group(2)
            body = m.group(3)

            if not any(old in (open_attrs + body) for old in done):
                continue

            new_open = re.sub(r'\s+id="[^"]+"', '', open_attrs)
            new_body = body
            for old, info in done.items():
                new_tok = info["new_token"]
                new_url = info.get("new_url")
                # cite @文档：doc-id 直接换 token
                new_body = new_body.replace(f'doc-id="{old}"', f'doc-id="{new_tok}"')
                # 行内链接：整条 href 换成副本 URL（host 也跟着换）
                if new_url:
                    new_body = re.sub(
                        rf'href="https://[^"]*/(?:docx|wiki|doc)/{re.escape(old)}[^"]*"',
                        f'href="{new_url}"',
                        new_body,
                    )

            if new_body == body:
                continue

            new_content = f'<{tag}{new_open}>{new_body}</{tag}>'
            res = run_lark_cli_json([
                "docs", "+update", "--api-version", "v2",
                "--doc", doc_id,
                "--command", "block_replace",
                "--block-id", bid,
                "--content", new_content,
            ], timeout=60)
            if res and res.get("ok"):
                replaced += 1
            else:
                print_progress(f"    ⚠ 重指向 block {bid} 失败")

    return replaced


# ===== 主递归：处理某个文档的所有引用 =====

def process_cites_for_doc(doc_id: str, doc_xml: str, folder_token: str,
                          depth: int, work_root: Path) -> Dict[str, Dict]:
    """解析 doc 的引用 → 逐个复制/递归 → 重指向。

    返回 mapping，**按 raw token（文档里实际出现的 token）** 索引——因为重指向要在
    文档 XML 里按出现形态匹配（cite 用 docx token、wiki 链接用 wiki token），而 registry
    去重仍按 canonical docx token。
    """
    refs = extract_doc_references(doc_xml, self_token=doc_id)
    if not refs:
        return {}

    print_progress(f"  发现 {len(refs)} 个被引用文档（depth={depth}）")
    max_depth = get_max_depth()
    mapping: Dict[str, Dict] = {}

    for ref in refs:
        raw = ref["raw_token"]

        # 解析 canonical（wiki 解包）
        canonical, dtype = raw, ref["doc_type"]
        if dtype == "wiki":
            unwrapped = _inspect_token(ref.get("url") or raw)
            if unwrapped:
                canonical, dtype = unwrapped

        reg = load_registry()
        if canonical in reg:  # 去重 / 防环：已处理过，直接复用
            mapping[raw] = reg[canonical]
            print_progress(f"  ↺ 复用已处理：{reg[canonical].get('title', canonical)}")
            continue

        # 1) 权限探测
        meta = probe_permission(canonical, dtype)
        if not meta:
            info = {"status": "no_permission", "title": raw, "raw_token": raw}
            register(canonical, info)
            mapping[raw] = info
            print_progress(f"  ✗ 无阅读权限，保留原链接：{raw}")
            continue

        canonical = meta["token"]
        dtype = meta["doc_type"]
        title = meta["title"]
        src_url = meta.get("url") or ref.get("url") or canonical

        # 已到深度上限，不再下钻
        if depth + 1 > max_depth:
            info = {"status": "depth_exceeded", "title": title, "raw_token": canonical}
            register(canonical, info)
            mapping[raw] = info
            print_progress(f"  ⚠ 达到递归深度上限，保留原链接：{title}")
            continue

        # 2) 优先原生复制
        copied = try_native_copy(canonical, dtype, folder_token, title)
        if copied:
            info = {
                "status": "done", "method": "copy", "title": title,
                "new_token": copied["token"], "new_url": copied["url"],
                "raw_token": canonical,
            }
            register(canonical, info)
            record_copy(canonical, copied["token"], title, "copy")
            mapping[raw] = info
            print_progress(f"  ✓ 原生复制副本：{title}")
            # 副本内部的 cite 仍指向原文档，递归处理副本
            child_xml = fetch_doc_xml(copied["token"], detail="with-ids")
            if child_xml:
                process_cites_for_doc(copied["token"], child_xml, folder_token,
                                      depth + 1, work_root)
            continue

        # 3) 兜底：递归扒取
        print_progress(f"  ⤷ 原生复制失败，转兜底递归：{title}")
        recursive_skill_copy(src_url, folder_token, depth, work_root, canonical[:16])
        reg = load_registry()
        info = reg.get(canonical)
        if info and info.get("status") == "done":
            info["method"] = "skill"
            register(canonical, info)
            record_copy(canonical, info.get("new_token"), title, "skill")
            mapping[raw] = info
            print_progress(f"  ✓ 递归扒取副本：{title}")
        else:
            info = {"status": "skill_failed", "title": title, "raw_token": canonical}
            register(canonical, info)
            mapping[raw] = info
            print_progress(f"  ✗ 递归扒取失败，保留原链接：{title}")

    # 4) 重指向当前文档
    n = apply_cite_mapping(doc_id, mapping)
    if n:
        print_progress(f"  已重指向 {n} 个 block 的引用")
    return mapping


# ===== 收尾去重核验 =====

def delete_drive_docx(token: str) -> bool:
    """把副本移到废纸篓（可恢复）。"""
    res = run_lark_cli_json([
        "drive", "+delete", "--as", "user",
        "--file-token", token, "--type", "docx", "--yes",
    ], timeout=60)
    return bool(res and (res.get("ok") or (res.get("data") or {}).get("deleted")))


def dedup_orphan_copies(main_doc_id: str, delete: bool = True) -> List[Dict]:
    """收尾去重核验：发现「同源多副本」，清理零引用的孤儿副本。

    背景：同一源文档被多处引用时，跨递归子进程的 registry 去重可能漏命中，导致
    同源被扒取多份；最终重指向只收敛到其中一份，其余成为没人引用的孤儿（实测：
    「小项目」多出一份 WIrr）。本函数据 cite_copies.json 台账按源分组，对每组：
    - 统计每个副本被「其它文档」引用的次数（排除自引用）
    - 保留 registry 里 canonical 的最终目标（主文档实际指向的那份）
    - 其余「零其它引用」的副本判为孤儿 → 移到废纸篓（delete=True 时）

    只动本次运行台账里记录的副本，绝不碰用户其它文档。返回每组的核验报告。
    """
    ledger = load_copies_ledger()
    groups: Dict[str, Dict[str, str]] = {}
    for e in ledger:
        groups.setdefault(e["canonical"], {})[e["new_token"]] = e.get("title", "")
    dup_groups = {c: toks for c, toks in groups.items() if len(toks) > 1}
    if not dup_groups:
        return []

    registry = load_registry()
    # 扫描范围：主文档 + 台账里所有副本（封闭集合，不外溢到无关文档）
    all_docs = {main_doc_id} | {e["new_token"] for e in ledger}
    doc_xml = {d: (fetch_doc_xml(d, detail="with-ids") or "") for d in all_docs}

    results = []
    for canonical, toks in dup_groups.items():
        refcount = {
            t: sum(1 for d, xml in doc_xml.items() if d != t and t in xml)
            for t in toks
        }
        keep = (registry.get(canonical) or {}).get("new_token")
        if not keep or keep not in toks:
            keep = max(toks, key=lambda t: refcount[t])
        orphans = [t for t in toks if t != keep and refcount[t] == 0]
        deleted = [o for o in orphans if delete and delete_drive_docx(o)]
        results.append({
            "title": toks.get(keep) or next(iter(toks.values())),
            "canonical": canonical,
            "copies": list(toks),
            "keep": keep,
            "orphans": orphans,
            "deleted": deleted,
        })
    return results
