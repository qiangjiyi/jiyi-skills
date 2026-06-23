---
name: lark-doc-copy
description: 完整复制飞书云文档（包括文字、样式、换行、空行、图片、有序列表、cite 引用等所有组件）到当前 Lark CLI 登录用户自己的飞书账号。当用户提出"复制这个飞书文档到我的账号"、"备份这个文档"、"把这份资料转存到我自己的飞书"、"一键克隆飞书文档"、"把这个文档下载下来再上传到我的云空间"、"我想在我的飞书里也有一份相同的文档"等任何涉及将参考飞书文档内容迁移到当前登录用户飞书云空间的需求时，必须使用本 skill。哪怕用户没有明确说"复制飞书文档"，只要意图是把一个公开/可访问的飞书文档搬运到自己的飞书账号，都应触发本 skill。
---

# 飞书文档复制 skill

## Skill 目标

给定一个参考飞书云文档的 URL，将该文档的**完整内容**（文字、样式、换行、空行、图片、有序/无序列表、引用块、callout 高亮框、表格、链接、cite 文档引用等所有组件）写入到当前 Lark CLI 登录用户自己的飞书云文档中，达到视觉与内容**高度一致**的效果。

## 输入

- 参考飞书文档 URL（支持 `/docx/<token>` 和 `/wiki/<token>` 两种形式）
- 用户当前已通过 `lark-cli auth login` 完成 user 身份认证

## 输出

执行完成后必须输出：

1. **复制完成情况总结**：文字内容、图片数量、列表序号、锚点链接等关键维度对比
2. **与原文档的差异说明**：已知限制和本次执行中无法修复的问题
3. **新飞书文档链接**
4. **放置目录说明**
5. **引用的相关文档**（如有）
6. **清理确认**

---

## 快速开始（一键执行）

skill 提供了完整的一键执行脚本，可以按顺序自动运行所有步骤：

```bash
bash scripts/run_all.sh "https://xxx.feishu.cn/docx/<token>"
```

**执行顺序**：

| 步骤 | 脚本 | 说明 |
|---|---|---|
| 0 | `scripts/preflight.sh` | 环境自检（Lark CLI + 依赖 skills + 登录用户） |
| **0.5** | **`scripts/00_try_native.py`** | **优先尝试原生 `drive files copy` 复制主文档**（保真最高、最快、无扒取 bug）；成功则 `run_all.sh` 跳过 1/2/3，失败退回扒取重建 |
| 1+2 | `scripts/01_fetch_source.py` | （兜底）读取源文档 + 下载图片 |
| 3+4 | `scripts/02_create_doc.py` | （兜底）创建新文档（默认根目录，可 `--target-dir-token` 指定） |
| 5-9 | `scripts/03_post_process.py` | （兜底）后处理（映射、目录锚点、图片、嵌套图、对齐、还原 grid、校准空 p、迁移画板、迁移内嵌表格、seq、合并 blockquote、去引用块灰底） |
| 9.5 | `scripts/process_cites.py` | 处理被引用的其它飞书文档（探测权限 → 优先原生复制 → 兜底递归扒取 → 重指向 cite/链接） |
| 10+11 | `scripts/04_verify.py` | 内容核验 + 图片位置核验 + grid 还原核验 + cite 引用核验（原生复制模式下只核验 cite） |
| 12 | `scripts/05_cleanup.py` | 清理临时文件 |

> **主文档复制策略（2026-06-19 起）**：与被引用文档（cite）一致——**能原生复制就原生复制**。
> `run_all.sh` 先跑 `00_try_native.py` 尝试 `drive files copy`，成功（含跨租户成功）则直接用
> 副本、跳过整套扒取重建（01/02/03），因为原生副本由飞书端到端复制、结构与源逐字节一致，
> **天然没有 seq/grid/图片定位等后处理 bug**。只有原生复制失败（跨租户禁复制 / 无权限 / 接口
> 报错）才退回扒取重建。无论哪条路径，之后都跑 cite 重指向 + 核验 + 清理。

**也可以单独运行某个步骤**（用于调试或重新执行某个步骤）：

```bash
bash scripts/preflight.sh
python3 scripts/00_try_native.py --source "https://xxx.feishu.cn/docx/<token>"  # 成功则跳过 01/02/03
python3 scripts/01_fetch_source.py --source "https://xxx.feishu.cn/docx/<token>"
python3 scripts/02_create_doc.py
python3 scripts/03_post_process.py
python3 scripts/process_cites.py
python3 scripts/04_verify.py
python3 scripts/05_cleanup.py
```

**共享库** `scripts/lib.py` 提供通用函数：
- lark-cli 调用包装（`run_lark_cli`, `run_lark_cli_json`）
- 文档读取（`fetch_doc_content`, `fetch_doc_xml`）
- 图片处理（`extract_image_tokens`, `download_image`, `upload_image`）
- XML 解析（`xml_to_blocks`, `extract_text_blocks`, `get_image_context`）
- 状态管理（`load_state`, `save_state`, `update_state`）

**状态文件** `state.json`（在 cwd 下）：保存脚本间的中间状态
- `source_url`, `source_xml_path`, `img_tokens`, `img_dir`
- `target_dir_token`, `target_dir_name`, `new_doc_id`, `new_doc_url`
- `id_mapping`, `uploaded_images`, `verification_results`

---

## 工作流概览（理解用）

如果想理解整个流程或者手动执行，可以参考以下步骤。每个步骤的详细规范要么在脚本里，要么在 references/ 里。

```
第 0 步：环境自检（Lark CLI + 依赖 skills + 登录用户）
  ↓ 不通过则停止
第 0.5 步：尝试原生复制主文档（drive files copy）← 新增
  ├─ 成功 → 跳过第 2~8.5 步（扒取重建），直接到第 8.6 步（cite 重指向）
  └─ 失败 → 退回扒取重建（继续往下）
  ↓
第 2 步：读取源文档 + 下载所有图片
  ↓
第 3 步：清理 XML + 创建新文档（默认根目录，可指定目标目录）
  ↓
第 4 步：构建 source → new block ID 映射
  ↓
第 5 步：修复目录锚点（cite/链接引用重指向移到第 8.6 步）
  ↓
第 6 步：上传图片到新文档末尾
  ↓
第 7 步：移动图片到正确位置 ← 重点
  ↓
第 7.05 步：移动嵌套图片（折叠标题/段落内）← ElementTree 全量扁平化补救
  ↓
第 7.5 步：修复图片显示尺寸（scale）← 重点
  ↓
第 7.55 步：还原图片对齐（左/右）← 原生 API replace_image
  ↓
第 7.6 步：还原并排图 grid 布局
  ↓
第 7.65 步：校准图片前后空 p 数量 ← 源/新逐图比对 (before, after)，互换案例挪空 p
  ↓
第 7.7 步：迁移画板（whiteboard）← 读 raw 节点重建，保留原布局
  ↓
第 7.8 步：迁移内嵌表格（sheet）← 读单元格渲染成原生 table
  ↓
第 8 步：修复有序列表的 seq ← 重点
  ↓
第 8.5 步：合并连续 blockquote + 去除引用块归一化灰底
  ↓
第 8.6 步：处理被引用的其它飞书文档 ← 新增（cite 递归）
           探测权限 → 优先原生复制副本 → 失败则兜底递归扒取 → 重指向 cite/链接
  ↓
第 9 步：内容完整性核验 ← review 重点
  ↓
第 9.5 步：ol 分离核验 ← 新增：检测 ol 合并
  ↓
第 10 步：图片位置核验 ← review 重点
  ↓
第 10.5 步：重复 li 核验（block_replace 副作用检测）
  ↓
第 10.7 步：被引用文档（cite 递归）核验 ← 新增：状态统计 + 旧链接残留检测
  ↓
第 10.8 步：收尾去重核验（顶层）← 新增：同源多副本 → 清理零引用孤儿
  ↓
第 13 步：清理临时文件
```

---

## 关键经验（吸取教训的重点）

下面这些是从实际执行中总结出的**必须避免的坑**：

### 0.5. ol 合并后如何手动拆分（补救已生成的文档）

**症状**：源文档是「OL1 + img + OL2 + img + OL3 ...」多个独立 ol，但新文档把相邻的 ol 合成一个，导致图片位置全部错位。

**补救步骤**（参考 `references/api-limitations.md` 限制 5）：

1. 用 `docs +fetch --scope range` 拿到被合并 ol 的 li IDs
2. `block_delete` 删除多余 li
3. `block_insert_after` 在正确位置插入新的 `<ol><li seq="N">...</li></ol>`
4. `block_move_after` 把图片移到新 ol 之后
5. 重新跑 `04_verify.py`，确认 `merged_count == 0`

**预防**：`02_create_doc.py` 的 `clean_xml` 已在 `</ol><img/><ol>` 模式中插入空 `<p></p>` 占位符，新文档不会再合并。

### 1. 图片移动（最容易出错，已自动化）

`compute_image_anchors` + `move_images` 按图片前驱 top-level block 分三模式自动处理，**所有 anchor 都用 top-level block，绝不用容器末项 li**（规避 `block_move_after` 陷阱）：
- **direct**：前驱是 p/h/callout/blockquote/pre(代码块) 或图片在 grid 内/后 → 直接 anchor 到该 block
- **two_step**：前驱是 ol/ul + 有非空 p 后继 → 先把 img 移到后继 p 之后，再把后继 p 移到 img 之后（= 5.1 方案 A 自动化），得到 `ol, img, p`
- **fallback**：前驱是 ol/ul 但无后继（夹在两个 ol 间/文末）→ 退回 ol 末 li，可能轻微越位，需人工或增强修正

要点：计算 anchor 时**跳过空 `<p>` 分隔行**（否则映射不到）；同一 anchor 多图按**反向源文档顺序**移动；grid 容器图片 anchor 取 grid **前面**的文本块。

**图前空行保留（2026-06-18）**：direct 模式跳过空 p 找文本块作 anchor，会导致「源文档图片前的空行」丢失（图片直接贴到文本块，空行跑到图后）。修复：`compute_image_anchors` 记录「文本块与图之间的空 p 个数」`blank_gap`，`move_images` 用 `_nth_empty_p_after` 把图 anchor 到文本块后第 `blank_gap` 个空 p 之后，保留图前空行。ol 前驱的图前空行本就由占位符机制（`_find_empty_p_after_ol`）保留，不受影响。

**陷阱（2026-06-18）**：源 `ol → img → p(非空) → ol` 时，two_step 第二步会把 p 错插进第二个 ol 的 li1/li2 之间（block_move_after 容器陷阱的「anchor 紧跟容器」形态）。已由 `clean_xml` 对该模式插空 `<p></p>` 占位符预防 —— 图片改走占位符锚点，绕开 two_step 第二步。详见 api-limitations 限制 5.2。

**block_move_after 与空段落（手动补救时务必注意）**：实测 `block_move_after` 在以下情况会把 src 落到「锚点后面紧跟的空段落之后」而非紧贴锚点：①锚点后紧跟空 `<p>`；②**移动的 src 本身是空 `<p>`**。所以手动调空行时**不要移动空段落**——改用「`block_insert_after` 插新空 `<p></p>` + `block_delete` 删旧空行」，或移动相邻的**非空**块来达成目标顺序。

详见 `references/image-positioning.md` 与 `references/api-limitations.md` 限制 5.1 / 5.2

### 1.1 图片显示尺寸（scale）修复

**问题**：飞书 `docs +create` 不保留源文档的图片 scale，新文档所有图片默认 `scale="1.000000"`（全尺寸）。源文档通常用 scale 把图片缩到合理显示大小（如 0.4），让版面紧凑。

**修复**：第 7.5 步 `fix_image_sizes`：读源 img 的 `width/height/scale`，用 `block_replace` 更新新 doc 对应图片的 scale。

**匹配规则**：源 `<img src="<orig_token>">` 与新 `<img name="<orig_token>.png">`，源 src 是新 name（去掉 .png）的前缀。

### 1.2 图片对齐（左/右）还原

**问题**：飞书图片对齐（`align`：1=左 / 2=中 / 3=右）是 docx **原生 block 属性，XML 接口不暴露/不保留**，`media-insert` 上传默认居中。源文档里左/右对齐的图全变居中（实测：OpenClaw 指南 4 张左对齐截图变居中）。

**修复**：第 7.55 步 `fix_image_align`：用原生 blocks API（`api GET /docx/v1/documents/{doc}/blocks`）读源图 `align`，对非居中（1/3）的图用 `replace_image`（原生 PATCH，同 token）设回。

**两个坑**：
- XML `block_replace`（fix_image_sizes 改 scale 用）会**清掉 align** → `fix_image_align` 必须在 `fix_image_sizes` **之后**跑。
- `replace_image` 不带 `scale` 会把 **scale 重置成 1** → 调用时把新图当前 `width/height/scale` 一并传入，align 和 scale 都不丢（`replace_image` 接受 `token/width/height/align/scale`）。

### 1.3 折叠标题/段落内的嵌套图片

**问题**：飞书「**折叠标题**」(可折叠 heading) 会把其下整段内容作为**子块嵌套进 heading 元素**（XML 里是 `<h2>…<h3/><p/><img/>…</h2>`）。`lib.xml_to_blocks` 只递归进 `callout/blockquote/ol/ul/grid/column`，**不递归进 heading/p**，所以嵌套其中的图片对 `compute_image_anchors` **完全不可见**——从不被锚定、上传后全堆在文末（实测：OpenClaw 指南「内置 API 模型」等章节 21 张图丢位）。同理嵌套在 `<p>` 里的内联图也会漏。

**修复**：第 7.05 步 `move_nested_images`（在 `move_images` 之后）用 **ElementTree 全量扁平化**补救：
1. 扁平化源文档（递归进所有标签），找出 `xml_to_blocks` 漏掉的图片（祖先链含非递归容器）
2. 每张图取「最近的前驱可锚文本块」(p/h/callout/pre/blockquote/li 含文字) 作锚点
3. 在新文档（同样全量扁平化，能看到折叠标题内子块及其 id）里按 (tag, 文本) 定位锚点 block id
4. `block_move_after` 把图（已上传在文末）移到锚点后；同锚点多图按**反向源序**

**核验**：`04_verify` 的 `verify_image_positions` 用 ElementTree 数「文末堆积图片数」源/新对比，多出即有图卡在文末（嵌套图没移走）。

> 为什么不直接让 `xml_to_blocks` 递归进 heading：那会牵动 `build_id_mapping`/`fix_list_seq`/所有核验，且 `compute_image_anchors` 的三模式锚点逻辑都假设 anchor 是 depth-0 顶级块；改动面大、风险高。`move_nested_images` 作为独立补救步骤，只处理「漏网的嵌套图」，不碰已调好的主路径。

### 1.4 图片前后空 <p> 数量校准

**问题**：`move_nested_images` 把嵌套在折叠标题里的图用 `block_move_after(前驱文本块, img)` 移到位，**没有继承** `move_images` 的 `blank_gap` + `_nth_empty_p_after` 机制（见关键经验 1 的「图前空行保留」段）。结果：原本夹在 anchor 与图之间的「图前空 p」被 `block_move_after` 反吸到图后，**空 p 分布错位**。实测：OpenClaw 指南「附：阿里云百炼」h4 标题上方多出 1 个空行（其实是 HS1Gb 图前空 p 被搬到了图后）。

**4 类 case 与修法**（每行 `(图前, 图后)`）：

| 案例 | 源 | 修前 | 修法 | 修后 |
|---|---|---|---|---|
| **swap**（最常见，互换）| (1, 0) | (0, 1) | `block_move_after(前驱文本块, 图后空p)` → `block_move_after(空p, img)` | (1, 0) |
| **front_lost**（图前少）| (1, 1) | (0, 2) | 找图后空 p 移到图前 | (1, 1) |
| **back_lost**（图后少）| (1, 1) | (1, 0) | 找图前空 p 移到图后 | (1, 1) |
| **other**（复杂）| 各种 | 各种 | 留人工，记日志 | — |

**front_lost 和 back_lost 是对称关系**：都是某侧少 1 个空 p、另一侧多 1 个。区别是"哪侧是对的"——前驱文本块决定图前是否应有空 p（通常有，源文档靠空行隔开图和段），后继文本块决定图后。

**修复**：第 7.65 步 `normalize_image_empty_p_around`（在 `rebuild_grids` 之后）源/新逐图比对 `(before, after)`，自动修正三类常见 case。

**核验**：`04_verify` 的 `verify_image_blank_p` 列出源/新每张图 (before, after) 分布，按 `swapped/front_lost/back_lost/other` 分类报告。

**操作空 p 后必验证**（2026-06-23 踩坑）：`block_insert_after` / `block_move_after` / `block_delete` 操作空 p 后**立即用 `_count_around_empty_p` 验证**前后图的位置数是否匹配预期——block_move_after 在 src 是空 p、anchor 紧跟空 p 时可能把空 p 落到错位置（多/少 1 个空 p）；不要假设"插 1 个 + 删 1 个 = 净 0"，必须用诊断确认。

**关键陷阱（2026-06-23 实测）**：Python `xml.etree.Element` **没有 `__bool__` 重载**，所有 Element 都视为 falsy（无论有没有子节点）！写 `if anchor_e and ep_e` 永远 False，必须 `if anchor_e is not None and ep_e is not None`。**所有 ElementTree 处理代码都用 `is not None` 替代 truthy check**。

### 2. 有序列表序号

**问题**：
- 飞书 create 时把所有 `<li>` 的 seq 都设为 `"1"`
- `seq="auto"` 在飞书 API 中无效，必须显式设数字
- 嵌套 ol 内的 li seq 也需要修复
- **算法关键（混合模型）**：根据 ol **第一个 li 是否有显式 seq** 决定模式
  - 有显式 seq → 「显式新列表」：per-ol 计数器从 0 开始（如 9 项 OL「拆解对标」= 1,2,...,9）
  - 无显式 seq → 「隐式续号」：从上一个 ol 的 last_seq+1 继续（如「OL(灰豚=1) + OL(Kimi 无 seq)」→ Kimi=2）
  - **不能用单一全局计数器**：li 有显式 seq 时不更新，后续 li 全部 -1（实测：9 项 OL 末尾「……」被赋成 5）
  - **也不能用纯 per-ol 计数器**：忽略「隐式续号」场景（实测：Kimi 被赋成 1 而不是 2）
- **独立列表发现要遍历文档顺序，不能只用 `root.findall("ol")`**：源文档常把 ol 包在 `<p>`/`<callout>` 里（如 `<p><b>其他</b><ol>…</ol></p>`），只取 root 直接子节点会漏掉整段列表（既不编号又打乱后续重复文本的消费对齐）。用前序遍历收集所有「无 ol 祖先」的 ol。
- **期望 seq 按「文本 → seq 列表（文档顺序）」存储并逐个消费**：同一文本可能在多个列表里重复（如多处「……」占位项），纯文本 key 会互相覆盖（实测 bug：3 个「……」应为 5/9/5，旧版全被覆盖成 5）。`fix_list_seq` 按文档顺序遍历新文档 li，对同名文本依次取对应 seq。

详见 `references/list-and-nesting.md` 与 `references/api-limitations.md` 限制 13

### 3. 跨租户图片

**问题**：`+media-download` 返回 403，必须用 `+media-preview` workaround

### 4. 文件路径

**问题**：必须用相对路径，绝对路径会被拒绝

### 5. block_replace 后 ID 变化

**问题**：每次 block_replace 后 block ID 会变，必须重新 fetch

### 6. 嵌套结构容易丢失 / 重复

**问题**：
- 源文档中嵌套在 li 里的 ol/ul 内容在清理重建时容易被丢失
- **新 bug（2026-06-17）**：飞书 `docs +create` 解析 li 内的嵌套 ol 时，会把**嵌套 ol 的第一个 li 复制成外层 ol 的一个顶级 li**（同时保留嵌套 ol）。结果：嵌套 ol 完整，但外层 ol 多了一个重复的 li
- 必须核验并修复

详见 `references/list-and-nesting.md` 11.2 场景 A / B

### 6.5. grid 并排图布局丢失（已自动还原）

**问题**：源文档用 `<grid>` 把多张图并排显示，create 时图片被剥离、空 grid 被删，图片变成竖直堆叠的独立 block（视觉上从「同一行」变成「上下排列」）。

**根因**：图片是 token 存储、无法在 XML 里重建（insert 含 token 的 grid 会被飞书换成占位图）；create 后 grid 没了，图片成了竖排独立块。

**已自动处理**：第 7.6 步 `rebuild_grids`（在 `fix_image_sizes` 之后）自动还原。关键手法是**把已上传的 img block 移进新建 grid 的列**（不能重建图片）：
1. `block_insert_after` 插入带占位 `<p>` 的 grid：`<grid><column width-ratio><p>__GSn__</p></column>…</grid>`
2. `block_move_after(占位 p, img)` 把每张图移到列内 p 之后 → 落入该列
3. `block_delete` 删占位 p

只处理「每列含一张图」的 grid；width-ratio 会被飞书归一化（趋于等宽，小差异）。`clean_xml` 的空 grid 删除已泛化到任意列数。`04_verify.py` 的 `verify_grids` 核验源/新图 grid 数量是否一致。

详见 `references/api-limitations.md` 限制 16

### 6.6. 画板（whiteboard）丢失（已自动还原）

**问题**：源文档里的 `<whiteboard token="...">` 是 token 对象，`docs +create` 无法从跨租户 token 重建，会被**静默丢弃**——连标题下的占位都不留（实测：「第一次使用路径」章节的流程图画板整块消失）。

**已自动处理**：第 7.7 步 `migrate_whiteboards`（在 `rebuild_grids` 之后）自动还原，思路与图片迁移一致：
1. `whiteboard +query --output_as raw` 读源画板 **raw 节点**
2. 在对应锚点（最近的已映射顶级前驱块）后 `block_insert_after` 插入空白画板 `<whiteboard type="blank">`，拿 `block_token`
3. `whiteboard +update --input_format raw --overwrite` 把 raw 节点覆盖写入

**关键：必须用 raw 而非 mermaid**。raw 保留原始坐标/尺寸/样式/连接器，布局逐字节一致；mermaid（`--output_as code`）会让飞书重新自动布局，丢掉原版排布（如从「两行换行 + 回环连接器」退化成「一条直线」）。`02` `clean_xml` 已剥离 whiteboard 标签防止 create 留残骸；`04` `verify_whiteboards` 核验源/新画板数量。源画板跨租户无读取权限（raw 读不到）时跳过并告警。

### 6.7. 同步块 / 内嵌表格丢失（已自动还原）

**问题**：扒取重建时，以下两类块被 `docs +create` **静默丢弃**（实测：OpenClaw 指南「命名变迁史」段的第一阶段正文 + 全文 3 张内嵌表全部消失）：
- **`<synced-source>` 同步块**：飞书「内容同步」对象，正文内嵌在 XML 里
- **`<sheet>` 内嵌电子表格**：token 对象，无法从跨租户 token 重建

**已自动处理**：
- **同步块** → `02` `clean_xml` **解包**：去掉 `<synced-source>`/`<synced-reference>` 外层包裹标签，内部 `<p>` 正文降级成普通顶级段落、随 create 正常落地。
- **内嵌表格** → 第 7.8 步 `migrate_sheets`：`sheets +cells-get` 读单元格 → 渲染成飞书**原生 table**（首行作表头）→ `block_insert_after` 插到对应锚点后。内容逐字一致、视觉接近，且不依赖跨租户复制权限。

`02` `clean_xml` 剥离 sheet 标签防残骸；`04` `verify_embedded` 核验同步块正文是否保留、内嵌表迁移数。源表跨租户无读取权限时跳过并告警。锚点用 `_preceding_mapped_anchor`（画板/表格共用：向前找最近的已映射顶级块）。

### 7. str_replace 限制

**问题**：XML 模式下 str_replace 只支持行内文本匹配，不能匹配 XML 属性

### 8. 颜色和样式的 API 限制

**问题**：
- 灰色背景色 `rgb(229,230,233)` 会被归一化为 `rgb(242,243,245)`
- callout 会被自动添加 `border-color` 属性

**引用块灰底已自动处理**：源文档引用块文字常带几乎不可见的浅灰 `rgb(229,230,233)`，被飞书归一化成标准灰高亮 `rgb(242,243,245)` 后会渲染成明显灰盒子，与源文档视觉不符。第 8.5 步 `strip_blockquote_bg` 在合并 blockquote 后自动去掉引用块内的灰底（仅清灰值，黄/蓝高亮保留），恢复纯文字 + 左竖线样式。去灰底后只剩灰底的裸 span 会被飞书折叠成纯文字，`04_verify.py` 已对 span 计数做对应校正。其它位置（标题/callout/行内 code）的灰底不动。

详见 `references/api-limitations.md`

---

## 多层级 cite 引用递归处理（已实现：`process_cites.py`）

源文档里引用的**其它飞书文档**（`<cite type="doc" doc-id="...">` @文档，或指向
`/docx/`、`/wiki/` 的行内链接）会被自动一并复制到当前用户云空间，并把主文档里的
引用重指向到副本。由 `scripts/process_cites.py` + `scripts/cite_lib.py` 实现，作为
第 8.6 步在 `03_post_process` 之后自动运行；`run_all.sh` 已接线，无需手动调用。

### 处理策略（每个被引用文档）

1. **提取引用**：`extract_doc_references` 解析 `<cite type="doc" doc-id>`（排除
   `file-type="sheets|bitable"` 的内嵌表格）和指向飞书文档的 `<a href>` 链接，
   按 token 去重，排除自引用。
2. **探测权限**：`drive metas batch_query` 探测阅读权限（wiki 先用 `drive +inspect`
   解包到 docx token）。无权限 → 标注 `no_permission`，保留原链接。
3. **优先原生复制**：有权限时先尝试 `drive files copy` 创建副本（保真最高、最快），
   落在**与主文档同一目录**。
4. **兜底递归扒取**：原生复制失败（典型：跨租户、源文档禁止复制）→ 在独立子目录里
   递归调用 `run_all.sh` 完整扒取重建（`recursive_skill_copy`）。
5. **重指向**：`apply_cite_mapping` 用 `block_replace` 把主文档里命中的 cite `doc-id`
   和链接 URL 换成副本的（旧 `str_replace` 改属性的写法不生效，已弃用）。

### 多级递归 + 去重防环

- **嵌套一层层往下扒**：A 引用 B、B 引用 C → B、C 都会被复制。兜底递归路径天然
  递归（子 `run_all.sh` 内部会再跑一遍本步骤）；原生复制路径会对副本再跑一次本步骤
  （副本内部 cite 仍指向原文档，需继续处理）。
- **共享 registry 去重防环**：环境变量 `LARK_DOC_COPY_REGISTRY` 指向的 JSON 在所有
  递归层间共享，按 canonical docx token 记录每个文档的处理结果；已处理过的直接复用，
  A→B→A 这类回环不会无限递归（主文档启动时即把 self 登记进 registry）。
- **深度上限**：`LARK_DOC_COPY_MAX_DEPTH`（默认 5）兜底防失控，超深度的引用保留原
  链接并标注 `depth_exceeded`。
- **递归子调用约定**：`run_all.sh <url> <folder-token>` 第 2 参数让被引用文档落到
  同目录；子调用设 `LARK_DOC_COPY_SKIP_PREFLIGHT=1` 跳过重复自检。临时工作目录
  `_cite_*`、registry 与副本台账 `cite_copies.json` 由顶层 `05_cleanup.py` 统一清理。
- **收尾去重核验（防同源多副本）**：同一源文档被多处引用时，跨递归子进程的 registry
  去重可能漏命中，导致同源被扒取多份、最终只有一份被引用、其余成孤儿（实测：「小项目」
  多出一份）。`cite_lib.record_copy` 把每份新建副本追加进**只追加**的台账
  `cite_copies.json`（registry 同目录、随其共享路径在递归各层间共享）；顶层
  `process_cites.py` 跑完调 `dedup_orphan_copies`：按源分组，统计每份副本被「其它文档」
  引用的次数（排除自引用），保留 registry 的最终目标，把「零其它引用」的孤儿移到废纸篓
  （可恢复）。只动台账里本次新建的副本，绝不碰用户其它文档。

### 核验与报告

`04_verify.py` 的 `verify_cites` 统计各状态数量，并检测「已复制（done）的引用是否仍
残留旧 token」——残留说明重指向未完成，需重跑 `process_cites.py`。最终报告的
【引用的相关文档】列出每个引用的标题、状态（已复制 / 无权限 / 扒取失败 / 超深度）
和副本链接。收尾去重核验另会报告「同源多副本」组：保留的那份、清理掉的孤儿。

---

## 最终输出格式

skill 执行完成后，必须按以下格式输出（中文）：

```
========================================
飞书文档复制完成
========================================

源文档：<源文档标题>
新文档：<新文档链接>
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

---

## 用户偏好

- **中文交流**：所有输出使用中文
- **简洁务实**：不做冗长解释，只给关键信息
- **完成后输出变动详情**：便于用户 review
- **容忍样式细微差异**：但文字内容必须 100% 一致

---

## 已知限制

分两类。**A 类**当前无法消除，会出现在最终报告的【已知差异】里；**B 类**飞书 API 行为有坑，但脚本已自动处理，仅在出问题时需要按「关键经验」手动补救。

**A. 真正无法消除的差异**

1. **灰色背景色（仅引用块外）**：源 `rgb(229,230,233)` 被飞书归一化为 `rgb(242,243,245)`。标题/callout/行内 code 等位置保留此差异；**引用块内的灰底已由第 8.5 步自动去除**（见 B 类）
2. **callout 边框**：飞书自动添加 `border-color` 属性

**B. 飞书 API 行为坑（脚本已自动处理）**

| 限制 | 处理位置 | 详情 |
|---|---|---|
| create 强制 `li seq="1"` + 跨 ol 自动递增失效 | `03` `fix_list_seq`（上下文混合计数器） | 关键经验 2 / api-limitations 13 |
| 引用块（blockquote）内列表被算进顶级续号链，污染后续顶级列表编号 | `03` `_compute_expected_seqs` 把 blockquote 当独立编号作用域（块内不接外层续号、也不更新外层 last_seq） | 关键经验 2（2026-06-19） |
| 跨租户图片无法 `media-download`（403） | `lib.download_image` 走 `media-preview` | 关键经验 3 / api-limitations 4 |
| `OL + img + OL` 被合并成一个 ol | `02` `clean_xml` 插空 `<p>` 预防；`04` `verify_ol_separation` 检测 | 关键经验 0.5 / api-limitations 5 |
| `ol + img + p(非空) + ol` 段落被错插进列表 | `02` `clean_xml` 对该模式插空 `<p>` 占位符，绕开 two_step 第二步 | 关键经验 1 / api-limitations 5.2 |
| 连续 blockquote 渲染成独立盒子 | `03` `merge_consecutive_blockquotes` | api-limitations 11 |
| 引用块灰底归一化成明显灰盒子 | `03` `strip_blockquote_bg`（去引用块灰底）；`04` span 计数校正 | 关键经验 8 / api-limitations 1 |
| block_replace 后相邻 li 内容重复 | `04` `verify_duplicate_li` 检测 | api-limitations 12 |
| ol/ul 无 block ID、block_replace 后 ID 变化 | 全程靠文本匹配 + 每步重新 fetch | 关键经验 5 / api-limitations 6、7 |
| 图片 scale/width/height 不保留（scale 默认全尺寸；media-insert 偶发把原生宽高写成占位 100×100） | `03` `fix_image_sizes`（还原 width/height/scale 三者） | 关键经验 1.1 / api-limitations 15 |
| 图片对齐（左/右）不保留（XML 接口无 align，media-insert 默认居中） | `03` `fix_image_align`（原生 blocks API 读源图 align → `replace_image` 同 token 带 align+scale 设回）；`04` 状态报告 | 关键经验 1.2 |
| grid 并排图布局丢失（变竖排） | `03` `rebuild_grids`（移图入新建 grid 列）+ 原生 API 还原列宽；`04` `verify_grids` 核验 | 关键经验 6.5 / api-limitations 16 |
| 画板（whiteboard）被 create 静默丢弃 | `02` `clean_xml` 剥离 whiteboard；`03` `migrate_whiteboards`（读源 raw 节点 → 建空白板 → raw 覆盖写入，**raw 保布局**）；`04` `verify_whiteboards` 核验 | 关键经验 6.6 |
| `move_nested_images` 不继承 blank_gap → 图前/图后空 p 互换 | `03` `normalize_image_empty_p_around`（源/新逐图比对 (before, after)，互换案例挪空 p）；`04` `verify_image_blank_p` 诊断 | 关键经验 1.4 |
| 同步块（synced-source）/ 内嵌表格（sheet）被 create 静默丢弃 | `02` `clean_xml` 解包同步块、剥离 sheet；`03` `migrate_sheets`（读单元格渲染成原生 table）；`04` `verify_embedded` 核验 | 关键经验 6.7 |
| 连续堆叠图第二张及之后定位丢失（漂到无关章节） | `03` `is_anchorable_top` 跳过 img，同组图共用上游文本 anchor | api-limitations 17 |
| 折叠标题/段落内的嵌套图片对 `xml_to_blocks` 不可见 → 不被锚定、堆文末 | `03` `move_nested_images`（ElementTree 全量扁平化找嵌套图 + 文本锚点重定位）；`04` 文末堆积图核验 | 关键经验 1.3 |
| 图片下载/上传瞬时失败导致静默漏图 | `lib.download_image` + `03` `upload_images` 均加重试；`04` `verify_images` 兜底核验数量 | api-limitations 18 |
| cite `str_replace` 改属性不生效 | `process_cites.py` 改用 `block_replace` 整块重指向 | 多层级 cite 引用递归处理 |
| 跨租户 / 禁止复制时原生 `drive files copy` 失败 | `process_cites.py` 兜底递归 `run_all.sh` 扒取 | 多层级 cite 引用递归处理 |
| 同源文档被多处引用 → 递归重复扒取出孤儿副本 | `cite_lib.record_copy` 记台账 + 顶层 `dedup_orphan_copies` 收尾清理零引用孤儿 | 多层级 cite 引用递归处理（收尾去重核验） |

---

## 详细参考文档

| 文档 | 内容 |
|---|---|
| `references/image-positioning.md` | 图片定位三场景、签名核验、常见问题排查 |
| `references/list-and-nesting.md` | 有序列表 seq 修复、嵌套结构恢复 |
| `references/api-limitations.md` | 飞书 API 已知限制和应对（共 17 条，编号 1–16 + 5.1） |

## 脚本清单

| 脚本 | 功能 | 依赖 |
|---|---|---|
| `scripts/preflight.sh` | 环境自检（必须先执行） | lark-cli |
| `scripts/00_try_native.py` | 优先原生复制主文档（`drive files copy`）；成功则跳过 01/02/03 | lib.py, cite_lib.py |
| `scripts/01_fetch_source.py` | （兜底）读取源文档 + 下载图片 | lib.py, preflight.sh |
| `scripts/02_create_doc.py` | 创建新文档（默认根目录） | lib.py, 01_fetch_source.py |
| `scripts/03_post_process.py` | 映射 + 目录锚点 + 图片 + 还原 grid + 校准空 p + 迁移画板/内嵌表格 + seq + 合并连续 blockquote | lib.py, 02_create_doc.py |
| `scripts/process_cites.py` | 处理被引用的其它飞书文档（复制 + 递归 + 重指向） | lib.py, cite_lib.py, 03_post_process.py |
| `scripts/04_verify.py` | 内容核验 + 图片位置核验 + 重复 li 检测 + cite 引用核验 | lib.py, process_cites.py |
| `scripts/05_cleanup.py` | 清理临时文件（含 registry 与 `_cite_*` 工作目录） | lib.py |
| `scripts/run_all.sh` | 一键执行入口（`<url> [folder-token]`，按顺序调用上面所有） | 全部 |
| `scripts/lib.py` | 共享工具库（被其他脚本导入） | lark-cli |
| `scripts/cite_lib.py` | cite 递归处理库（registry/提取/探测/复制/递归/重指向） | lib.py, lark-cli |

---

## 调试教训 & 已知陷阱

排查/修改时反复踩到的坑，跨具体修复通用：

### Element.__bool__ quirk（最高频踩坑）

Python `xml.etree.Element` **没有 `__bool__` 重载**——所有 Element 都被视为 falsy（无论有没有子节点）。`if element:` 永远 False，`element and other_thing` 永远是 `other_thing` 的真假。

**正确**：`if element is not None` 或 `if element is not None and element.get("id"):`。

任何处理飞书 XML 的代码（`normalize_image_empty_p_around`、`move_nested_images`、未来新增的 element 处理函数）都用 `is not None` 替代 truthy check。

### 操作空 p 必验证（2026-06-23 案例）

`block_insert_after` / `block_move_after` / `block_delete` 操作空 p 后**立即用 `_count_around_empty_p` 验证**——block_move_after 在 src 是空 p、anchor 紧跟空 p 时可能把空 p 落到错位置（多/少 1 个空 p）。**不要假设"插 1 + 删 1 = 净 0"**——跑诊断确认。
