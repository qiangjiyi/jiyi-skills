---
name: lark-doc-copy
description: 完整复制飞书云文档（包括文字、样式、换行、空行、图片、有序列表、cite 引用等所有组件）到当前 Lark CLI 登录用户自己的飞书账号。当用户提出"复制这个飞书文档到我的账号"、"备份这个文档"、"把这份资料转存到我自己的飞书"、"一键克隆飞书文档"、"把这个文档下载下来再上传到我的云空间"、"我想在我的飞书里也有一份相同的文档"等任何涉及将参考飞书文档内容迁移到当前登录用户飞书云空间的需求时，必须使用本 skill。哪怕用户没有明确说"复制飞书文档"，只要意图是把一个公开/可访问的飞书文档搬运到自己的飞书账号，都应触发本 skill。
---

# 飞书文档复制 skill

把一个参考飞书云文档（`/docx/<token>` 或 `/wiki/<token>`）的**完整内容**——文字、样式、
换行、空行、图片、有序/无序列表、引用块、callout、表格、画板、封面、目录组件、cite 文档
引用等——复制到当前 Lark CLI 登录用户自己的云空间，达到视觉与内容**高度一致**。

**前置**：用户已通过 `lark-cli auth login` 完成 user 身份认证（`scripts/preflight.sh` 会自检）。

## 输出

执行完成后必须按【最终输出格式】（见下）汇报：复制结果总结、与原文档的已知差异、
**主文档新链接**、放置目录、引用的相关文档（如有）、临时文件清理确认。

---

## 快速开始（一键执行）

绝大多数情况直接跑一键脚本，它会按顺序自动完成所有步骤：

```bash
bash scripts/run_all.sh "https://xxx.feishu.cn/docx/<token>"
```

| 步骤 | 脚本 | 说明 |
|---|---|---|
| 0 | `scripts/preflight.sh` | 环境自检（Lark CLI + 依赖 skills + 登录用户） |
| **0.5** | **`scripts/00_try_native.py`** | **优先原生 `drive files copy` 复制主文档**；成功则跳过 1/2/3 |
| 1+2 | `scripts/01_fetch_source.py` | （兜底）读取源文档 + 下载图片 |
| 3+4 | `scripts/02_create_doc.py` | （兜底）创建新文档（默认根目录，可 `--target-dir-token` 指定） |
| 5-9 | `scripts/03_post_process.py` | （兜底）后处理：映射、锚点、图片定位/尺寸/对齐、grid、callout、空 p、画板、内嵌表、封面、ISV 目录、seq、blockquote |
| 9.5 | `scripts/process_cites.py` | 处理被引用的其它飞书文档（复制 + 递归 + 重指向） |
| 10+11 | `scripts/04_verify.py` | 内容/图片位置/grid/cite 等核验（原生复制模式下只核验 cite） |
| 12 | `scripts/05_cleanup.py` | 清理临时文件 |

> **主文档复制策略：能原生复制就原生复制。** `run_all.sh` 先用 `00_try_native.py` 尝试
> `drive files copy`——成功（含跨租户成功）则直接用副本、**跳过整套扒取重建（01/02/03）**，
> 因为原生副本由飞书端到端复制、结构与源逐字节一致，**天然没有 seq/grid/图片定位等后处理
> bug**。只有原生复制失败（跨租户禁复制 / 无权限 / 接口报错）才退回扒取重建。无论哪条路径，
> 之后都跑 cite 重指向 + 核验 + 清理。

**单独运行某步**（调试或重跑某步）：按上表脚本顺序逐个 `python3 scripts/0X_*.py` 即可；
`00_try_native.py` / `01_fetch_source.py` 需要 `--source "<url>"`，其余从 `state.json` 读状态。

**共享库 `scripts/lib.py`**：lark-cli 调用包装（`run_lark_cli` / `run_lark_cli_json`）、文档读取
（`fetch_doc_xml`）、图片处理（`download_image` / `upload_image`）、XML 解析（`xml_to_blocks`）、
状态管理（`load_state` / `update_state`）。
**状态文件 `state.json`（cwd 下）**：脚本间中间状态——`source_url`、`source_xml_path`、
`img_tokens`、`new_doc_id`、`new_doc_url`、`id_mapping`、`uploaded_images` 等。

---

## 工作流（扒取重建路径，理解 + 手动执行用）

原生复制成功时下面这些后处理全部跳过。只有走兜底扒取重建时才逐步执行（均已在
`03_post_process.py` 自动化，顺序有依赖，不要随意调换）：

1. 读源文档 XML + 下载图片 → 清理 XML + create 新文档 → 构建 source→new block ID 映射
2. 更新目录锚点；上传图片到文末
3. **图片定位**（最易错，详见 `references/image-positioning.md`）：
   `compute_image_anchors` 按图前驱分 direct/two_step/fallback 三模式 → `move_images`；
   折叠标题内嵌套图由 `move_nested_images` 补救
4. 图片尺寸 `fix_image_sizes`（scale/宽高）→ 对齐 `fix_image_align`（左/右，须在尺寸之后）
5. 还原并排图 grid `rebuild_grids` → 修 callout 边界 `fix_callout_imgs` → 校准图前后空 p
   `normalize_image_empty_p_around`
6. 迁移画板 `migrate_whiteboards` → 内嵌表 `migrate_sheets` → 封面 `migrate_cover`
   → ISV 目录组件 `migrate_addons`
7. 修有序列表 seq `fix_list_seq`（详见 `references/list-and-nesting.md`）→ 合并连续
   blockquote + 去引用块灰底
8. cite 递归（见下「cite 引用递归」）→ 核验 `04_verify.py` → 清理

---

## 已知限制

**A. 真正无法消除的差异**（会出现在最终报告的【已知差异】里）

1. **灰色背景色（仅引用块外）**：源 `rgb(229,230,233)` 被飞书归一化为 `rgb(242,243,245)`。
   标题/callout/行内 code 等位置保留此差异；引用块内的灰底已由 `strip_blockquote_bg` 自动去除。
2. **callout 边框**：飞书自动添加 `border-color` 属性。

**B. 飞书 API 行为坑（脚本已自动处理；出问题时按"详见"读细节手动补救）**

| 限制 | 处理位置 | 详见 |
|---|---|---|
| create 强制 `li seq="1"` + 跨 ol 自动递增失效；blockquote 内列表污染顶级编号 | `03` `fix_list_seq`（上下文混合计数器，blockquote 独立编号作用域） | api-limitations 13 / list-and-nesting |
| 跨租户图片无法 `media-download`（403） | `lib.download_image` 走 `media-preview` | api-limitations 4 |
| `OL + img + OL` 被合并；`ol + img + p + ol` 段落被错插进列表 | `02` `clean_xml` 插空 `<p>` 占位符预防；`04` `verify_ol_separation` | api-limitations 5 / 5.2 |
| 连续 blockquote 渲染成独立盒子；引用块灰底归一化成明显灰盒 | `03` `merge_consecutive_blockquotes` + `strip_blockquote_bg` | api-limitations 11 / 1 |
| ol/ul 无 block ID、block_replace 后 ID 变化、可能产生重复 li | 全程文本匹配 + 每步重新 fetch；`04` `verify_duplicate_li` | api-limitations 6 / 7 / 12 |
| 图片 scale/宽高不保留；对齐（左/右）不保留 | `03` `fix_image_sizes` + `fix_image_align`（须在尺寸之后） | api-limitations 15 |
| 连续堆叠图第二张漂走；图紧跟顶级标题后无 anchor 漂文末 | `03` `compute_image_anchors`（`is_anchorable_top` 跳过 img；`MAPPABLE_TOP` 含 h1–h9） | api-limitations 17 / 19 |
| 折叠标题/段落内嵌套图对 `xml_to_blocks` 不可见 → 堆文末 | `03` `move_nested_images`（ElementTree 全量扁平化重定位） | image-positioning |
| `move_nested_images` 不继承 blank_gap → 图前/后空 p 互换 | `03` `normalize_image_empty_p_around`；`04` `verify_image_blank_p` | api-limitations 5.3 |
| grid 并排图变竖排，含每列多图的 2×2/3×2 图墙 | `03` `rebuild_grids`（按列把多图依次移入）+ 还原列宽；`04` `verify_grids` | api-limitations 16 |
| callout 边界 parse 错误 → 外层图被吸进 callout | `03` `fix_callout_imgs`（API 把 img 移出 callout 后补空 p）；`04` `verify_callout` | api-limitations 16 |
| 画板被 create 静默丢弃 | `02` `clean_xml` 剥离；`03` `migrate_whiteboards`（raw 保布局）；`04` `verify_whiteboards` | — |
| 同步块 / 内嵌表格被 create 静默丢弃 | `02` 解包同步块/剥离 sheet；`03` `migrate_sheets`（渲染原生 table）；`04` `verify_embedded` | — |
| 文档封面（顶部背景图，文档级属性）不复制 | `03` `migrate_cover`（media-preview 下载 → `resource-update --type cover`）；`04` `verify_cover_and_addons` | api-limitations 20 |
| ISV 组件块（目录 add_ons / block_type 40）被静默丢弃 | `03` `migrate_addons`（原生 blocks API 读 component_type_id/record → children-create 重建，逐块带各自 record） | api-limitations 21 |
| 图片下载/上传瞬时失败静默漏图 | `lib.download_image` + `03` `upload_images` 重试；`04` `verify_images` 兜底数量 | api-limitations 18 |
| cite 重指向 / 跨租户禁复制 / 同源多副本孤儿 / self 登记标题污染 | `process_cites.py` + `cite_lib.py` | cite-recursion |

---

## 手动补救通用原则（脚本失败、需要手工调块时必读）

手工调块顺序时，飞书 API 有几个反复踩到的坑，跨具体修复通用：

- **`block_move_after` 落点不可靠**，三种已知形态会让 src 落到非预期位置：①anchor 后紧跟空 p；
  ②src 本身是空 p；③**src 当前在 anchor 之前**（回挪时越位一格）。**每次调用后必须重新 fetch
  核验实际落点**，不能假设"紧贴 anchor"。详见 `references/api-limitations.md` 限制 5.3 / 19.1。
- **能用 children-create API 就别用 block_move_after**：`POST .../blocks/{parent}/children` 带显式
  `index`，落点确定（重建目录组件、补空行都用这个）。空段落不能用 `block_type 2 + 空 elements`
  （报 `invalid param`）；空行改用 `docs +update block_insert_after --content '<p></p>' --doc-format xml`。
- **调空行用「删旧 + 插新」而非移动空 p**；锚点尽量选非容器块（grid/column 等容器作 anchor 易越位）。
- **`xml.etree.Element` 没有 `__bool__` 重载**：所有 Element 都视为 falsy，`if el:` 永远 False。
  处理飞书 XML 一律用 `if el is not None`。

---

## cite 引用递归

源文档里引用的其它飞书文档（`<cite type="doc">` @文档 / 指向 `/docx`、`/wiki` 的行内链接）会被
`process_cites.py` 自动一并复制到同目录，并把主文档里的引用重指向到副本：**探测权限 → 优先
原生复制 → 失败兜底递归扒取 → `block_replace` 重指向**。共享 registry + 深度上限防环、收尾去重
清理同源孤儿。完整策略、递归约定、self 登记真实标题等坑，见 `references/cite-recursion.md`。

---

## 最终输出格式

skill 执行完成后，必须按以下格式输出（中文）：

```
========================================
飞书文档复制完成
========================================

源文档：<源文档标题>
新文档：<主文档新链接>
放置目录：<目录名称>

【复制结果】
- 文字内容：✅ 100% 一致（共 X 个文本块）
- 图片：✅ X 张全部复制
- 锚点链接：✅ X 个全部更新
- 有序列表序号：✅ X 个 li 全部修复
- 引用样式标签：✅ 完全一致（b: X, em: X, ...）

【已知差异】（无法修复）
1. 灰色背景色：源 rgb(229,230,233) → 新 rgb(242,243,245)
2. callout 边框：飞书自动添加 border-color 属性

【引用的相关文档】（如有）
- <子文档 1 标题>（已复制：原生/递归） → <副本链接>
- <子文档 2 标题>（无权限，保留原链接） → <原链接>

【临时文件清理】
✅ 已清理所有临时文件、图片、脚本

【用户 review 项】
- 请打开新文档，对照源文档检查整体视觉效果
- 重点关注：图片是否在正确位置、列表是否正常递增、引用样式是否正确
========================================
```

> ⚠ 主文档链接坑：`run_all.sh` 顶层在清理前会显式打印「📄 主文档副本」横幅——**汇报时用这个
> 链接，不要从 cite 列表里挑**（同名引用文档易被误当主文档，见 cite-recursion）。

---

## 用户偏好

- **中文交流**：所有输出使用中文。
- **简洁务实**：不做冗长解释，只给关键信息。
- **完成后输出变动详情**：便于用户 review。
- **容忍样式细微差异**：但文字内容必须 100% 一致。

---

## 脚本清单

| 脚本 | 功能 |
|---|---|
| `scripts/preflight.sh` | 环境自检（必须先执行） |
| `scripts/00_try_native.py` | 优先原生复制主文档（`drive files copy`）；成功则跳过 01/02/03 |
| `scripts/01_fetch_source.py` | （兜底）读取源文档 + 下载图片 |
| `scripts/02_create_doc.py` | （兜底）创建新文档 |
| `scripts/03_post_process.py` | （兜底）后处理：映射/锚点/图片/grid/callout/空 p/画板/内嵌表/封面/ISV 目录/seq/blockquote |
| `scripts/process_cites.py` | 处理被引用文档（复制 + 递归 + 重指向） |
| `scripts/04_verify.py` | 内容/图片位置/重复 li/grid/封面+ISV/cite 等核验 |
| `scripts/05_cleanup.py` | 清理临时文件（含 registry 与 `_cite_*` 工作目录） |
| `scripts/run_all.sh` | 一键入口（`<url> [folder-token]`，按序调用以上全部） |
| `scripts/lib.py` | 共享工具库 |
| `scripts/cite_lib.py` | cite 递归处理库（registry / 提取 / 探测 / 复制 / 递归 / 重指向） |

---

## 详细参考文档

| 文档 | 内容 |
|---|---|
| `references/api-limitations.md` | 飞书 API 已知限制与处理（限制 1–21，按编号查阅；手动补救细节都在这里） |
| `references/image-positioning.md` | 图片定位三场景、签名核验、常见问题排查 |
| `references/list-and-nesting.md` | 有序列表 seq 修复、嵌套结构恢复 |
| `references/cite-recursion.md` | cite 多层级递归复制：策略、防环、去重、self 登记真实标题 |
