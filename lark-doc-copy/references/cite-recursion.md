# 多层级 cite 引用递归处理

源文档里引用的**其它飞书文档**（`<cite type="doc" doc-id="...">` @文档，或指向 `/docx/`、
`/wiki/` 的行内链接）会被自动一并复制到当前用户云空间，并把主文档里的引用重指向到副本。
由 `scripts/process_cites.py` + `scripts/cite_lib.py` 实现，作为第 8.6 步在 `03_post_process`
之后自动运行；`run_all.sh` 已接线，无需手动调用。

## 处理策略（每个被引用文档）

1. **提取引用**：`extract_doc_references` 解析 `<cite type="doc" doc-id>`（排除
   `file-type="sheets|bitable"` 的内嵌表格）和指向飞书文档的 `<a href>` 链接，按 token 去重，
   排除自引用。
2. **探测权限**：`drive metas batch_query` 探测阅读权限（wiki 先用 `drive +inspect` 解包到
   docx token）。无权限 → 标注 `no_permission`，保留原链接。
3. **优先原生复制**：有权限时先尝试 `drive files copy` 创建副本（保真最高、最快），落在**与主
   文档同一目录**。
4. **兜底递归扒取**：原生复制失败（典型：跨租户、源文档禁止复制）→ 在独立子目录里递归调用
   `run_all.sh` 完整扒取重建（`recursive_skill_copy`）。
5. **重指向**：`apply_cite_mapping` 用 `block_replace` 把主文档里命中的 cite `doc-id` 和链接 URL
   换成副本的（旧 `str_replace` 改属性的写法不生效，已弃用）。

## 多级递归 + 去重防环

- **嵌套一层层往下扒**：A 引用 B、B 引用 C → B、C 都会被复制。兜底递归路径天然递归（子
  `run_all.sh` 内部会再跑一遍本步骤）；原生复制路径会对副本再跑一次本步骤（副本内部 cite 仍指
  向原文档，需继续处理）。
- **共享 registry 去重防环**：环境变量 `LARK_DOC_COPY_REGISTRY` 指向的 JSON 在所有递归层间共
  享，按 canonical docx token 记录每个文档的处理结果；已处理过的直接复用，A→B→A 这类回环不会
  无限递归（主文档启动时即把 self 登记进 registry）。
- **深度上限**：`LARK_DOC_COPY_MAX_DEPTH`（默认 5）兜底防失控，超深度的引用保留原链接并标注
  `depth_exceeded`。
- **递归子调用约定**：`run_all.sh <url> <folder-token>` 第 2 参数让被引用文档落到同目录；子调用
  设 `LARK_DOC_COPY_SKIP_PREFLIGHT=1` 跳过重复自检。临时工作目录 `_cite_*`、registry 与副本台账
  `cite_copies.json` 由顶层 `05_cleanup.py` 统一清理。

## 收尾去重核验（防同源多副本）

同一源文档被多处引用时，跨递归子进程的 registry 去重可能漏命中，导致同源被扒取多份、最终只有一
份被引用、其余成孤儿（实测：「小项目」多出一份）。`cite_lib.record_copy` 把每份新建副本追加进
**只追加**的台账 `cite_copies.json`（registry 同目录、随其共享路径在递归各层间共享）；顶层
`process_cites.py` 跑完调 `dedup_orphan_copies`：按源分组，统计每份副本被「其它文档」引用的次数
（排除自引用），保留 registry 的最终目标，把「零其它引用」的孤儿移到废纸篓（可恢复）。只动台账
里本次新建的副本，绝不碰用户其它文档。

## self 登记必须用真实标题（2026-06-23 实测）

`process_cites.py` 启动时把当前文档以 self 身份登记进**共享 registry**（防回引死循环）。共享
registry 跨递归层、按 token 后写覆盖——若 self 的 title 写死通用占位「（主文档）」，当某个被引用
文档 X 走兜底递归扒取时，X 的子进程会以 self 身份把 X 也登记成「（主文档）」，**覆盖**父进程本应
给 X 记的真实标题。结果父进程最终报告里这个引用显示成「（主文档）→ X 副本链接」，既看不出真实
标题、又像是把主文档指错（实测：主文档引用的同名文档「多账号管理工具。」被显示成「（主文档）」，
让人误以为主文档副本是它）。

**修复**：self 登记用 `_doc_title(new_doc_id)` 取真实标题；`run_all.sh` 顶层（`LARK_DOC_COPY_DEPTH=0`）
在清理前从 `state.json` 读 `new_doc_url` 显式打印「📄 主文档副本」横幅（state.json 会被第 5 步清理，
故必须在清理前打印），避免主文档链接被 cite 列表淹没/混淆。

## 核验与报告

`04_verify.py` 的 `verify_cites` 统计各状态数量，并检测「已复制（done）的引用是否仍残留旧 token」
——残留说明重指向未完成，需重跑 `process_cites.py`。最终报告的【引用的相关文档】列出每个引用的
标题、状态（已复制 / 无权限 / 扒取失败 / 超深度）和副本链接。收尾去重核验另会报告「同源多副本」
组：保留的那份、清理掉的孤儿。
