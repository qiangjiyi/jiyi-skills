# 有序列表序号和嵌套结构修复规范

本文档是 `SKILL.md` 第 8 步「有序列表序号修复」和第 11 步「嵌套结构修复」的详细操作规范。

---

## 背景与教训

### 教训 1：飞书 create 时把所有 li 的 seq 设为 "1"

执行 `docs +create` 后，所有 `<li>` 标签的 seq 属性都会被飞书强制设为 `"1"`，导致：

- 列表显示为「1. 1. 1. 1.」而不是「1. 2. 3. 4.」
- 这是**批量问题**（通常涉及几十到上百个 li）
- 必须显式给每个 li 设置正确的 seq 值

### 教训 2：`seq="auto"` 在飞书 API 中无效

尝试用 `seq="auto"` 让飞书自动递增，但飞书 API 会忽略这个值，仍然存为 `"1"`。

**结论**：必须显式给每个 li 设置 `seq="1"`, `seq="2"`, `seq="3"` 等具体数字。

### 教训 3：嵌套结构（ol 内的 ol）容易丢失

源文档中有些 li 包含嵌套 ol/ul，在清理和重建过程中，这些嵌套 li 容易被错误地删除或丢失。

---

## 第 8 步：有序列表序号修复

### 8.1 解析源文档的 ol 结构

遍历源文档所有 `<ol>` 标签，提取每个 li 的：
- 源 block ID
- 源 seq 值（可能为 None、None、"1"、"3"、"6" 等）
- 在 ol 中的位置（1-indexed）

### 8.2 计算期望 seq 值（混合计数器，见 `_compute_expected_seqs`）

不是简单的「位置即 seq」，而是混合模型（详见 `api-limitations.md` 限制 13）：

- 每个独立 ol 看「第一个 li 是否有显式 seq」决定模式：有显式 → per-ol 计数器
  从 0 开始；无 → 从上一个 ol 的 `last_seq+1` 续号（隐式续号）。
- 嵌套 ol 独立从 1 编号。
- **独立列表发现用文档顺序前序遍历**，收集所有「无 ol 祖先」的 ol——不能只用
  `root.findall("ol")`，否则会漏掉被 `<p>`/`<callout>` 包裹的列表。
- **期望值按「文本 → seq 列表（文档顺序）」存储**，应对同一文本（如多处「……」）
  在多个列表重复出现，避免纯文本 key 互相覆盖。

### 8.3 在新文档中按文档顺序匹配 li

`fix_list_seq` 按文档顺序遍历新文档所有 `<li>`，用 li 文本前缀查期望 seq 列表，
对同名文本**逐个消费**（第 N 个该文本的 li → 列表第 N 个 seq）。靠文本匹配而非
第 4 步的 ID 映射，因为 block_replace 后 li 的 block ID 会变。

### 8.4 用 block_replace 修复 seq

```bash
# 对每个 li 单独执行 block_replace
lark-cli docs +update --api-version v2 --doc "<new-doc-id>" \
  --command block_replace \
  --block-id "<new li block ID>" \
  --content '<li seq="<期望 seq 值>"><li 的内容></li>'
```

### 8.5 处理特殊情况

#### 情况 A：嵌套 ol 内 li 的 seq

源结构：
```xml
<ol>
  <li seq="1">被这两个用户点击、点赞等行为的笔记，有很大重合
    <ol>
      <li seq="1">eg.  两个用户都经常点击《盗墓笔记》...</li>
    </ol>
  </li>
  <li seq="2">两个用户的关注作者有很大重合</li>
</ol>
```

- 外层 li seq="1" → 正常
- 嵌套 li seq="1" → 飞书会自动渲染为「a.」（字母编号），保留为 seq="1" 即可

#### 情况 B：嵌套 li 被错误删除

如果发现源文档的 ol 有 4 个 li，但新文档的 ol 只有 2 个 li，说明嵌套 li 被删除了。需要用 `block_replace` 恢复嵌套结构：

```python
# 找到外层 li，替换为包含嵌套 ol 的版本
new_outer_li = f'''<li seq="1">
  外层 li 内容
  <ol>
    <li seq="1">嵌套 li 1</li>
    <li seq="1">嵌套 li 2</li>
    ...
  </ol>
</li>'''
```

### 8.6 验证修复效果

```python
# 重新 fetch 新文档，检查所有 ol 的 seq 分布
# 期望：所有多项目 ol 都是 1, 2, 3... 递增
# 期望：所有 ol 中无 "all 1" 的情况
```

---

## 第 11 步：嵌套结构修复

### 11.1 检测缺失的嵌套 li

对比源文档与新文档的所有 ol：

```python
src_ols = parse_ols(source_xml)
new_ols = parse_ols(new_xml)

for src_ol, new_ol in zip(src_ols, new_ols):
    # 对比每个 ol 的总 li 数（递归计数，包括嵌套）
    src_total = count_lis_recursive(src_ol)
    new_total = count_lis_recursive(new_ol)
    
    if src_total != new_total:
        print(f"Nested li missing in ol")
        # 进一步分析哪些 li 缺失
```

### 11.2 常见嵌套结构丢失场景

#### 场景 A：嵌套 ol 整体丢失

源结构：
```xml
<ol>
  <li seq="1">简介：<ul>
    <li>@小号指路：...</li>
    <li>引导关注群聊：...</li>  ← 可能丢失
    <li>引导看收藏夹：...</li>  ← 可能丢失
    <li>引导看小红书号：...</li>  ← 可能丢失
  </ul></li>
</ol>
```

**症状**：新文档的外层 li 只剩下 1 个嵌套 li（@小号指路），其他 3 个丢失。

**修复**：用 `block_replace` 把外层 li 替换为完整版本：

```bash
lark-cli docs +update --api-version v2 --doc "<new-doc-id>" \
  --command block_replace \
  --block-id "<外层 li new ID>" \
  --content '<li seq="1"><b>简介：</b><ul><li>...</li><li>...</li>...</ul></li>'
```

#### 场景 B：嵌套 ol 的第一个 li 被错误复制到外层（2026-06-17 用户报告）

**症状**：新文档的外层 ol 出现 2 个 li，第一个 li 包含完整的嵌套 ol（正确），但同时外层 ol 还有一个**额外的 li**，其文本与嵌套 ol 的第一个 li 完全相同。

源结构：
```xml
<ol>
  <li seq="1">
    <b>基础设置：</b>
    <ol>
      <li seq="1">大号收藏小号的2个笔记...</li>   ← 嵌套项
      <li>小红书ID改成微信号...</li>
      <li>建立小红书群...</li>
    </ol>
  </li>
</ol>
```

新文档错误地变成：
```xml
<ol>
  <li seq="1">
    <b>基础设置：</b>
    <ol>
      <li seq="1">大号收藏小号的2个笔记...</li>   ← 嵌套项（正确）
      <li>小红书ID改成微信号...</li>
      <li>建立小红书群...</li>
    </ol>
  </li>
  <li seq="1">大号收藏小号的2个笔记...</li>      ← 重复（错误）
</ol>
```

**根因**：飞书 `docs +create` 在解析 li 内的嵌套 ol 时，**把嵌套 ol 的第一个 li 的内容"提升"成外层 ol 的一个顶级 li**（同时保留嵌套 ol）。这是一个已知的飞书解析器行为，不是我们 skill 的代码 bug。

**修复**：手动 `block_delete` 删除那个**重复的顶级 li**（保留带 `<b>基础设置：</b>` 的那个外层 li）：

```bash
lark-cli docs +update --api-version v2 --doc "<new-doc-id>" \
  --command block_delete \
  --block-id "<重复 li 的 ID>"
```

**检测方法**：用 `04_verify.py` 的 `verify_duplicate_li`（限制 12 已经覆盖此场景）。或在 source vs new 对比时检查：每个外层 ol 的顶级 li 数量是否与源文档一致（不计嵌套 li）。

### 11.3 恢复后重新核验

修复完嵌套结构后，重新执行：

1. 第 9 步的文字内容核验（确保文字 100% 一致）
2. 第 10 步的图片位置核验（图片位置可能受嵌套结构影响）
3. 第 9.5 步的 ol 分离核验（避免合并回归）

---

## 完整执行流程

```
第 8 步:
  解析源 ol → 计算期望 seq → block_replace 修复 → 验证

第 11 步（如果第 9 步发现文字缺失）:
  对比 ol 结构 → 找到缺失的嵌套 li → 补充嵌套内容
  → 重新执行第 9 步核验

第 8 步（再次执行，修复嵌套 li 的 seq）:
  嵌套 ol 内的 li 也要设 seq
  → 重新执行第 10 步图片位置核验
```

---

## 关键经验

1. **永远不要用 `seq="auto"`**：飞书 API 会忽略这个值
2. **不要用 `block_replace` 一次性替换整个 ol**：这会导致 ol 内所有 li 失去 ID，需要逐个 li 替换
3. **清理图片时不要删除 ol/ul 容器本身**：只删除 ol 内的图片标签，保留 ol 结构
4. **嵌套结构修复后要重新核验**：因为 li 数量变了，图片位置可能也变了
5. **block_replace 后 block ID 会变**：每次替换后要重新 fetch 获取新 ID

---

## 关键源文档参考模式

源文档中常见的 ol/ul 结构：

```xml
<!-- 单层列表 -->
<ol>
  <li seq="1">item 1</li>
  <li seq="2">item 2</li>
</ol>

<!-- 嵌套列表（li 内含 ol） -->
<ol>
  <li seq="1">外层 item 1
    <ul>
      <li>嵌套 item 1</li>
      <li>嵌套 item 2</li>
    </ul>
  </li>
  <li seq="2">外层 item 2</li>
</ol>

<!-- 显式 seq 跨越（用于显示非连续编号） -->
<ol>
  <li seq="3">继续上一段的编号</li>
</ol>
<ol>
  <li seq="6">继续跨段编号</li>
</ol>
```

新文档要尽量保持这种结构。
