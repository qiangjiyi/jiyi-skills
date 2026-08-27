# 阶段调度规则

本文件包含各阶段的具体调用指令、命令示例和 prompt 模板。主 Skill 按需读取本文件中对应阶段的规则。

阶段契约和 handoff 定义见 [execution-contract.md](execution-contract.md)，配图引擎选择见 [illustration-engines.md](illustration-engines.md)。**首跑踩坑实录与修复手册见 [known-pitfalls.md](known-pitfalls.md)，调度遇到报错先查它。**

### 原生 Skill 的执行语义与父流程边界

在 Claude Code 中，`Skill` 工具调用本身不是一个异步子进程，也不是“子 Skill 已经完成”的返回值。它会把被选中的 Skill 规范加载到当前 Agent 上下文；工具返回后，当前 Agent 必须继续按照这个 Skill 自己的 `SKILL.md`、references 和脚本完整执行，直到该 Skill 交付成功 handoff 或明确返回失败。

执行顺序必须是：

```text
父流程 stage-start
  → Skill 工具加载下游 Skill
  → 当前 Agent 进入下游 Skill 执行态并完成其完整 workflow
  → 下游交付 handoff 或失败原因
  → 父流程只做机器校验、归一化和 stage-complete/stage-fail
```

下游 Skill 执行态允许读取自己的 `SKILL.md`/references、运行自己的脚本、写入自己的 Outline/Prompt/图片/HTML/handoff，并调用自己规定的图像后端；这些是原生 Skill 的实际执行，不属于父流程模拟。父流程不得在没有加载并执行下游 Skill 的情况下读取其文档重做流程，也不得在下游失败后代写或补齐下游交付。

父流程恢复后只能执行机器检查和文件打包，例如格式化产物的确定性归一化；不能读取正文重新格式化、重写图片引用、补写 `article-illustrated.md` 或手写 `illustration-handoff.json`。

如果必需产物缺失，只能把具体缺失清单原样交回同一个下游 Skill，最多重试一次；重试后仍缺失就立即将阶段标记为 `failed`/`blocked`，不得继续后续阶段。图片存在但 handoff 缺失，仍然属于失败。

`cover`、`illustrate`、`typeset` 是原生 Skill-only 阶段；不得通过 `manual-fallback` 将主流程模拟的结果登记为完成。

## 统一执行循环

对每个选中的下游阶段执行以下顺序：

```text
stage-start → Skill 加载 → 当前 Agent 完成下游 Skill workflow → Prompt/产物机器校验 → stage-complete → 继续下一阶段
```

调用前登记：

```bash
node <THIS_SKILL_ROOT>/scripts/execution-manifest.mjs stage-start \
  --file <PACKAGE_DIR>/execution-manifest.json \
  --stage <STAGE> \
  --invocation-kind native-skill \
  --invocation-source runtime \
  --invoked-skill <SKILL_NAME> \
  --inputs <INPUTS>
```

然后使用原生 `Skill` 工具加载下游，并把 package 路径、输入文件、目标输出和“不询问、直接采用推荐项”的授权传给它。注意：工具返回 Skill 说明后，不能立即执行 `ls` 或归一化检查；当前 Agent 必须先按刚加载的下游 Skill 完成自己的 workflow，只有下游报告完成或失败后，父流程才能恢复。

阶段完成只登记下游实际产物：

```bash
node <THIS_SKILL_ROOT>/scripts/execution-manifest.mjs stage-complete \
  --file <PACKAGE_DIR>/execution-manifest.json \
  --stage <STAGE> \
  --outputs <OUTPUTS>
```

`stage-complete` 会检查声明的输出是否真实存在，并强制检查该阶段的最小 handoff；少报一个文件也不能绕过门禁。不要让父流程承担下游的内容生产、图片插入或 HTML 组装。需要修复时，把明确错误交回同一个下游 Skill 重试；无法修复就标记 `failed` 或 `blocked`。

## 下游调用要求

### `format`

加载并执行 `baoyu-format-markdown`，输入 `source.md`。当前调用应明确要求读取它自己的 `EXTEND.md`，在全局配置允许自动选择时直接采用配置，不停在标题/摘要问答；完整完成分析、frontmatter、正文格式化和 typography，并在 package 根目录交付 `source-analysis.md` 与 `source-formatted.md`。父流程随后只移动原生文件，不在移动动作中产生正文差异：

```bash
node <THIS_SKILL_ROOT>/scripts/normalize-format-output.mjs \
  --package-dir <PACKAGE_DIR> \
  --source-file source.md
```

归一化成功后，运行本 Skill 的确定性格式化门禁：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-format-output.mjs \
  --package-dir <PACKAGE_DIR> \
  --analysis-file analysis.md \
  --formatted-file formatted.md \
  --report-file format-validation.json
```

只有 `format-validation.json` 的 `passed=true`，才可以登记 `analysis.md`、`formatted.md` 和 `format-validation.json`。如果原生派生文件不存在、格式化 Skill 停在交互选择或门禁失败，最多把具体错误交回同一个 Skill 重试一次；仍未交付就标记 `format` 为 `failed`，不要复制 source 充数，也不要直接运行下游内部脚本。

格式化阶段的调用提示应包含：

```text
当前调用来自 wechat-article-production。请完整执行 baoyu-format-markdown 的 Analyze → Format → Typography workflow，读取并遵循它自己的 EXTEND.md；若配置允许 auto-select，请直接选择最佳标题和摘要，不要停在用户问答。输入是 <PACKAGE_DIR>/source.md，原生输出必须是 package 根目录下的 source-analysis.md 和 source-formatted.md。完成报告中列出这两个文件；不要在 package 外写文件，也不要修改 baoyu-format-markdown Skill 本身。
```

### `cover`

加载并执行 `baoyu-cover-image`，输入 `formatted.md`，采用推荐项直接生成。当前 Agent 必须继续执行 CoverImage 自己的偏好读取、分析、Prompt、后端调用和完成报告；公众号流水线封面固定使用 `2.35:1` 横向比例；调用提示必须把它作为当前请求的 `--aspect 2.35:1` override，不能沿用宝玉 Skill 的全局 `default_aspect`。后端由 `baoyu-cover-image` 自己读取其 `EXTEND.md` 和运行时规则。要求封面、完整最终提示词和 `cover/prompt-validation.json` 进入 `cover/`。比例、文字和水印由下游 Skill 检查，最终 package 校验器再做机器复核。不要修改宝玉封面图 Skill 的全局默认比例。

封面 Prompt 不能只写一段概念描述。由于 Claude Code 的 `Skill` 调用会把下游规范加载到当前 Agent，当前 Agent 在写 Prompt 前必须读取 CoverImage 的 `references/workflow/prompt-template.md`；任一自动维度必须读取 `references/auto-selection.md` 和对应的 type、palette、rendering、mood、font、text reference。Prompt 必须包含模板规定的 Content Context、Visual Design、Text Elements、Mood Application、Font Application 和 Composition，并且记录源文章标题、摘要、关键词、五维配置、比例和语言。

写完 Prompt、调用图像后端之前，必须运行本 Skill 自己的确定性校验器：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-cover-prompt.mjs \
  --package-dir <PACKAGE_DIR> \
  --prompt-file <PROMPT_RELATIVE_PATH> \
  --source-file formatted.md \
  --expected-aspect 2.35:1 \
  --report-file cover/prompt-validation.json
```

校验失败时不得调用图像后端；只修正当前 CoverImage 流程中的 Prompt 后重新校验。父流程不读取宝玉 Skill 源码来模拟生成，也不修改宝玉 Skill；父流程只在下游完成后检查 `prompt-validation.json`，报告缺失或失败就把阶段标记为 failed/blocked。

调用 `baoyu-cover-image` 时，当前请求必须明确包含以下约束：

```text
当前调用来自 wechat-article-production。请完整执行 baoyu-cover-image 的偏好读取、文章分析、五维选择、完整 Prompt Template、图像后端调用和完成报告。不要只写一段概念描述。写 Prompt 前读取 baoyu-cover-image 的 references/workflow/prompt-template.md，以及自动维度对应的 references；Prompt 必须包含 Content Context、Visual Design、Text Elements、Mood Application、Font Application 和 Composition。公众号封面使用当前请求的 2.35:1 比例 override；按照下游 EXTEND.md 决定文字级别，不要自行添加标题。Prompt frontmatter 必须一次性写全八字段（type 填视觉类型如 metaphor/hero，另含 palette/rendering/text/mood/font/aspect/lang），避免校验往返。调用图像后端前，运行 wechat-article-production/scripts/validate-cover-prompt.mjs 并生成 cover/prompt-validation.json；校验不通过不得生图。图像后端调用统一附加 --imageApiDialect openai-native（规避全局 .env 中 ratio-metadata 对 codex-cli/openai 的击穿，见 known-pitfalls 坑 I1）。不要修改 baoyu-cover-image Skill 本身。
```

文字策略必须单独处理：只有用户当前请求明确要求“带标题/带文字/无文字/纯视觉”等文字级别时，才向 manifest 传入 `--cover-text-override`，并在下游请求中明确传递该 override。没有显式文字要求时，父流程不得写入 `--text`、`--no-title`、原文标题或“中文标题使用原文标题”等指令；由 `baoyu-cover-image` 按项目 → XDG → 用户级 `EXTEND.md` 的 `preferred_text` 决定。全局 `EXTEND.md` 中已经配置的文字级别优先于文章类型自动推断。

### `illustrate`

加载并执行选定的配图 Skill，输入 `formatted.md` 和 package 路径。Skill 工具返回后，当前 Agent 必须继续完成该 Skill 自己的文章分析、配图策略、提示词、生成、QA，以及 `article-illustrated.md` 的图片插入；父流程不能在此时抢回控制权做文件检查。选择小舞伴时，生成图片后的 QA 必须先运行其 `scripts/validate-images.mjs`，禁止批量 `Read` 完整 PNG；通过后立即运行 `finalize-handoff.mjs`。

默认小舞伴调用必须明确写出：

```text
当前调用来自 wechat-article-production。请完整执行 jiyi-little-dancer-illustrations 的文章分析、配图规划、结构化独立 prompt、参考图处理、图片生成、QA 和 illustration-handoff.json / article-illustrated.md handoff。请按照小舞伴自己的 `EXTEND.md`、当前请求和运行时规则解析图像后端，通过原生 Skill 完整调用；在完成报告中注明实际后端和 used_for_identity: true。注意：需要传入参考图（--ref）的生成必须显式指定支持参考图的后端（如 --provider openai），EXTEND 钉死的 codex-cli 不支持 --ref，且不会因传了 --ref 自动切换；生图命令统一附加 --imageApiDialect openai-native（见 known-pitfalls 坑 I1/I2）。图片生成后先运行 `scripts/validate-images.mjs`，再运行 `scripts/finalize-handoff.mjs`；不要使用 Read 批量加载完整 PNG，不要直接调用 image_gen、不要运行图像后端内部脚本、不要只返回图片路径；固定后端不可用时标记 blocked，不要静默替换后端。
```

如果用户显式选择 `baoyu-article-illustrator`，调用提示必须改为要求它完整执行自己的文章分析、outline、prompt、生成、QA 和 `article-illustrated.md` handoff，并按照宝玉文章配图 Skill 自己的 `EXTEND.md`、当前请求和运行时规则解析后端、由它自己报告实际图像后端。不要修改宝玉文章配图 Skill 的全局 `EXTEND.md`。

小舞伴模式还必须传入真实的 `illustrations/ip-reference.png`，并要求 handoff 记录参考图实际用于身份锁定。没有 handoff 时，配图阶段不能完成。

### `typeset`

加载并执行运行时实际可用的排版 Skill，当前通常是 `gzh-design` / `gzh-design-skill`。如果执行过 `illustrate`，输入 `article-illustrated.md`；如果用户明确跳过正文配图，输入 `formatted.md`。当前 Agent 必须先完成排版 Skill 自己的 workflow，再由父流程检查结果；主 Agent 不得在没有执行排版 Skill 时手写 HTML，也不得直接改 `article.html`。

**排版产出必须直接满足生产校验器的标签黑名单**（比排版 Skill 自带校验更严，见 known-pitfalls 坑 P1）：正文 HTML 禁止 `html/head/body/script/style/div/svg/figure/figcaption` 标签——图片说明用 `<p>` 承载、图容器用 `<section>`、装饰位用文字或 span 色块而非 `<svg>`。排版 Skill 自带校验通过不代表生产 validate-package 通过，以生产清单为准，避免事后返工。若 validate 后发现禁用标签，把错误清单交回排版 Skill 重排；normalize-typeset 是移动不是复制，修复永远发生在排版 Skill 的源产物上，再重跑 wrap_preview → 改名 article-preview.html → 重跑 validate-package。

排版 Skill 交付后，父流程必须把它的产物归一化为契约文件名（与 `format` 阶段的 `normalize-format-output.mjs` 同理）：

```bash
node <THIS_SKILL_ROOT>/scripts/normalize-typeset-output.mjs \
  --package-dir <PACKAGE_DIR>
```

`gzh-design-skill` 输出的是 `{原文件名}_排版_{主题}.html`（干净正文片段）与 `{...}_预览.html`（预览页），该脚本会把最新一份分别重命名为 `article.html` 与 `article-preview.html`。归一化成功后再进入 `validate` 阶段；若 package 根目录找不到 `*_排版_*.html` / `*_预览.html`，脚本会报错，应把明确错误交回同一个排版 Skill 重跑，不要手写 article.html 充数。

### `validate`

只做一次最终门禁，先校验 manifest 结构，再运行 package 校验脚本：

```bash
node <THIS_SKILL_ROOT>/scripts/execution-manifest.mjs validate \
  --file <PACKAGE_DIR>/execution-manifest.json

node <THIS_SKILL_ROOT>/scripts/validate-package.mjs \
  --package <PACKAGE_DIR> \
  --manifest <PACKAGE_DIR>/execution-manifest.json \
  --report-file <PACKAGE_DIR>/validation-report.json
```

脚本负责文件、图片引用、尺寸、HTML 兼容性、占位符、源文件完整性和原生 Skill 调用证据。主 Agent 不再重复做同一批检查。

### `publish`

只有 `validate` 通过后才创建草稿。先执行发布器 `--dry-run`，再执行一次真实发布；不群发、不自动重试不明确的发布结果。配置和命令见 [references/publishing-configuration.md](references/publishing-configuration.md)。

标准命令（注意没有 `--package` 参数，用绝对路径逐项传入）：

```bash
node <THIS_SKILL_ROOT>/scripts/publish-wechat-article.mjs \
  --html <PACKAGE_DIR>/article.html \
  --cover <PACKAGE_DIR>/cover/cover.png \
  --title "<标题>" --account jiyi --summary "<摘要>" \
  --result-file <PACKAGE_DIR>/publish-result.json --dry-run
# inputsValidated: true 后，去掉 --dry-run 加 --yes 真实发布（仅建草稿）
```

stage 登记注意：typeset 的 `--invoked-skill` 必须填运行时 Skill 工具可解析的名字（本机是 `gzh-design`），登记一个加载会报 Skill not found 的名字会导致 transcript 校验失败（known-pitfalls 坑 T2）。

若用户明确请求微信贴图（图片消息），改读 [image-text-draft.md](image-text-draft.md)：它使用独立 `newspic` 发布器和图片目录预检，不要求 `article.html`、公众号封面或长文 package 校验；只创建草稿，绝不调用 `freepublish`。
