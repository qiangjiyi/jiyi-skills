---
name: jiyi-little-dancer-illustrations
description: 生成带有“小舞伴”个人 IP 的中文正文配图。用于用户要求为中文文章、帖子、博客、Notion 文档、工作流、方法论、流程、结构、状态、隐喻或观点生成“手绘”“舞蹈感”“个人 IP 配图”“文章插图”“配图建议”“shot list”“去标题/改图”等任务；默认使用原创手绘小女孩角色、纯白留白、粉色与少量橙蓝红批注。
---

# 吉义小舞伴怪诞正文配图

## 核心定位

为中文文章设计和生成 16:9 横版正文配图。目标不是做商业插画、PPT 信息图或写实儿童肖像，而是把文章里的关键判断、流程、结构、状态或隐喻，变成一张清爽、轻怪诞、有舞蹈身体语言的手绘解释图。

默认视觉 IP 是“小舞伴”：一个由用户参考照片和舞蹈视频抽象出的原创手绘小女孩角色。她有深色头发、双侧小马尾或舞动发束、头顶蝴蝶结/发饰、明亮大眼、粉色系服装和灵活的身体姿态。她不是照片复刻，也不是站在角落里的装饰，而是正在用身体参与认知结构的活跃角色。

## IP 一致性硬约束

小舞伴的角色身份必须以 `assets/little-dancer-reference-sheet.png` 和
`references/little-dancer-ip.md` 为唯一来源。参考图不是背景素材，也不是只在文字里提到的灵感；生成时必须作为真实的视觉参考输入传给图像后端。

不可变化的身份锚点包括：深色双侧小马尾、头顶浅粉蝴蝶结发带与小发髻、自然圆脸和中等偏大的明亮眼睛、浅粉短袖上衣上的蝴蝶结图案、浅粉长裤上的橙色爱心、白色运动鞋，以及略带不规则的黑色手绘线条。每张图只能改变动作、表情、视线、重心和认知隐喻，不得重新设计发型、发饰、脸、服装或鞋子。

如果任务来自 `wechat-article-production`，优先使用 package 内的
`illustrations/ip-reference.png` 作为参考图，并在 handoff 中记录它。只把文件路径写进提示词不算传入参考图；如果当前图像后端不支持带参考图的新图生成，必须改用该后端支持的编辑/变体流程，或将任务标记为未完成，不能用普通文字生图冒充完成。

## 先读这些参考

按任务需要读取，不要一次塞满上下文：

- `references/little-dancer-ip.md`：外形锚点、性格、动作、表情和禁忌。
- `references/motion-library.md`：从舞蹈视频提炼出的身体动作、节奏和情绪词典。
- `references/style-dna.md`：风格 DNA、颜色、文字、儿童友好约束和禁忌。
- `references/composition-patterns.md`：结构类型、原创隐喻方法和反复刻规则。
- `references/prompt-template.md`：单张生图和编辑提示词模板。
- `references/qa-checklist.md`：生成后检查和迭代规则。
- `references/config/preferences-schema.md`：图像后端配置字段、解析优先级和失败约束。
- `assets/little-dancer-reference-sheet.png`：角色和动作校准图，只用于保持外形与动作一致，不要照抄构图。

## 图像后端配置与执行

小舞伴负责文章分析、构图、完整提示词、参考图和 QA；选定的图像后端负责实际生图、参考图传递、比例参数、重试和运行日志。配置规则见 `references/config/preferences-schema.md`。

本次执行先用以下脚本解析 `IMAGE_BACKEND`，并把 JSON 结果写入本次 handoff 或运行记录：

```bash
node <THIS_SKILL_ROOT>/scripts/resolve-image-backend.mjs \
  --project-dir <PROJECT_DIR> \
  [--requested <CURRENT_REQUEST_BACKEND>]
```

它优先使用当前请求中的后端 override，其次按 `references/config/preferences-schema.md` 读取 `.jiyi-skills` 配置，最后使用默认值 `auto`。当上游是 `wechat-article-production` 时，不读取也不接受父级统一后端；小舞伴只依据自己的配置和当前请求解析。

- `IMAGE_BACKEND` 必须通过当前运行时的原生 `Skill` 工具调用；
- 不要直接运行选定后端的内部脚本，也不要让主 Agent 模拟后端流程；
- 固定后端不可用时，立即将本次配图标记为 `blocked`，不得自行切换或手工补图；
- 只有显式配置为 `auto` 时，才允许运行时选择可用的原生图像 Skill，并在 handoff 中记录实际后端；
- 每次调用都要把最终 prompt 文件、输出路径、比例和 `illustrations/ip-reference.png` 作为明确输入传给 `IMAGE_BACKEND`。

当本 Skill 被 `wechat-article-production` 通过原生 `Skill` 调用时，父流程只是调度者。本 Skill 不得把 Outline、Prompt、图片或 handoff 的写入交回父流程；父流程也不得读取本文件后模拟这些步骤。后端或文件操作失败时，返回结构化失败原因，不能用“图片已生成”代替完整交付。

## 工作流

### 1. 消化正文

先读用户给的正文、链接、Notion 页面、Markdown 文件、截图或主题。提炼：

- 核心观点是什么；
- 哪些段落承担认知转折；
- 哪些内容适合用图解释；
- 哪些地方只适合文字，不需要图。

不要平均配图。优先选择认知锚点，例如核心判断、两个断点、输入输出闭环、分流、前后对比、状态变化、常见坑或承接路径。

### 2. 先出配图策略

如果用户只是说“分析怎么配图 / 思考哪些地方需要配图”，先给 shot list。每张图必须使用 `## Shot NN — ...` 标题，并逐项写清楚：

- 放在哪个段落后；
- 图的主题；
- 核心意思；
- 结构类型；
- 小舞伴在做什么动作；
- 小舞伴是什么表情和身体状态；
- 建议元素；
- 建议中文标注词。

不得只写三句概括或把多个镜头合并成一段。`outline.md` 的每个 Shot 必须包含以下字段：

```markdown
## Shot 01 — 一句话主题

- 放置锚点：正文中出现的精确短语
- 主题：这张图讲什么
- 核心意思：读者看完后应理解的判断
- 结构类型：概念隐喻 / 前后对比 / Workflow / 系统局部 / 角色状态 / 方法分层 / 地图路线 / 小漫画分镜
- 小舞伴动作：一个来自 motion-library.md 的主动作
- 表情与身体状态：一个主表情、重心、身体朝向
- 视线方向：她具体看向什么
- 建议元素：3-5 个物件及其关系
- 中文标注词：3-5 个短词
- 构图与信息流：画面空间关系、左右方向和视觉动线
```

写完 Outline 后必须先运行：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-outline.mjs \
  --file <PACKAGE_DIR>/illustrations/outline.md \
  --report-file <PACKAGE_DIR>/illustrations/outline-validation.json
```

校验失败时不得开始写 Prompt 或生图。`outline-validation.json` 是内部校验记录，不作为对外 handoff 的替代。

默认 4-8 张。文章很短时 1-3 张；长文也不要轻易超过 9 张。

### 3. 单张生成

如果用户明确要求“生成 / 输出 / 做图 / 帮我生成”，每张图单独生成，不要把多张图拼在一起。

每张图生成前，必须按 `references/prompt-template.md` 的完整结构写入独立 prompt 文件，至少保留 Visual DNA、IP identity lock、Primary movement、Expression、Gaze direction、Theme、Structure type、Core idea、Composition、Suggested elements、Chinese handwritten labels、Color use、Constraints 和 Reference handling。不能把这些部分压缩成几段没有字段名的自然语言。

写完本轮所有 prompt 后，先运行：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-prompts.mjs \
  --prompt-dir <PACKAGE_DIR>/illustrations/prompts \
  --reference <PACKAGE_DIR>/illustrations/ip-reference.png \
  --report-file <PACKAGE_DIR>/illustrations/prompt-validation.json
```

脚本失败时不得开始生图；修正 prompt 后重新校验。

校验通过后，使用原生 `Skill` 工具逐张调用 `IMAGE_BACKEND`，传入 prompt 文件、输出路径、`--ar 16:9` 和真实参考图。不要只把参考图路径写进 prompt。当 `image_concurrency` 配置大于 1 且当前运行时支持并发调用同一后端时，可在校验全部通过后按该并发数同时发起多张图的生成；每张图仍必须使用各自的完整 prompt 文件与参考图，输出命名和 handoff 记录不受并发影响。后端限流或任一张失败时，回退为串行只重试失败项。

每张图只讲一个核心结构。提示词必须包含：

- 16:9 横版中文正文配图；
- 纯白背景；
- 黑色手绘线稿和轻微抖动；
- 少量粉色、橙色、蓝色或红色手写批注；
- 大量留白；
- 小舞伴作为核心动作主体；
- 一个来自 `motion-library.md` 的动作和一个明确表情；
- 禁止 PPT、商业插画、写实儿童肖像、复杂架构、左上角类型标题。

不要把用户照片中的背景、长椅、院落、成人陪同者、真实品牌或精确服装图案复制进画面。参考素材只用于抽象角色锚点和身体语言。

不要复刻旧案例。每次都从当前文章重新发明一个奇怪但成立的低科技隐喻，让小舞伴用动作与物件共同解释观点。

### 4. 检查与迭代

生成后读取 `references/qa-checklist.md`，但不要使用 `Read` 一次性加载一张或多张完整 PNG，也不要并行读取全部图片做视觉 QA。完整 raster 会以 base64 进入上下文，可能在 handoff 前把当前会话撑满。

先运行确定性的图片门禁；它只读取图片文件头、尺寸和文件大小，不会把图片内容放进 Agent 上下文：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-images.mjs \
  --package-dir <PACKAGE_DIR> \
  --expected-count <OUTLINE_SHOT_COUNT>
```

它会检查生成数量、顺序命名、PNG 可读性和 `16:9` 比例，并写入
`illustrations/image-validation.json`。失败时只重写提示词并重生成失败图片，然后重新运行校验；通过后立即执行下一节的 `finalize-handoff.mjs`，不要再批量读取原图。

图片中的角色锚点、动作和语义质量由 Outline、完整 Prompt、参考图传递证据和用户后续视觉抽查共同保证；如果运行时确实提供必须的视觉复核，只能逐张、低频执行，不能并行加载全部 PNG，也不能把视觉读取当作 handoff 前的必需批处理步骤。

如果中文错字严重，减少标注词并重生成；不要用程序覆盖已经生成的图片文字。

### 5. 保存交付

如果用户在 workspace 内工作，把最终图复制到：

```text
assets/<article-slug>-illustrations/
```

按顺序命名：

```text
01-topic-name.png
02-topic-name.png
```

在流水线 package 中，保留 `illustrations/ip-reference.png`、每张最终提示词、
`illustrations/image-validation.json` 和 `illustrations/illustration-handoff.json`。图片和 QA 完成后，必须使用确定性脚本生成文章交接文件，不要手写两个容易漏字段的 JSON/Markdown：

```bash
node <THIS_SKILL_ROOT>/scripts/finalize-handoff.mjs \
  --package-dir <PACKAGE_DIR> \
  --source-file formatted.md \
  --reference-source <THIS_SKILL_ROOT>/assets/little-dancer-reference-sheet.png \
  --backend <ACTUAL_BACKEND_SKILL>
```

脚本会再次读取机器图片校验、检查 Outline、Outline 校验报告、Prompt 校验、参考图、图片比例和文章锚点，然后原子写入 `article-illustrated.md` 与 `illustrations/illustration-handoff.json`。任一检查失败都必须返回失败，不得报告完成。

handoff 顶层必须记录：

- `reference_asset.source`：实际参考图来源；
- `reference_asset.package_path`：package 内的参考图路径；
- `reference_asset.used_for_identity`：必须为 `true`；
- `identity_anchors`：本次 QA 实际检查过的固定锚点列表。
- `image_backend.skill`：记录本次实际使用的后端 Skill；
- `image_backend.per_image`：数组；逐张记录 `prompt_file`、`output`、实际后端 `skill`、比例 `aspect` 和 `reference_used_for_identity`。后端由本 Skill 自己解析，不能由父级 manifest 统一指定。
- `outline_validation`：引用 `illustrations/outline-validation.json`，且其中 `passed` 必须为 `true`。
- `prompt_validation`：引用 `illustrations/prompt-validation.json`，且其中 `passed` 必须为 `true`。
- `image_validation`：引用 `illustrations/image-validation.json`，且其中 `passed` 必须为 `true`。

## 输出口径

生成前的策略输出要短而准。生成后的交付要包含：

- 生成了几张；
- 每张图的用途；
- 保存路径；
- 哪些图最稳，哪些图是可选。

不要长篇解释风格理论；让小舞伴的动作和隐喻自己说话。

## 流水线 handoff 模式

当上游明确说明当前调用来自 `wechat-article-production`，并提供 package 路径时，完成生图与 QA 后不要只返回图片路径，必须在该 package 内完成文章 handoff：

- 保留 `outline.md`、`outline-validation.json`、每张图的最终提示词和全部最终图片；
- 如果图像后端支持 `--log-file`，将 JSONL 运行日志保存到 `illustrations/logs/`，不要混入 `illustrations/prompts/`；
- 从上游提供的 Markdown 生成 `article-illustrated.md`，由本 Skill 自己把图片插入到已确定的位置，并使用相对路径；
- 同时写入 `illustration-handoff.json`，记录源文件、文章文件、图片相对路径、alt 文案和每张图的放置锚点；
- 在 handoff 中记录本 Skill 实际解析出的后端，以及每张图的 `16:9` 比例和参考图使用状态；
- 如果某张图生成失败或 QA 未通过，继续在本 Skill 内只重试失败项；不要把生图任务交回主 Agent，也不要要求主 Agent用脚本代插图片；
- 每次重试仍必须使用同一份 `reference_asset` 和完整的 `IP identity lock`，不能因为重试而退回宽泛角色描述；
- handoff 缺失时任务仍视为未完成，不能只报告“图片已生成”。

主流程可以整理文件目录，但不能重新决定配图位置、重写图片引用或调用另一个图像生成后端来补齐本 Skill 的交付。
