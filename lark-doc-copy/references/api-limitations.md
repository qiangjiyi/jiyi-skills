# 飞书 API 已知限制与处理建议

本文档总结了在使用 lark-cli 操作飞书文档时遇到的 API 限制。这些限制无法绕过，必须在最终报告中向用户说明。

---

## 限制 1：灰色背景色自动归一化

### 现象

源文档中使用 `background-color="rgb(229,230,233)"`（深一点的灰色），通过 `block_replace` 更新到新文档后，会被自动归一化为 `rgb(242,243,245)`（飞书标准 light-gray）。

### 影响范围

- 影响所有通过 `block_replace` 写入的 `<span background-color="...">` 标签
- 不影响通过 `docs +create` 一次性创建的内容（但 create 后所有内容又会受后续 block_replace 影响）

### 触发场景

```bash
# 会触发归一化
lark-cli docs +update --api-version v2 --doc "<id>" \
  --command block_replace \
  --block-id "<id>" \
  --content '<p><span background-color="rgb(229,230,233)">...</span></p>'

# 验证：fetch 后会发现颜色变成 rgb(242,243,245)
```

### 涉及的颜色

- `rgb(229,230,233)` → `rgb(242,243,245)`（最常见的灰色）
- 其他接近 named color 的自定义 RGB 值也可能被归一化

### 飞书 API 的设计意图

推测飞书把接近"named color"的 RGB 值自动识别为对应的命名色，避免颜色滥用导致视觉不一致。

### 处理建议

- **引用块（blockquote）内的灰底：已自动去除**。源引用块文字常带几乎不可见的
  `rgb(229,230,233)`，归一化成 `rgb(242,243,245)` 后会渲染成明显的灰盒子，与源
  文档视觉不符。`03_post_process.py` 的 `strip_blockquote_bg`（第 8.5 步，在合并
  blockquote 之后）会把引用块内的灰底（`rgb(229,230,233)` / `rgb(242,243,245)`）
  去掉，恢复纯文字 + 左竖线。仅清灰值，黄/蓝高亮保留。
  - **副作用**：去掉 background-color 后只剩灰底的裸 `<span>` 会被飞书折叠成纯
    文字（span 消失）。`04_verify.py` 的样式核验已对此做 span 计数校正（从源计数
    里扣除引用块内仅灰底的 span），避免误报 span 数量差异。
- **其它位置（标题/callout/行内 code）的灰底：容忍**。视觉差异很小（都是浅灰），
  不强制修复，在最终报告中说明"灰色背景色被归一化"。
- **不要尝试保留精确 RGB**：目前没有 API 可以保留源文档的精确 RGB 值。

---

## 限制 2：callout 自动添加 border-color

### 现象

源文档中的 callout：
```xml
<callout background-color="rgb(240,244,255)" emoji="❗">
```

新文档中的 callout（飞书自动添加）：
```xml
<callout background-color="rgb(240,244,255)" border-color="rgb(255,186,107)" emoji="❗">
```

### 影响

- 视觉上 callout 多了一个橙色边框
- 这是飞书的"美化增强"，无法通过 API 去除

### 处理建议

- **接受这个差异**：橙色边框实际上让 callout 更醒目
- **在最终报告中说明**：让用户知道这是飞书自动添加的

---

## 限制 3：飞书 create 时强制设置 li seq="1"

### 现象

`docs +create` 处理包含 `<li>` 的内容时，会忽略 XML 中的 seq 属性，统一设置为 `seq="1"`。

### 影响

新文档中所有 `<li>` 初始都是 seq="1"，导致列表显示为「1. 1. 1. 1.」。

### 处理方法

创建后必须用 `block_replace` 逐个修复每个 li 的 seq 值（详见 `list-and-nesting.md`）。

---

## 限制 4：跨租户图片无法直接下载

### 现象

- 源文档在租户 A（`p03a4vs9s2.feishu.cn`）
- 用户当前在租户 B（`qiangjiyi.feishu.cn`，个人账号）
- 用 `lark-cli docs +media-download --token "<源 token>"` 返回 HTTP 403

### 原因

跨租户访问时，token 鉴权失败。

### workaround

改用 `lark-cli docs +media-preview`：
```bash
lark-cli docs +media-preview --token "<源 token>" \
  --output "./<token>.png"
```

`+media-preview` 可以下载跨租户图片到本地（鉴权方式不同）。

### 处理建议

- 优先使用 `+media-preview` 而非 `+media-download`
- 如果 `+media-preview` 也失败，记录失败图片，在报告中标注

---

## 限制 5：飞书 ol 合并

### 现象

源文档中两个连续的 `<ol>` 块（中间可能隔着 `<img>`）：

```xml
<ol><li>item 1</li></ol>
<ol><li>item 2</li></ol>
```

新文档中可能被合并为单个 ol：

```xml
<ol><li>item 1</li><li>item 2</li></ol>
```

更严重的情况：源文档是 `OL + img + OL`（如「工具推荐 1 + 截图 + 工具推荐 2 + 截图」），
飞书解析器会先把 img 当作 ol 内部的富文本插到第一个 ol 末尾，第二个 ol 被并入同一个 ol。
最终结果是：合并 ol（带两个 li）→ 第一个 img → 第二个 img，全部串位。

### 历史 bug

2026-06-17 用户报告「账号拆解工具推荐」章节：
- 源文档：OL(灰豚) → img(灰豚截图) → OL(Kimi) → img(Kimi截图)
- 复制后：OL(灰豚, Kimi) → img(灰豚截图) → img(Kimi截图)
- 根因：`02_create_doc.py` 的 `clean_xml` 删除了 img 标签，导致两个 ol 相邻，触发合并

### 处理建议（已实现 workaround）

在 `02_create_doc.py` 的 `clean_xml` 中：**当 img 位于两个 ol/ul 之间时，删 img 前先把 img 替换成空 `<p></p>` 占位符**。这样飞书解析器会保留两个 ol 的独立边界，img 仍能通过第 7 步的 `block_move_after` 移动到正确位置。

代码位置：`scripts/02_create_doc.py` `clean_xml` 函数。

---

## 限制 5.1：图片 anchor 选错（ol 之前的 p vs ol 的最后 li）

### 现象

源文档结构：
```xml
<p>注册流程就不放了...</p>
<ol>
  <li>自动回复...</li>
  <li>设置菜单栏...</li>
  <li>多准备几个微信...</li>  ← 正确的 anchor
</ol>
<img>...联系叁斤 按钮截图...</img>
<p>选择什么方式承接...</p>
```

错误的 anchor 选择：
- ❌ 用 `<p>注册流程就不放了...</p>` 作 anchor → 图片被放到 ol 之前
- ✅ 用 `<li>多准备几个微信</li>`（最后 li）作 anchor → 图片跟在 ol 之后

### 历史 bug

2026-06-17 用户报告「公众号承接方式」章节的截图位置错乱。
根因：老代码 `compute_image_anchors` 中存在「关键场景 3.5」反模式：
- 看到 `OL + img + 短 p + ...` 就猜测图片属于"下一段"
- 实际上：物理位置即语义位置，图片在 ol 后面就属于这个 ol
- 该段代码已在 2026-06-17 删除（详见 `references/image-positioning.md` 场景 A.1）

### block_move_after 的"锚点为容器末尾"陷阱

**症状**：当 anchor 是某个容器的最后一项（如 ol 的最后 li），`block_move_after --src-block-ids <img>` 不会把 img 放到 anchor "正后方"，而是可能放到 anchor 所在容器**之后**的下一个 top-level block 之后。

**实测案例**（2026-06-17 公众号承接章节）：
- 源结构：`ol(3 items) → img → p("选择什么方式承接")`
- 期望 anchor = ol 的最后 li（"多准备几个微信"）
- 调用 `block_move_after --block-id <最后li> --src-block-ids <img>` 后实际得到：`ol → p → img → h2`（img 跑到了 p 后面，而不是 ol 后面）

### 当前实现（`compute_image_anchors` + `move_images`，已自动规避陷阱）

核心原则：**所有 block_move_after 的 anchor 都用 top-level block，绝不用容器末项 li**。按图片前驱 top-level block 分三种模式（计算前会跳过空 `<p>` 分隔行）：

| 模式 | 触发条件 | 移动方式 |
|---|---|---|
| `direct` | 前驱是 p/h/callout/blockquote，或图片在 grid 内/后 | 直接 `block_move_after(前驱 block, img)`——top-level anchor，可靠 |
| `two_step` | 前驱是 ol/ul，且后面有可用的非空 p 后继 S | 先 `move_after(S, img)` 得 `ol,S,img`，再 `move_after(img, S)` 得 `ol,img,S`——两步都是 top-level anchor，规避陷阱（这就是 5.1 方案 A 的自动化形式）。**注意**：当 S 后紧跟另一个 ol/ul 时第二步会命中容器陷阱（见限制 5.2），此场景已由 `clean_xml` 的空 p 占位符预先接管，不再走 two_step |
| `fallback` | 前驱是 ol/ul 但找不到后继（夹在两个 ol 之间、或文末） | 退回 `move_after(ol 末 li, img)`，可能命中陷阱、轻微越位，需人工或后续增强修正 |

逐张按**反向源文档顺序**移动（保证同一 anchor 多图顺序正确）。代码：`scripts/03_post_process.py`。

**已知残留**：`fallback` 的「`ol → img → ol → img → h2` 夹心」场景（图片夹在连续 ol 之间）仍可能越位到 h2 之后。彻底修复可用 `clean_xml` 在该位置插入的空 `<p></p>` 占位符作为可靠 anchor（待增强）；或按方案 B 手工修。

**必须做的核验**：移动后跑 `04_verify.py` 看 `image_positions`；`mismatch_count > 0` 时按上面残留说明处理。

---

## 限制 5.2：two_step 第二步把段落错插进列表（img 紧跟 ol 时）

### 现象

源结构 `ol(5项) → img → p("其他赛道一样") → ol(基于价值, 遇到瓶颈)`，新文档里
「其他赛道一样」跑进了第二个 ol 的 li1 和 li2 之间，把 ol 拆成两段：

```
1. 基于价值做垂直，聚焦精准人群和需求，起号快，天花板低
其他赛道一样          ← 错位
2. 遇到瓶颈后，可以扩散人群，价值点下移，圈选更大的人群
```

### 根因（block_move_after 容器陷阱的第二种形态）

限制 5.1 的陷阱不止「anchor 是容器末项」，还有「**anchor 是顶级 block，但它紧跟着一个容器**」：

`two_step` 移动图片分两步：
1. `move_after(succ_p, img)` → `ol5, p(succ), img, ol2`（img 此时**紧跟 ol2**）
2. `move_after(img, succ_p)` → 想把 succ_p 移到 img 正后方，但因 img 紧跟 ol2，
   飞书把 succ_p 错插到 **ol2 第一个 li 之后** → `ol5, img, ol2(li1), p(succ), ol2(li2)`

即：只要 `block_move_after` 的 anchor 后面紧跟 ol/ul 容器，被移动的块就会被吸进容器内部（插到容器首项之后），而不是落在 anchor 正后方。

### 历史 bug

2026-06-18 用户报告「赛道选择」章节「其他赛道一样 + 1./2.」顺序错乱。
实测复现：在测试文档上完整重放 `two_step` 两步，第二步精确产生上述拆分。

### 处理方法（已实现 workaround）

`02_create_doc.py` 的 `clean_xml` 针对 `</ol|ul><img/><p>非空文本</p><ol|ul>` 模式，
把 img 替换成空 `<p></p>` 占位符。这样图片走 `move_images` 的**占位符锚点**
（`_find_empty_p_after_ol`）直接落位，彻底绕开 `two_step` 第二步，得到
`ol5, 空p, img, p(succ), ol2`（ol2 完整不拆，仅多一个无害空行）。

只匹配「单个非空 p 后紧跟列表」：多 p 或空 p 场景第二步不会让 img 紧跟容器，
本身就不触发陷阱，无需占位符。

### 补救已生成文档（手工 3 步）

若文档已生成且命中此 bug（`img, ol(基于价值[1]), p(其他赛道一样), ol(遇到瓶颈[2])`）：
1. `block_move_after(li基于价值, li遇到瓶颈)` 把两个 li 并回同一 ol
2. `block_move_after(p其他赛道一样, li基于价值)` + `block_move_after(li基于价值, li遇到瓶颈)`
   反向移动，让 p 自然落在 ol 之前（**不能**直接 `move_after(img, p)`，会再次命中陷阱）
3. 清理被拖带错位的空 `<p>`（可 `block_delete`）

---

## 限制 5.3：图片前的空行丢失 + block_move_after 对空段落的怪异行为

### 现象

源文档 `文本 → 空<p> → img` 时，新文档里图片直接贴在文本下面，图前空行没了
（实测：「背景图设置」段落的图前空行丢失）。

### 根因

`compute_image_anchors` 找 anchor 时**跳过空 `<p>`**（空 p 不在文本映射里），导致
direct 模式把图 anchor 到文本块、紧贴文本，原本图前的空 p 被挤到图片后面。

### 处理（已自动化）

`compute_image_anchors` 记录「文本块与图之间的空 p 个数」`blank_gap`；`move_images`
用 `_nth_empty_p_after(new_xml, 文本块, blank_gap)` 把图 anchor 到文本块后第
`blank_gap` 个空 p 之后，保留图前空行。新建后结构 `文本, 空A, 空B`（图删后两空 p
相邻），anchor 到空A → `文本, 空A, img, 空B`，与源文档一致。

ol 前驱的图前空行本就由占位符机制（`_find_empty_p_after_ol`）保留，不受此问题影响。

### ⚠ block_move_after 对空段落的怪异行为（手动补救必读）

实测 `block_move_after` 在两种情况下会把 src 落到「锚点后面紧跟的空段落**之后**」，
而非紧贴锚点：

1. **锚点后紧跟空 `<p>`**：`move_after(锚点, src)` → src 落在空 p 之后
2. **被移动的 src 本身是空 `<p>`**：空 p 会越过锚点后面的块

所以**手动调整空行时不要移动空段落**。可靠做法：
- 加空行：`block_insert_after(非空锚点, '<p></p>')`（insert 不受此问题影响）
- 删空行：`block_delete`
- 调顺序：移动**非空**块（文本/图/标题），且锚点的后继也是非空块

**⚠ 删空 p 前先对照源文档**：手动补救（如还原 grid / 调图片顺序）时清理「多余」空
`<p>` 之前，务必确认它在源文档里不是一个**有意义的空行**（如段落与标题之间的间距）。
实测教训：修图片区域时误删了「正文与 h2 之间」的空行，造成新的缺空行问题。删之前
用 `seq(源, 关键词)` 对比该位置源文档是否本就有空行。

---

## 限制 6：飞书 ol/ul 没有 block ID

### 现象

`<ol>` 和 `<ul>` 容器本身没有 ID，只有里面的 `<li>` 有 ID。

### 影响

- 无法直接通过 ID 定位 ol 容器
- 必须通过解析 XML 结构 + 文本匹配来推断位置

### 处理建议

- 在第 4 步构建映射时，以 li 为主要映射单位
- 图片定位时（指向 ol 后的图片），用 ol 的最后一个 li 作为 anchor

---

## 限制 7：块 ID 在 block_replace 后会变化

### 现象

执行 `block_replace` 后，被替换的 block 会获得新的 ID。

### 影响

- 之前的 ID 映射会失效
- 必须重新 fetch 获取新 ID

### 处理建议

- 每次 `block_replace` 后都重新 fetch
- 用 fetch 的最新 ID 进行后续操作

---

## 限制 8：颜色 RGB 的命名色映射表

飞书 API 的颜色处理基于以下命名色（参考 lark-doc-xml.md）：

| 颜色名 | RGB |
|--------|-----|
| gray | 187,191,196 |
| red | - |
| orange | - |
| yellow | - |
| green | - |
| blue | - |
| purple | - |
| light-red | - |
| light-orange | - |
| light-yellow | - |
| light-blue | - |
| light-gray | 242,243,245 |
| medium-gray | - |

**特殊 RGB 值**：
- `rgb(240,244,255)` = light-blue（5 个 callout 使用，保留正确）
- `rgb(229,230,233)` = 自定义灰色（17 个，被归一化为 light-gray）
- `rgba(255,246,122,0.8)` = 黄色高亮（6 个，保留正确）

---

## 限制 9：str_replace 的匹配限制

### 现象

`docs +update --command str_replace` 在 XML 模式下：

- **只支持行内文本匹配**，不能匹配 XML 属性
- 不能跨 block / 跨段落匹配

### 影响

- 不能用 `str_replace` 替换 `<span background-color="...">` 中的属性值
- 不能用 `str_replace` 替换 `<a href="...">` 中的 URL

### 处理建议

- 用 `block_replace` 替换整个 block 的内容
- 对纯文本内容可以用 `str_replace`（如 `小红书推荐机制` → `小红书推荐机制TEST`）

---

## 限制 10：本地文件路径必须用相对路径

### 现象

`lark-cli docs +media-insert --file <path>` 中：

```
错误示例：
--file "/tmp/lark-copy/images/<token>.png"
→ "unsafe file path: --file must be a relative path within the current directory"

正确示例：
--file "_img_download/<token>.png"
```

### 处理建议

- 上传前先 cd 到工作目录，把图片放到 cwd 的子目录中
- 用相对路径如 `_img_download/<token>.png`

---

## 综合建议

执行此 skill 时，遇到上述限制应该：

1. **不要尝试强行绕过**：很多限制是飞书 API 设计的硬性约束
2. **在最终报告中明确说明**：让用户知道哪些差异是 API 限制导致的
3. **关注内容而非像素级样式**：容忍样式差异，保证内容 100% 一致
4. **保留容忍度**：用户最终关心的是"看起来一样"而非"字节级一致"
## 限制 11：连续 blockquote 渲染差异（**已可修复**）

### 现象

源文档中，相邻的多个 `<blockquote>` 元素的内部 `<p>` 不带 id：
```xml
<blockquote id="A"><p><span>第 1 行</span></p></blockquote>
<blockquote id="B"><p><span>第 2 行</span></p></blockquote>
```

这种结构下，飞书渲染为**一个合并的 blockquote 容器**，里面有 4 行文字，整体只有一个左侧灰色竖条。

新文档中，同样的内容飞书自动给所有 `<p>` 分配 id：
```xml
<blockquote id="A"><p id="auto-generated-1"><span>第 1 行</span></p></blockquote>
<blockquote id="B"><p id="auto-generated-2"><span>第 2 行</span></p></blockquote>
```

这种结构下，飞书渲染为**4 个独立的 blockquote 盒子**，每个盒子都有自己的背景色，盒子之间有间隔。

### 解决方案（推荐）：合并连续 blockquote

虽然无法去除 `<p>` 上的 id，但可以通过**合并连续 blockquote 为一个**来解决：

```xml
<!-- 修复前：3 个独立 blockquote -->
<blockquote><p>第 1 行</p></blockquote>
<blockquote><p>第 2 行</p></blockquote>
<blockquote><p>第 3 行</p></blockquote>

<!-- 修复后：1 个统一 blockquote -->
<blockquote>
  <p>第 1 行</p>
  <p>第 2 行</p>
  <p>第 3 行</p>
</blockquote>
```

### 执行步骤

1. 检测新文档中连续的多个 `<blockquote>` 元素（它们之间没有其他 block 元素）
2. 获取每个 blockquote 内 `<p>` 的内容
3. 用 `block_replace` 将第一个 blockquote 替换为合并后的版本（包含所有 `<p>`）
4. 用 `block_delete` 删除其他重复的 blockquote

### 为什么这样能解决

- 合并后的单个 `<blockquote>` 内有多个 `<p>`，飞书渲染为统一的盒子
- 虽然每个 `<p>` 仍然有 id，但它们都在同一个 blockquote 内，视觉上是统一的
- 与源文档的结构略有差异（源文档是多个独立 blockquote），但视觉效果更接近源文档

### 注意

- 这个修复会让 XML 结构与源文档不同（数量上），但视觉效果更好
- 如果严格保留源文档结构，可以接受这个差异

---

## 限制 12：block_replace 可能产生重复 li

### 现象

当用 `block_replace` 修复嵌套结构（给外层 li 写入带嵌套 ol 的新内容）时，可能出现**重复 li**：

源文档结构：
```xml
<ol>
  <li>关于保量的补充
    <ol>
      <li>如果目标曝光是 24 小时内 1000 次...</li>
      <li>新笔记要获得更多保量的话...</li>
    </ol>
  </li>
</ol>
<p></p>
<ol>
  <li>关于垂直</li>
</ol>
```

执行 block_replace 后可能变成：
```xml
<ol>
  <li>关于保量的补充
    <ol>
      <li>如果目标曝光是 24 小时内 1000 次...</li>
      <li>新笔记要获得更多保量的话...</li>
    </ol>
  </li>
  <li>如果目标曝光是 24 小时内 1000 次...  <!-- 重复项！ -->
</ol>
<p></p>
<ol>
  <li>关于垂直</li>
</ol>
```

外层 li 的内容（包括嵌套结构）被复制了一份作为同一 ol 的下一个顶级 li。

### 原因

- 飞书的 block_replace 在替换一个 li 时，可能将其内部嵌套的 `<ol>` 的内容也复制为兄弟 li
- 这可能是飞书解析器对嵌套结构的一种处理方式
- 具体触发条件不完全确定，但发生概率较高

### 检测方法

**`scripts/04_verify.py` 中的 `verify_duplicate_li` 函数会自动检测**：

```python
def verify_duplicate_li(state: dict) -> dict:
    """检查同一个 ol 内是否有重复的 li 内容"""
    for ol in root.iter("ol"):
        text_to_ids = {}
        for li in ol.findall("li"):
            text = "".join(li.itertext()).strip()
            if not text or len(text) <= 20:
                continue
            if text in text_to_ids:
                text_to_ids[text].append(li.get("id"))
            else:
                text_to_ids[text] = [li.get("id"]]
        for text, ids in text_to_ids.items():
            if len(ids) > 1:
                # 报告重复项
```

### 处理方法

**步骤 1**：检测到重复后，保留第一个，删除其他的：

```bash
# 保留第一个 li，删除其他重复的 li
lark-cli docs +update --api-version v2 --doc "<id>" \
  --command block_delete --block-id "<duplicate_li_id>"
```

**步骤 2**：或者用 `block_replace` 把重复的 li 替换为空 li：
```bash
lark-cli docs +update --api-version v2 --doc "<id>" \
  --command block_replace --block-id "<dup_id>" --content "<p></p>"
```

### 预防建议

1. **修复嵌套结构前先检查**：用 `verify_duplicate_li` 检查当前状态
2. **修复后立即检查**：每次 block_replace 后都跑一次 `verify_duplicate_li`
3. **嵌套修复时谨慎**：先用一个简单的修复测试看是否触发此问题

---

## 限制 13：跨 ol 自动递增编号失效

### 现象

源文档中，相邻的多个 `<ol>` 块依赖飞书的"跨 ol 自动递增"功能：

```xml
<!-- 源文档 -->
<ol><li seq="1">关于小红书养号</li></ol>
<p>说明文字...</p>
<ol><li>笔记目标清晰</li></ol>  <!-- 无 seq，依赖自动递增到 2 -->
<p>说明文字...</p>
<ol><li seq="3">激励用户互动</li></ol>  <!-- 显式 3 -->
```

新文档中，同样的结构变成：
```xml
<!-- 新文档（每个 ol 都从 1 开始）-->
<ol><li seq="1">关于小红书养号</li></ol>
<ol><li seq="1">笔记目标清晰</li></ol>  <!-- 显示为 1，不是 2 -->
<ol><li seq="1">激励用户互动</li></ol>  <!-- 显示为 1，不是 3 -->
```

### 原因

- 飞书 API 在 `block_replace` 和 `docs +create` 时**强制给所有 `<li>` 加 seq 属性**
- 即使写入 `<li>` 不带 seq，飞书也会自动分配 `seq="1"`
- 飞书的"跨 ol 自动递增"只对**没有显式 seq 的 li** 生效
- 我们无法让 li 保持"无 seq"状态

### 已尝试的解决方案

1. ❌ `block_replace` 写入 `<li>` 不带 seq → 飞书自动加 `seq="1"`
2. ❌ 用 str_replace 移除 seq 属性 → XML 模式下 str_replace 不能匹配属性
3. ❌ 用空 li 替换 → 没有 seq 仍然被飞书加为 "1"

### 处理建议

#### 方案 A：基于上下文的智能 seq 计算（推荐）

在 `scripts/03_post_process.py` 的 `fix_list_seq` 函数中，分析源文档的 ol 序列，根据上下文计算每个 li 应该显示的编号：

```python
def calculate_expected_seq(src_ols, src_li_id, previous_count):
    """根据源文档结构和前一个 ol 的项目数计算期望 seq"""
    # 如果源 li 有显式 seq，使用它
    # 否则，根据前面 ol 的项目数累计计算
    pass
```

#### 方案 B：接受差异

源文档的"无 seq 自动递增"依赖飞书的特殊行为，新文档无法完整复现。

在最终报告中说明：
> 源文档中相邻 ol 之间的自动递增编号（如 4 → 5 → 6）无法在 API 层实现，新文档的每个 ol 都从 1 开始。

### 实际影响

- 对主要内容理解无影响
- 仅影响读者对列表的"连续性"预期
- 实际使用场景中，用户通常能理解每个独立 ol 的含义

### 当前实现（混合计数器 + 文档顺序消费，已落地方案 A）

`scripts/03_post_process.py` 的 `fix_list_seq` + `_compute_expected_seqs` 用源文档
结构计算每个 li 应显示的编号，再 `block_replace` 写回新文档。核心要点：

1. **混合计数器**：每个独立 ol 看「第一个 li 是否有显式 seq」决定模式 —— 有显式
   seq → per-ol 计数器从 0 开始；无 → 从上一个 ol 的 last_seq+1 续号（隐式续号）。
   嵌套 ol 独立从 1 编号。详见函数 docstring 的判别示例。
2. **独立列表发现用文档顺序遍历，而非 `root.findall("ol")`**：源文档常把 ol 包在
   `<p>`/`<callout>` 里（如 `<p><b>其他</b><ol>…</ol></p>`），只取 root 直接子节点
   会漏掉这些列表 → 整段不编号、且打乱重复文本的消费对齐。改用前序遍历收集所有
   「无 ol 祖先」的 ol。
3. **期望值按「文本 → seq 列表（文档顺序）」存储，逐个消费**：同一文本可能在多个
   列表里重复（如多处「……」占位项），纯文本 key 会互相覆盖。改成列表后，
   `fix_list_seq` 按文档顺序遍历新文档 li，对同名文本依次消费对应 seq。

### 历史 bug（seq 计算）

- v1 全局计数器：li 有显式 seq 时不更新计数器 → 9 项 OL 末尾「……」错为 5
- v2 per-ol 计数器：忽略隐式续号 → Kimi 错为 1（应接 灰豚=1 得 2）
- v3 混合模型：显式/隐式分别处理
- **v4（2026-06-18）**：修两个 bug —— (a) 重复文本「……」互相覆盖（3 个「……」
  应为 5/9/5，旧版全被覆盖成 5）；(b) `<p>` 包裹的 ol（「其他」列表）被
  `root.findall("ol")` 漏掉，既不编号又打乱「……」消费对齐。改为「文档顺序遍历找
  独立 ol」+「文本→seq 列表逐个消费」后，三个「……」正确得到 5/9/5。

---

## 限制 14：图片在两个 ol 之间时的位置判定

### 现象

源文档中，图片可能位于两个 ol 之间：

```xml
<ol>...5 个 li...</ol>
<img ... />  ← 图片在 ol 1 之后
<p>其他赛道一样</p>  ← 简短过渡文本
<ol>...2 个 li...</ol>
```

**问题**：从 XML 顺序看，图片应该在 ol 1 之后。但从视觉渲染看，图片应该放到 ol 2 之后（因为图片视觉上属于 ol 2 的内容）。

### 原因

- 飞书的"OL 之后图片"的视觉渲染规则不是简单的 XML 顺序
- 当图片前后有"短过渡文本 + OL"的结构时，图片倾向于显示在第二个 OL 之后
- 我们无法直接从 XML 顺序推断视觉位置

### 解决方案（已实现）

`scripts/03_post_process.py` 的 `compute_image_anchors` 函数实现了**关键场景 3.5 检测**：

```python
# 检测：如果图片后面紧跟一段短 p（≤20字），然后是另一个 ol
# 则把图片放到第二个 ol 之后
for j in range(i + 1, min(len(src_blocks), i + 15)):
    next_b = src_blocks[j]
    if next_b["depth"] == 0 and next_b["tag"] in ("ol", "ul"):
        # 检查图片和这个 ol 之间是否有短过渡文本
        transition_text = ...
        if 0 < len(transition_text) <= 20:
            # 找到这个 ol 的最后一个 li
            anchor_src_id = last_li_of_this_ol
            break
```

### 实际效果

| 源文档结构 | 修复前 anchor | 修复后 anchor |
|---|---|---|
| img 在 ol 1 之后（无 ol 2） | ol 1 最后 li ✓ | ol 1 最后 li ✓ |
| img 在 ol 1 + 短文本 + ol 2 之后 | ol 1 最后 li ✗ | **ol 2 最后 li** ✓ |

---

## 限制 15：图片 scale 不保留（**已可修复**）

### 现象

源文档的 img 标签通常带 `scale` 属性（如 0.4），控制图片显示尺寸。
但 `docs +create` 创建的新文档，所有图片的 `scale` 都是 `1.000000`（原图全尺寸显示）。

实际影响：
- 源文档图片按 0.4 缩放显示（如 700×400 的图显示为 280×160）
- 新文档图片按 1.0 显示（同样的 700×400 显示为 700×400，占满版面）
- 整篇文档视觉密度、节奏感完全错位

### 历史 bug

2026-06-17 用户报告「图片和参考文档不一致」时发现此问题。
根因：`docs +create` API 不接受 `scale` 参数（或忽略），新建文档时一律设为 1.0。

### 补充 bug（2026-06-18）：width/height 也会被写成占位 100×100

`media-insert` 偶发把图片**原生 width/height 写成占位的 100×100**（实测：问答集 8 张图有 3 张如此）。此时即便 `scale` 与源一致，显示尺寸 = 原生尺寸 × scale 仍然偏小（如源 1080×708、scale 1.0，新文档 100×100 渲染成小方块）。

旧版 `fix_image_sizes` 只比/只改 `scale`，且"scale 相同就 `continue` 跳过"，导致这类图被漏修。**已修复**：现在同时还原 `width/height/scale` 三者，判断"是否需要更新"也同时看三者（缺失属性补上）。

### 处理建议（已实现 workaround）

**第 7.5 步 `fix_image_sizes`**（`scripts/03_post_process.py`）：

1. 读源 XML 解析所有 `<img>` 的 `width/height/scale`，按 `src` 作 key
2. 读新文档当前所有 `<img>`，按 `name`（去掉 `.png/.jpg`）的前缀匹配源 `src`
3. 对 width/height/scale 任一不匹配的新图片，用 `block_replace` 替换整个 img 标签，把三者都改成源值

**匹配规则**（与 `references/image-positioning.md` 一致）：
- 源：`<img src="<orig_token>">`
- 新：`<img name="<orig_token>.png">`（name 比 src 长，src 是 name 前缀）

代码位置：`scripts/03_post_process.py` `fix_image_sizes` 函数。

**手动补救已有文档**：

```python
# 1. 读源 XML 拿到所有 src→(w, h, scale) 映射
# 2. 读新文档所有 img 的 bid
# 3. 对每个 bid，对每个 src 比对：若 name (去掉 .png) 以 src 开头，且 scale 不匹配，
#    则用 block_replace 更新 scale
```

---

## 限制 16：grid 并排图布局丢失（**已自动还原**）

### 现象

源文档中用 `<grid>` 容器把多张图片并排显示（如「对标选题」章节的两张图在同一行）。新文档里 grid 被拆掉，两张图变成竖直堆叠。

**根因**：
- 图片是 **token 存储**的，无法在 XML 里重建：用 `block_insert_after` 插入含 `<img src="token">` 的 grid，飞书会**忽略 token、换成 512×512 占位图**（实测）。所以不能「重新创建」图片到 grid 里，只能**移动已上传的 img block**。
- create 时图片被 `clean_xml` 剥离、空 grid 被删除，图片变成竖排的独立 block。

### 关键发现（怎么把已有图片放进 grid 列）

`block_move_after(anchor=空 column, src=img)` 会把 img 放到 column **之后**（grid 的直接子节点，夹在两列之间），**不进列内**。正确做法是让列里先有一个占位块：

1. `block_insert_after` 插入带占位 `<p>` 的 grid：
   `<grid><column width-ratio="r1"><p>__GS0__</p></column><column width-ratio="r2"><p>__GS1__</p></column></grid>`
2. `block_move_after(占位 p, img)` —— 把 img 移到列内 p 之后，img 就**落入该列**
3. `block_delete` 删掉占位 p，列里只剩 img

### 历史 bug

2026-06-17 用户报告「5.3 选题制定 对标选题」两张图未在同一行（当时只能手动补救）。
2026-06-18 实现自动化 `rebuild_grids`。

### 当前实现（`rebuild_grids`，第 7.6 步，已自动化）

`03_post_process.py` 的 `rebuild_grids` 在 `fix_image_sizes` 之后运行：
1. `_parse_source_image_grids` 解析源文档里「纯图片列」的 grid，返回 `[(width-ratio, [tokens...]), ...]`（**每列可含 1 张或多张图**）。判定纯图片列：列里除 `<img>` 和空 `<p>` 外有正文文字 → 整个 grid 跳过（那是图文混排/文本列 grid，create 能原样保留，误处理会破坏布局）。
2. 对每个 grid：用 `name="<token>.png"` 在新文档找到对应 img block；anchor 取「第一张图的前一个同级块」（`_preceding_sibling_id`）。
3. 「占位 p」三步法：每列插一个占位 `<p>__GScN__</p>`；**列内多图依次首尾相接**——第一张移到占位 p 之后落入列，后续每张移到上一张之后（保持列内上下顺序）；最后删占位 p。
4. 幂等：首图已在 `<column>` 内则跳过，不会重复建 grid；每个 grid 处理前重新 fetch（id 会变）。

**多图支持（2026-06-23）**：原先只还原「每列 1 图」，碰到 2 列×每列 2 图的 2×2 图墙时只把每列首图放进列、其余图掉到 grid 外变竖排（实测：「共享文件夹」段 4 图墙错位）。现按上面的列内首尾相接逻辑逐列归位。

配套：
- `clean_xml` 的空 grid 删除已泛化到**任意列数**（`<grid>(<column…></column>)+</grid>`），避免 3 列及以上残留空 grid。
- `04_verify.py` 的 `verify_grids` 核验源「并排图 grid 数」== 新文档「列内含图 grid 数」。

### 已知差异

- **width-ratio**：`block_insert_after` 建 grid 时飞书强制等宽（忽略 XML 里的 width-ratio）。**已修复**：`rebuild_grids` 在图片就位后用原生 API `update_grid_column_width_ratio`（整数百分比，最大余数法凑 100）二次设置列宽，可还原非等宽布局（±1% 舍入）。
- 支持每列 1 张或多张图（纯图片列）；含**正文文字**的图文混排列/文本列不自动重建（这类 grid 在 create 时本就能保留）。
- `04_verify.py` 的 `verify_grids` 除核验「grid 数」外，还核验「列内图片总数」——grid 数对上但列内图少了 → 多图列没还原完整，会专门告警。

---

## 限制 17：连续堆叠图片，第二张及之后定位丢失（**已修复**）

### 现象

源文档里多张图竖直堆叠在同一段文字下（`文本 → 图A → 图B`），复制后第一张（图A）位置正确，但第二张（图B）漂到无关章节（实测：两张「行业流量大盘」中的第二张跑到了「多账号」问答下）。

### 原因

`compute_image_anchors` 找图片「前驱 top-level block」时，`is_anchorable_top` 只跳过空 `<p>`，**没跳过 `img`**。于是图B 的前驱被取成图A（一个 img），而后续分支只处理 `p/h/callout/grid/ol/ul`，**没有「前驱是 img」的分支** → 图B 得到 `anchor=None` → `move_images` 不移动它 → 图B 留在 media-insert 的上传位置（文档末尾）。任何含 2+ 连续堆叠图的文档都会复现。

### 解决方案（已实现）

`is_anchorable_top` 增加 `if blk["tag"] == "img": return False`，跳过前面的 img，让同组图片都锚到上游同一个文本块。`move_images` 对同 anchor 多图按**反向源文档顺序**移动，顺序自然正确（`文本, 图A, 图B`）。

---

## 限制 18：图片下载/上传瞬时失败导致静默漏图（**已加重试**）

### 现象

递归扒取大文档（图多、跨租户）时，偶发漏掉某张图（实测：得物文档 18 张漏 1 张），最终文档图片数比源文档少。

### 原因

`download_image`（`+media-preview`）和 `upload_images`（`+media-insert`）原本都是**单次尝试**：失败只 append 到 `failed` 打条警告，失败的 token 仍留在 `img_tokens` 但本地无文件 → 上传阶段 `continue` 跳过 → 图片静默丢失。批量场景下网络/限流瞬时抖动迟早触发。

### 解决方案（已实现）

`download_image` 和 `upload_images` 的 media-insert 均加**有限次重试**（默认 3 次，下载带退避 + 非空文件校验）。`04_verify.py` 的 `verify_images` 仍核验源/新图片数，作为兜底告警；递归子调用若仍漏图，按以下步骤手动补：下载该 token → media-insert → block_move_after 到正确锚点 → 设回源 scale。

---

## 限制 19：图紧跟顶级标题后 → 无 anchor 漂到文末（**已修复**）

### 现象

源文档常见 `## 标题\n图` 或 `## 标题\n空行\n图`——图紧跟在**顶级 heading** 后、之间无正文段落。复制后这类图全部漂到文档**末尾**（实测：「多账号管理工具」文档「代理服务器」图、「软件登录」标题后首图被甩到文末「下载地址」章节，其余图整体前移补位）。

### 原因

`compute_image_anchors` 找图的「前驱 top-level block」后，按 `pred.tag in MAPPABLE_TOP` 决定走 direct 模式。早期 `MAPPABLE_TOP` 只含 `p/callout/blockquote/pre`、**漏了 heading**（错误假设「顶级 heading 不会成为图的直接前驱」）。于是前驱是 heading 的图匹配不到任何分支 → 落入 fallback 且无 anchor → 留在 media-insert 上传位置（文末）。

### 解决方案（已实现）

`MAPPABLE_TOP` 加入 `h1`–`h9`，这类图走 direct 锚到 heading（`blank_gap` 机制照常保留图前空行）。注意区分：这是**顶级 heading 作前驱**（heading 与图是同级兄弟）；折叠标题内**嵌套**的图（heading 作父容器、图是其 child）是另一回事，由 `move_nested_images` 处理。

---

## 限制 19.1：block_move_after「src 在锚点之前会越位一格」（手动补救必读）

### 现象

手工用 `block_move_after(anchor, src)` 补救顺序时实测：**当 src 当前位置在 anchor 之前**（把一个靠前的块往后挪到 anchor 后面）时，src 不紧贴 anchor，而是落到「anchor 的下一个块」之后（越位一格）。连续几次这种「回挪」会让块一路漂移到无关位置。

### 定性

这是 `block_move_after` 落点不可靠家族的**第三种形态**（前两种见限制 5.3：①anchor 后紧跟空 p；②src 本身是空 p）。共同结论：**block_move_after 落点不可靠，每次调用后必须 fetch 重新核验实际落点，不能假设「紧贴 anchor」。**

### 更稳的手工补救手法（按可靠性排序）

1. **能用 children-create API 就别用 block_move_after**：`POST .../blocks/{parent}/children` 带显式 `index`，落点确定（重建目录组件、补空行都用这个）。空段落不能用 `block_type 2 + 空 elements`（报 `invalid param`），空行改用 `docs +update block_insert_after --content '<p></p>' --doc-format xml`。
2. **挪顺序优先「正向移动」**：要让 A 在 B 前，与其把 A 往前挪，不如把 B 往 A 后挪（src=B 在 anchor=A 之后，落点更可预期）。
3. **调空行用「删旧 + 插新」而非移动空 p**（见限制 5.3），锚点尽量选**非容器**块；必须锚 grid 等容器时插完立刻核验，越位了再补正。

---

## 限制 20：文档封面（顶部背景图）丢失（**已自动还原**）

### 现象

飞书文档顶部的背景图是 docx **文档级属性**（`document.cover` = token + offset_ratio），**不在 XML body 里**——`docs +fetch` 取不到、`docs +create` 也不复制，扒取重建后新文档顶部背景图整块消失。原生 `drive files copy` 路径天然保留封面，本问题只出现在扒取重建兜底路径。

### 解决方案（已实现：`migrate_cover`，第 7.9 步）

1. `GET /docx/v1/documents/{id}` 读源 `document.cover`（token + offset_ratio_x/y）。
2. **跨租户用 `docs +media-preview --token` 下载封面图**——`docs resource-download --type cover` 跨租户 403（与普通图片同坑），必须走 media-preview workaround。
3. `docs resource-update --type cover --file ... --offset-ratio-x/y` 上传并设为新文档封面，带回原 offset。

`04_verify.py` 的 `verify_cover_and_addons` 对比源/新 `document.cover` 是否都存在。源封面跨租户无读取权限时跳过并告警。

---

## 限制 21：ISV 组件块（目录等 add-ons）丢失（**已自动还原**）

### 现象

飞书「目录」等第三方/扩展组件是 docx **`block_type 40`（add_ons）**，带 `component_type_id` + `record`（如目录组件 `blk_637dcc698597401c1a8fd711`）。`docs +create` 把它们**静默丢弃**——XML 里只剩一个无内容的 `<readonly-block type="isv">`（丢了 component_type_id/record），光看 XML 既不知道是什么组件、也无法重建（实测：「🧭目录」「skill目录」两处目录组件整块消失）。

### 解决方案（已实现：`migrate_addons`，第 7.95 步，须用原生 blocks API）

1. `GET .../blocks` 列出源所有 `block_type 40`，拿每个的 `component_type_id` + `record` + 位置（parent + 前驱同级）。
2. 锚点 = 最近的**已映射前驱同级块**；新父 = 源父（页面根 → 新文档根，否则走 mapping）。
3. `POST .../blocks/{parent}/children`，`index` = 锚点在新父 children 中的位置 +1，body 用 `{"block_type":40,"add_ons":{component_type_id, record}}` 重建。

**两个坑**：
- 必须用原生 children-create API 重建，**不能用 XML `block_insert_after`**（XML 的 `<readonly-block>` 不带 component_type_id/record，飞书重建不出组件）。
- **同一文档可能有多个同类型组件，但 `record` 各不相同**（实测：两处目录组件一个 `isShowAllLevel:true`、一个 `false`）——必须逐块带各自的 `record`，不能复用第一个。

`04_verify.py` 的 `verify_cover_and_addons` 数源/新 `block_type 40` 数量核验。

---

