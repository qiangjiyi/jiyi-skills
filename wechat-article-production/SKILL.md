---
name: wechat-article-production
description: "把 Markdown、粘贴正文或已有 handoff 制作成微信公众号文章，支持格式化、封面、正文配图、公众号排版、package 校验和创建草稿；也可将一组图片创建为微信贴图（newspic）草稿。按用户指定阶段续跑。配图默认原生调用 jiyi-little-dancer-illustrations，也可显式选择 baoyu-article-illustrator。"
---

# 微信公众号文章生产

## 目标

负责阶段调度、文件交接和最终门禁，不复制下游 Skill 的创作逻辑。

下游 Skill 必须通过当前运行时的原生 `Skill` 工具调用。读取下游 `SKILL.md`、运行它的内部脚本、让子 Agent 模仿它，均不算调用。没有原生 `Skill` 工具时，立即将运行标记为 `blocked`，不要手工降级。

阶段调度和调用细节见 [references/stage-dispatch-rules.md](references/stage-dispatch-rules.md)。完整阶段契约见 [references/execution-contract.md](references/execution-contract.md)，配图引擎见 [references/illustration-engines.md](references/illustration-engines.md)。首跑踩坑实录与修复手册见 [references/known-pitfalls.md](references/known-pitfalls.md)，任一阶段报错先查它再重试。主 Skill 只保留本文件的调度概览。

用户明确要“微信贴图 / 图片消息 / 小绿书草稿”时，不执行公众号长文链路；读取 [references/image-text-draft.md](references/image-text-draft.md)，以 `cards/` 图片目录和纯文本说明创建 `newspic` 草稿。该模式只创建草稿，禁止调用正式发布接口。

## 阶段范围

默认执行：

```text
prepare → format → cover → illustrate → typeset → validate → publish
```

按用户请求解析范围：

- “只做 X”：执行 `prepare → X`，不自动进入后续生产阶段；
- “从 X 开始”：从 `X` 执行到 `publish`，除非用户明确说不发布或指定停止阶段；
- “从 X 到 Y”：执行包含首尾的阶段；
- “跳过 X”：不执行 X，但不能假设它的产物已经存在；
- 阶段和 handoff 不明确时先询问，不猜测。

`prepare` 始终执行，用来创建或读取 package、解析范围、确定配图 Skill 和初始化 manifest；它不代表其他阶段已经完成。

## 配图选择

默认使用 `jiyi-little-dancer-illustrations`。用户明确请求或显式指定时，切换到 `baoyu-article-illustrator`。
选择优先级：显式 `--illustration-skill` > 用户当前请求 > 默认值。
详情见 [references/illustration-engines.md](references/illustration-engines.md)。

## Package 与 manifest

新运行统一创建到：

```text
/Users/jiyi/Workspace/exports/wechat-articles/{slug}-{YYYYMMDD-HHmmss}/
```

不要让主 Agent 自己拼接目录、复制源文件或初始化。使用一次性 prepare 脚本：

```bash
node <THIS_SKILL_ROOT>/scripts/prepare-package.mjs \
  --slug <ARTICLE_SLUG> \
  --source <SOURCE.md> \
  --illustration-skill <ILLUSTRATION_SKILL> \
  [--cover-text-override none|title-only|title-subtitle|text-rich]
```

脚本成功时输出且仅输出绝对 package 路径。失败时保留脚本报错信息，不再尝试自行拼接命令或路径。

## 统一执行循环

对每个选中的阶段：
1. 登记 stage-start
2. 加载并执行下游 Skill
3. 下游完成后运行机器校验
4. 登记 stage-complete

各阶段的具体调用指令、命令和 prompt 模板见 [stage-dispatch-rules.md](references/stage-dispatch-rules.md)。原生 Skill 调用规则、容错与恢复见 [execution-contract.md](references/execution-contract.md)。

## Package 文件清单

package 中只保留以下稳定 handoff：

```text
source.md
analysis.md
formatted.md
format-validation.json
cover/cover.png
cover/prompts/*
cover/prompt-validation.json
illustrations/outline.md
illustrations/outline-validation.json  # 仅小舞伴
illustrations/prompts/*
illustrations/prompt-validation.json    # 仅小舞伴
illustrations/image-validation.json     # 仅小舞伴，机器图片门禁
illustrations/*.{png,jpg,jpeg,webp}
illustrations/ip-reference.png                 # 仅小舞伴
illustrations/illustration-handoff.json        # 仅小舞伴
article-illustrated.md
article.html
article-preview.html
execution-manifest.json
validation-report.json
publish-result.json
```

贴图模式不要求上述长文 package 文件；发布器会把图片、标题、说明和结果写入用户指定的产物目录。

## 完成报告

只报告 manifest 中真实存在的内容：

- 标准化后的阶段范围和明确跳过的阶段；
- 实际调用的配图 Skill，以及下游报告的图像后端；
- package 路径；
- `execution-manifest.json`、`validation-report.json` 和已生成产物路径；
- 是否真的创建了公众号草稿。

没有原生调用记录、缺少 handoff、缺少实际文件或校验失败时，明确报告 `blocked` / `failed`，不要写“已完成”。
