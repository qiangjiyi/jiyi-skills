# 图片定位详细规范

本文档是图片定位（`03_post_process.py` 的 `compute_image_anchors` / `move_images` / `move_nested_images`）与图片位置核验（`04_verify.py`）的详细操作规范，对应 `SKILL.md` 工作流的「图片定位」环节。

---

## 背景与教训

在飞书文档复制过程中，图片定位是**最容易出错**的环节之一。我们从实际执行中总结出以下几个核心教训：

### 教训 1：飞书 create 后所有图片会被堆到文档末尾

`docs +create` 只能接受文本内容（XML），图片无法在创建时直接插入。所有图片必须先用 `+media-insert` 上传到文档末尾，然后再移动到正确位置。

### 教训 2：anchor 选错会导致图片位置错乱

很多情况下，图片"前面"的文本块并不是正确的 anchor。例如：
- 图片前面是 `<ol>` 时，anchor 应该是 ol 的**最后一个 li**，不是 ol 前面的文本
- 图片在 `<grid>` 容器内时，anchor 是 grid **前面**的文本块（不是 column，因为 column 在新文档中不存在）

### 教训 3：相同 anchor 的多张图片需要反向移动

如果两张图片有相同的 anchor，按正向顺序移动会导致顺序错乱（后面的图片反而排到了前面）。必须按**反向文档顺序**移动。

---

## 第 7 步详细执行

### 7.1 准备工作

1. 已上传所有图片，获取了每个图片的新 block_id（记为 `image_new_id`）
2. 已建立 source → new 的 block ID 映射

### 7.2 为每张图片计算正确的 anchor

遍历源文档所有 `<img>` 标签，按以下步骤计算每张图片的 anchor：

```
对每张源图片:
  1. 在源文档中找到图片的"前一个 top-level block"
  
  2. 如果前一个 block 是 ol/ul:
     anchor = ol/ul 中最后一个 li 的 new ID
  
  3. 如果前一个 block 是 p/h1/h2/h3/callout/blockquote:
     anchor = 该 block 的 new ID
  
  4. 如果图片本身在 grid 内:
     anchor = grid 前面那个 block 的 new ID（grid 本身在新文档中不存在）
  
  5. 如果图片本身在 ol/ul 嵌套的 li 内:
     anchor = 该嵌套 li 的 new ID（如果有的话）
     如果没有合适 anchor，使用外层 li
```

### 7.3 关键场景详解

#### 场景 A：图片在 `<ol>` 后面

源结构：
```xml
<p>XXX</p>
<ol>
  <li>item1</li>
  <li>item2</li>
  <li>item3</li>  ← 这是图片的 anchor
</ol>
<img ... />        ← 图片
<p>YYY</p>
```

✅ 正确 anchor：`<li>item3</li>` 的 new ID
❌ 错误 anchor：`<p>XXX</p>` 的 new ID

**原因**：如果用 `<p>XXX</p>` 作为 anchor，图片会被放到 `<ol>` 之前，破坏列表结构。

#### 场景 A.1：图片在两个 `<ol>` 之间（带短过渡文本）

源结构（图片插在两个有序列表中间，中间是短过渡文本）：
```xml
<ol>
  <li>item1</li>
  <li>item2</li>
  <li>item3</li>  ← 这是图片的 anchor（**第一个 ol 的最后 li**）
</ol>
<img ... />        ← 图片
<p>其他赛道一样</p>  ← 短过渡文本（≤20字）
<ol>
  <li>itemA</li>
  <li>itemB</li>
</ol>
```

✅ 正确 anchor：`<li>item3</li>`（**第一个** ol 的最后 li）
❌ 错误 anchor：`<li>itemB</li>`（第二个 ol 的最后 li）
❌ 错误 anchor：`<p>XXX</p>`（第一个 ol 之前的文本）

**原因**：源文档里图片的物理位置就是它的语义位置。不能因为后面又来了个 ol 就猜测图片"其实属于后面的 ol"。

**反模式（不要写）**：看到「ol1 + 图片 + 短p + ol2」就启发式地把图片放到 ol2 之后。本 skill 历史上有过这个 bug（见 `scripts/03_post_process.py` 的「关键场景 3.5」），导致赛道组合图被错放到第二个 OL 之后。已删除。

#### 场景 B：多张图片共用同一 anchor

源结构：
```xml
<p>XXX</p>
<ol>
  <li>item1</li>
  <li>item2</li>
</ol>
<img ... />     ← 图片 1
<img ... />     ← 图片 2
<p>YYY</p>
```

如果两张图片都用 `<li>item2</li>` 作为 anchor，**必须按反向顺序移动**：
1. 先移动图片 2（让它在 item2 之后）
2. 再移动图片 1（让它也在 item2 之后，但在图片 2 之前）

**原因**：`block_move_after` 是"插入到 anchor 之后"，所以后插入的会排在前面。如果按正向顺序移动，会导致图片 2 跑到图片 1 前面。

#### 场景 C：图片在 `<grid>` 容器内

源结构：
```xml
<p>XXX</p>
<grid>
  <column>
    <img ... />   ← 图片 1
  </column>
  <column>
    <img ... />   ← 图片 2
  </column>
</grid>
<p>YYY</p>
```

✅ 正确 anchor：`<p>XXX</p>` 的 new ID
❌ 错误 anchor：column 的 ID（column 在新文档中不存在）

grid 在新文档中已被移除（因为只包含图片），所以图片 1 和图片 2 都应该用 grid 前面的文本作为 anchor。

---

## 7.4 执行代码骨架

实际实现见 `scripts/03_post_process.py` 的 `compute_image_anchors`（计算每张图的
mode + anchor）与 `move_images`（执行）。核心是**按反向源文档顺序逐张移动，且
所有 block_move_after 的 anchor 都用 top-level block**（规避 5.1 容器末项陷阱）：

```python
for plan in reversed(image_plans):       # 反向源文档顺序
    img = uploaded[plan['orig_token']]
    if plan['mode'] == 'two_step':        # 前驱是 ol，用后继 p 做枢轴
        move_after(plan['anchor'], img)   #   ol, p, img
        move_after(img, plan['anchor'])   #   ol, img, p
    else:                                  # direct / fallback
        move_after(plan['anchor'] or plan['fallback'], img)
```

mode 判定（前驱 top-level block，计算时跳过空 `<p>`）：p/h/callout/blockquote 或
grid → `direct`；ol/ul 且有非空 p 后继 → `two_step`；ol/ul 无后继 → `fallback`
（ol 末 li，可能越位）。详见 `api-limitations.md` 限制 5.1 的表格。

---

## 第 10 步：图片位置核验

### 10.1 签名提取

对每张图片，提取**前后各 1-3 个文本块**作为 signature：

```python
def get_image_signature(img_in_doc, radius=1):
    """img_in_doc 是图片在文档中的位置信息"""
    prev_texts = []
    next_texts = []
    
    for block in reversed(img_in_doc['prev_blocks'][:radius]):
        if block['tag'] in ('p', 'h1', 'h2', 'h3', 'callout', 'blockquote'):
            prev_texts.append(block['text'][:50])
    
    for block in img_in_doc['next_blocks'][:radius]:
        if block['tag'] in ('p', 'h1', 'h2', 'h3', 'callout', 'blockquote'):
            next_texts.append(block['text'][:50])
    
    return {
        'prev': ' || '.join(prev_texts),
        'next': ' || '.join(next_texts),
    }
```

### 10.2 核验流程

```python
# 1. 提取源文档每张图片的 signature
src_sigs = {}
for img in source_images:
    src_sigs[img['orig_token']] = get_image_signature(img)

# 2. 提取新文档每张图片的 signature
new_sigs = {}
for img in new_images:
    new_sigs[img['orig_token_via_name_attr']] = get_image_signature(img)

# 3. 对比
for token, src_sig in src_sigs.items():
    new_sig = new_sigs.get(token)
    if src_sig != new_sig:
        print(f"MISMATCH: {token[:20]}")
        print(f"  src: {src_sig}")
        print(f"  new: {new_sig}")
```

### 10.3 关键：图片在跨文档迁移后的 token 匹配

上传到新文档后，每张图片会获得新的 src token，但 **name 属性保留原 token**（如 `name="FnsvbAkHpoQS2kxg9lZcARADny0.png"`）。

因此核验时应该用：
- 源文档：`<img src="<orig_token>"` 用 src 匹配
- 新文档：`<img name="<orig_token>.png"` 用 name 匹配

---

## 常见问题排查

### Q1：图片位置看起来"差一位"

**症状**：图片在新文档中比预期位置早或晚了一个段落。

**可能原因**：anchor 选错（用了 ol/ul 前的文本，而不是 ol/ul 的最后一个 li）。

**排查**：检查源文档中图片前的最后一个 top-level block 是什么。

### Q2：多张图片顺序颠倒

**症状**：源文档中图片 A 在图片 B 前面，新文档中 B 反而在 A 前面。

**可能原因**：相同 anchor 的图片没有按反向顺序移动。

**修复**：重新执行第 7 步，按反向顺序移动。

### Q3：grid 内的图片位置异常

**症状**：原本在 grid 内的图片，移到了 grid 前后文本块的错误位置。

**可能原因**：anchor 用错了（用了 column 而不是 grid 前面的文本）。

**修复**：grid 内的图片，anchor 应该是 grid 前面那个 block。

---

## 必须执行的核验

完成第 7 步后，**必须**执行第 10 步的签名核验：

- 如果 0 张图片 signature 不一致：✅ 完成
- 如果有图片 signature 不一致：找到这些图片，重新计算 anchor 并移动，然后再次核验

**重要**：不允许跳过核验步骤直接交付。
