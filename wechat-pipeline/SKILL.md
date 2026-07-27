---
name: wechat-pipeline
description: 在 Codex 或 Claude Code 中，以独立 Stage Agent 原生执行文章格式化、公众号封面、正文配图和公众号 HTML 排版，并在全部阶段回执验收通过后创建微信公众号草稿。用户提供文章或 Markdown，要求制作微信图文、完整配图排版、发送到指定公众号草稿箱或恢复中断的公众号流水线时使用。
---

# 微信公众号 Stage Runner 流水线

编排器。只负责预检、创建请求、等待、回执验收、恢复和草稿创建。每个内容阶段交给当前运行时的独立 Stage Agent，由该 Agent 完整加载并执行一个下游 Skill 的原生流程。

## 不可突破的边界

- 每个阶段**必须**通过 `scripts/stage-runner.mjs` 启动独立 Stage Agent。Agent 必须完整读取下游 `SKILL.md`，并读取、执行该 Skill 对当前任务要求的 `EXTEND.md`、references 和 scripts；不得只加载入口。
- **禁止**在编排器中替下游 Skill 选择标题、主题、图片数量、风格、配色、提示词或生成后端。
- **禁止**在主 Agent 中手写分析稿、封面提示词、配图大纲、正文配图或公众号 HTML。
- **禁止**跳过下游 Skill 加载而凭描述信息自行模仿其行为（伪执行）。
- Runner 不可用、Stage 回执缺失或验收失败时立即停止；**禁止**降级为同一 Agent 手工生产。
- 不修改下游 Skill 的源码、`EXTEND.md`、全局配置或真实凭据。
- 只创建草稿，不群发、不正式发布。

## 防伪执行规则

| LLM 可能的捷径思维 | 为什么是错的 | 必须行动 |
|---|---|---|
| "格式化规则我知道，直接写" | baoyu-format-markdown 有自己的规则和校验 | 通过 stage-runner 启动独立 Agent 加载该 Skill |
| "封面提示词我可以直接生成" | baoyu-cover-image 有原生风格决策和后端选择 | 通过 stage-runner 启动独立 Agent 加载该 Skill |
| "配图大纲我自己列就行" | baoyu-article-illustrator 有原生配图规范 | 通过 stage-runner 启动独立 Agent 加载该 Skill |
| "排版 HTML 我直接写" | gzh-design 有自己的模板和设计规范 | 通过 stage-runner 启动独立 Agent 加载该 Skill |
| "回执丢了，直接重跑" | 丢失会话证据等于从空白上下文猜测 | 使用 `--resume-receipt` 续接原 Stage |

## 流水线 DAG

```
format → (cover ∥ illustrate) → layout → publish
```

1. `format`：独立 Agent 加载 `baoyu-format-markdown`，输出格式化 Markdown。
2. `format` 通过后，`cover` 与 `illustrate` 并行：
   - `cover`：独立 Agent 加载 `baoyu-cover-image`，使用不可变 Markdown 快照，编排器只追加 `2.35:1` 接口约束。
   - `illustrate`：独立 Agent 加载 `baoyu-article-illustrator`，按原生规则更新插图版 Markdown。
3. `layout`：独立 Agent 加载 `gzh-design`，依赖 `illustrate` 产物。
4. 四份回执均为 `completed` 且 Runner 验收通过后，调用微信草稿脚本发布。

每个 Stage Agent 必须完整读取自己的 SKILL.md 和任务直接要求的 references，自行完成分析、决策、生成与原生校验。Stage Agent 不知道后续发布职责，也不得执行其它 Stage。

## 执行步骤

执行前完整读取 [references/stage-contract.md](references/stage-contract.md)，其中包含请求、证据、回执、恢复协议和全部操作命令。仅在需要创建草稿或解析输出目录时读取 [references/configuration.md](references/configuration.md)。

### 1. 预检

依次运行 preflight（skills → runtime → account）和 resolve-config（mode → output-dir），任一失败立即停止。账号不唯一时才询问用户，不得输出凭据。

### 2. 准备输入

- 文件输入：直接使用绝对路径。粘贴文章：先原样保存为临时 Markdown。
- 新运行目录：`<输出根>/wechat-article-<slug>-<YYYYMMDD-HHmmss>/`，必须不存在或为空。
- 只有恢复用户明确指定的旧任务时才复用旧目录。

### 3. 启动流水线

```bash
node <本Skill目录>/scripts/run-pipeline.mjs \
  --source <输入Markdown绝对路径> \
  --run-dir <新运行目录绝对路径> \
  --runtime <auto|codex|claude> \
  --illustration-mode <auto-recommended|confirm> \
  --layout-mode <auto-recommended|confirm> \
  --account <目标账号> \
  --publish  # 用户要求创建草稿时才传
```

`--runtime auto` 默认选择宿主运行时，也可用 `--agent-bin` 指定二进制。单个 Stage 默认最多 30 分钟。从中间阶段启动或直接发布已有产物时，参见 [stage-contract.md](references/stage-contract.md)。

### 4. 确认与恢复

- `auto-recommended`：下游 Skill 自动采用自己的推荐项，编排器不给出具体选项。
- `confirm`：保留下游 Skill 的原生确认门。
- Stage 返回 `needs_input` 时，报告问题并停止。用户回答后用 `--resume-receipt` 续接同一 Stage；Claude Code 使用原生 session resume，Codex 使用带完整证据的隔离上下文交接。
- Agent 已 `completed` 但验收失败时，修复产物后用 `--revalidate-receipt` 重新验收，不重新启动 Agent。

### 5. 回执验收

只有 `status=completed` 且 `artifact_validation.passed=true` 才能进入下一阶段。回执必须包含完整 Skill manifest 哈希、实际读取/执行证据、Agent session/thread ID、产物和确定性验收结果。最终 `pipeline-manifest.json` 汇总四个回执，没有该文件不得声称流水线完成。

### 6. 发布安全

- 真实发布只在用户明确要求"发送到草稿箱""创建草稿"时传 `--publish`。
- `draft/add` 只提交一次；结果不明确时不自动重试。
- 正文图片和封面上传使用有上限的并发（默认 3）。
- 不使用或保存静态 `WECHAT_ACCESS_TOKEN`。修改真实 `.env` 前必须获得用户明确同意。

## 完成报告

草稿创建成功时报告账号、作者、标题、正文图片数量、Draft Media ID、运行目录和 `pipeline-manifest.json`。
