# 执行契约

本文件只定义范围、handoff 和真实执行边界。机器可判定的检查交给 `scripts/execution-manifest.mjs` 与 `scripts/validate-package.mjs`，不要在主 Skill 中重复展开。

本文的标准阶段只适用于公众号长文。用户明确请求“微信贴图 / 图片消息 / 小绿书草稿”时，切换到 [image-text-draft.md](image-text-draft.md)：无需建立长文 manifest，不要求 `article.html` 或 `cover/cover.png`，仅创建 `article_type=newspic` 草稿并写出 `publish-result.json`。

## 标准阶段

| ID | 负责人 | 输入 | 最小 handoff |
| --- | --- | --- | --- |
| `prepare` | 本 Skill | 原文、已有 package 或 handoff | `execution-manifest.json` |
| `format` | `baoyu-format-markdown` | `source.md` | 原生 `source-analysis.md`、`source-formatted.md`，再由归一化脚本交付 `analysis.md`、`formatted.md` 和 `format-validation.json` |
| `cover` | `baoyu-cover-image` | `formatted.md` | `cover/cover.png`、符合 CoverImage 模板的最终提示词、`cover/prompt-validation.json`（固定 2.35:1） |
| `illustrate` | 选定配图 Skill | `formatted.md`、package | `illustrations/`、`article-illustrated.md` |
| `typeset` | `gzh-design` / `gzh-design-skill` | `article-illustrated.md`；明确跳过正文配图时为 `formatted.md` | `article.html`、`article-preview.html` |
| `validate` | 本 Skill 的脚本 | package、manifest | `validation-report.json` |
| `publish` | 内置发布器 | 已通过校验的 package | `publish-result.json` |

`prepare` 永远加入执行范围；被跳过的生产阶段不能记录为 completed，也不能假设它的 handoff 已经存在。

## 范围解析

记录以下字段：

```json
{
  "requested_start_stage": "format|null",
  "requested_end_stage": "publish|null",
  "requested_only_stage": "null",
  "explicit_skips": [],
  "publish_requested": true,
  "scope_source": "default|prompt",
  "illustration_skill": "baoyu-article-illustrator|jiyi-little-dancer-illustrations"
}
```

阶段别名：

- `format`：格式化、优化排版、Markdown；
- `cover`：封面；
- `illustrate`：正文配图、生图、插图；
- `typeset`：公众号排版、HTML；
- `validate`：校验、验证；
- `publish`：发布、创建草稿、草稿箱。

解析优先级：用户明确范围 > 用户明确跳过 > 默认范围。只有“只做 X”时才只执行 X；“从 X 开始”默认继续到 publish，除非用户明确说不发布或指定停止阶段。

## 续跑输入

| 输入 | 可开始阶段 | 必须做的 prepare 动作 |
| --- | --- | --- |
| 原始 Markdown / 粘贴正文 | 任意需要正文的流程 | 复制为 `source.md` |
| `formatted.md` | `cover`、`illustrate` | 复制到 package |
| `article-illustrated.md` | `typeset` | 连同本地图片复制到 package |
| `article.html` | `publish` | 检查封面、账号、正文图片和元数据 |
| 已有 package | 对应 handoff 阶段 | 原地读取 manifest，不覆盖已完成产物 |

缺少起始阶段所需 handoff 时，标记 `blocked`，并说明能够创建它的最早阶段。不要默默重跑、跳过或手工补产物。

## 原生调用证据

以下阶段必须由当前运行时的原生 `Skill` 工具调度：

| 阶段 | 精确 Skill |
| --- | --- |
| `format` | `baoyu-format-markdown` |
| `cover` | `baoyu-cover-image` |
| `illustrate` | manifest 中的 `scope.illustration_skill` |
| `typeset` | 运行时实际可用的 `gzh-design` 或 `gzh-design-skill` |

每个阶段都必须按以下顺序执行：

```text
manifest stage-start → 原生 Skill 调用 → Prompt 校验通过 → 产物存在 → manifest stage-complete
```

`stage-start` 至少记录：阶段、准确 Skill 名称、`invocation.kind=native-skill`、`tool=Skill`、`source=runtime`、输入路径。运行时返回的 invocation ID 才可以写入；不能自行生成 ID。

`stage-complete` 只登记真实输出路径。脚本会检查输出存在；输出缺失时不要完成阶段。

格式化阶段允许使用 `scripts/normalize-format-output.mjs` 对原生 Skill 已生成的派生文件做确定性改名；该脚本只移动文件，不产生正文内容。归一化后必须运行 `scripts/validate-format-output.mjs`，检查原生分析章节、formatted frontmatter 和正文非空；该脚本不能读取正文重新格式化，也不能在原生文件缺失时复制 source 充数。

每个下游阶段的业务责任都必须由对应的原生 Skill 承担；父流程只负责调度、机器门禁和文件归档。没有 `Skill` 工具时，生产阶段必须阻塞，不能把父流程的产物登记为 `native-skill`。

在 Claude Code 中，`Skill` 工具返回的是下游规范加载结果，不是下游 workflow 已完成的信号。加载后当前 Agent 必须继续执行该下游 Skill 自己的文档、references、脚本和后端调用；只有下游完成或失败后，父流程才进入只读门禁。下游未交付时，父流程只能把具体缺口交回同一个 Skill；最多重试一次，再次缺失立即阻塞。

## 配图后端证据

`wechat-article-production` 只调用文章配图 Skill，不直接调用图像生成后端。

选择 `baoyu-article-illustrator` 时：

1. 父级 transcript 必须有 `baoyu-article-illustrator` 的原生 Skill 调用；
2. 子 Skill 必须完成 outline、独立 prompt、图片生成、QA 和 `article-illustrated.md`；
3. 子 Skill 完成报告应注明实际 image backend；
4. 下游必须按照自己的 `EXTEND.md`、当前请求和运行时规则原生调用实际后端，并在完成报告中注明实际调用；
5. 父级不能直接调用任何图像后端、`codex-imagegen` wrapper 或某个 provider 来绕过下游 Skill。

选择 `jiyi-little-dancer-illustrations` 时：

- 必须把 package 内的 `illustrations/ip-reference.png` 作为真实图像输入传给下游；
- 必须交付结构化 `illustrations/outline.md` 及 `illustrations/outline-validation.json`，且校验报告必须通过；
- 必须交付 `illustrations/image-validation.json`，且机器图片门禁必须通过；该脚本只读取图片文件头和尺寸，禁止把全部 PNG 批量读入 Agent 上下文；
- 必须交付 `illustration-handoff.json`；
- handoff 必须记录参考图来源、package 路径、`used_for_identity: true` 和固定身份锚点；
- 只在提示词里写参考图路径不算使用参考图。

图片后端日志可以辅助复盘，但日志本身不等于上层 Skill 调用。最终是否完成，仍以原生调用记录和完整 handoff 为准。

封面阶段由 `baoyu-cover-image` 按自己的 `EXTEND.md`、当前请求和运行时规则解析后端。封面 prompt 必须按照其 `references/workflow/prompt-template.md` 写出内容上下文、视觉设计、文字元素、情绪、字体和构图字段，并通过父 Skill 的 `scripts/validate-cover-prompt.mjs`。封面 prompt 必须声明 `2.35:1`，`cover/cover.png` 的真实 PNG 尺寸必须通过 2.35:1 比例门禁；只在 prompt 中写比例而实际文件不符合时，`cover` 阶段不能完成。该比例约束只属于公众号流水线，不修改宝玉封面图 Skill 的全局默认值。文字级别只有在用户当前请求显式指定时才允许写入 manifest override；否则必须遵循下游 `EXTEND.md` 的 `preferred_text`。

## 阶段完成与失败

- 下游返回失败：把原始错误传回同一个 Skill，必要时只重试失败阶段；
- 下游只交付部分 handoff：阶段保持 `failed`/`blocked`；
- 父流程只负责接收下游结果，不承担 `article-illustrated.md`、图片放置或 `article.html` 的内容生产；
- `cover`、`illustrate`、`typeset` 不允许以非原生调用登记完成，必须由原生 Skill 调用完成；
- `validate` 失败：不得进入 `publish`；
- `publish` 结果不明确：记录 `failed`，不自动重试。

状态口径：`blocked` 表示前置条件、运行时 Skill 工具或必需输入不存在；`failed` 表示已经开始原生执行，但下游返回错误或机器门禁失败。停止时必须在 manifest 的 `error` 中写出阶段、期望 handoff、实际缺口和重试次数，不得只写“执行失败”。

## 机器命令

初始化：

```bash
node <THIS_SKILL_ROOT>/scripts/execution-manifest.mjs init \
  --file <PACKAGE_DIR>/execution-manifest.json \
  --package-dir <PACKAGE_DIR> \
  --execution-stages prepare,format,cover,illustrate,typeset,validate,publish \
  --illustration-skill jiyi-little-dancer-illustrations \
  --native-dispatcher Skill \
  --native-dispatcher-available true
```

最终校验：

```bash
node <THIS_SKILL_ROOT>/scripts/validate-package.mjs \
  --package <PACKAGE_DIR> \
  --manifest <PACKAGE_DIR>/execution-manifest.json \
  --report-file <PACKAGE_DIR>/validation-report.json
```

manifest 是执行记录，不是对话 transcript。禁止写入 access token、App Secret、cookie、`.env` 内容或其他凭据。
