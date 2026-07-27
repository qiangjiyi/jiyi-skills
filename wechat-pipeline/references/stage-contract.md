# Stage Runner 合同与操作

## 强制合同

`wechat-pipeline` 只负责编排。每个内容阶段必须由 `scripts/stage-runner.mjs` 启动一个独立 Stage Agent，并让它只执行一个下游 Skill。Codex 与 Claude Code 共用同一请求、结果、证据和验收合同；只有驱动层不同。

请求只允许包含 `stage`、`skill`、`run_dir`、`input_file`、`output_dir`、`mode`、`constraints`、`user_preferences`。不得传递标题候选、主题、图片数量、风格、配色、提示词或生成后端等下游决策。三个路径必须为绝对路径，`output_dir` 必须位于 `run_dir` 内。

Agent 最终结果必须符合 `stage-agent-result.schema.json`，状态只能是：

- `completed`：原生流程和原生校验完成，并登记全部产物。
- `needs_input`：保留下游 Skill 的确认门和会话 ID。
- `failed`：无法安全完成。

`execution_evidence` 必须记录：

- 实际读取的 `SKILL.md` 绝对路径与 SHA-256；
- 当前任务实际读取的 references/EXTEND 文件绝对路径与 SHA-256；
- 当前任务实际执行的脚本绝对路径、SHA-256 与退出码。

Runner 会验证入口哈希、证据文件哈希、脚本退出码、完整 Skill 目录 manifest 在执行前后未变化，以及阶段必需产物。只有 `status=completed` 且 `artifact_validation.passed=true` 才能继续。已有失败回执不会自动覆盖。

产物必须位于 `output_dir`；`illustrated_markdown` 可为输入 Markdown 本身。`prompt` 可登记单个非空 Markdown 或只含 Markdown 的目录，其它角色必须是文件。Agent 不得执行其它 Stage、发布草稿或修改 Skill 源码与配置。

## 运行时

```bash
node <本Skill目录>/scripts/preflight.mjs skills
node <本Skill目录>/scripts/preflight.mjs runtime --runtime <auto|codex|claude>
```

`auto` 默认选择宿主运行时；也可传 `--agent-bin <绝对路径>`。兼容旧参数 `--codex-bin`，但新调用应使用统一参数。任一预检失败立即停止，不得退回主 Agent 手工制作。

Codex Stage 使用结构化 `codex exec` 隔离调用；Claude Code Stage 使用 `claude -p`、动态专用 agent、Skill preload、JSON Schema 和 stream-json。Claude Code 恢复使用原 session；Codex 恢复使用新隔离 Agent，并完整交接原请求、结果、问题、回答和证据。

## 完整流水线

```bash
node <本Skill目录>/scripts/run-pipeline.mjs \
  --source <输入Markdown绝对路径> \
  --run-dir <空运行目录绝对路径> \
  --runtime <auto|codex|claude> \
  --illustration-mode <auto-recommended|confirm> \
  --layout-mode <auto-recommended|confirm> \
  --account <账号标识> \
  --publish
```

不创建草稿时省略 `--account` 与 `--publish`。`format` 完成后 `cover` 与 `illustrate` 并行；`cover` 使用不可变 Markdown 快照。`layout` 依赖 `illustrate`。发布前四阶段回执必须全部通过。

## 确认、恢复与重新验收

Stage 返回 `needs_input` 后，把用户回答保存为文本，再执行：

```bash
node <本Skill目录>/scripts/stage-runner.mjs \
  --resume-receipt <运行目录>/.stage-runner/<stage>/receipt.json \
  --answer-file <回答文件> \
  --runtime <auto|codex|claude>

node <本Skill目录>/scripts/run-pipeline.mjs \
  --resume --run-dir <运行目录> \
  --runtime <auto|codex|claude> \
  --illustration-mode <模式> --layout-mode <模式> \
  --account <账号标识> --publish
```

Agent 已声明 `completed` 但确定性验收失败时，修复产物或升级验证器后只重新验收，不重新调用模型：

```bash
node <本Skill目录>/scripts/stage-runner.mjs \
  --revalidate-receipt <运行目录>/.stage-runner/<stage>/receipt.json
```

旧回执会保存到 `receipt-history/`；请求和 Agent 结果哈希不匹配时拒绝重验。

## 从中间阶段启动

```bash
# 从封面开始
node <本Skill目录>/scripts/run-pipeline.mjs --run-dir <目录> --start-from cover \
  --formatted-markdown <formatted.md> --account <账号> --publish

# 从排版开始
node <本Skill目录>/scripts/run-pipeline.mjs --run-dir <目录> --start-from layout \
  --illustrated-markdown <illustrated.md> --cover-image <cover.png> --account <账号> --publish

# 仅发布已有产物
node <本Skill目录>/scripts/run-pipeline.mjs --run-dir <目录> --start-from publish \
  --body-html <article.html> --cover-image <cover.png> --title <标题> --account <账号> --publish
```

跳过阶段会在 `pipeline-manifest.json` 标记为 `skipped`，不伪造 Stage 回执。

## 直接发布与账号预检

需要发布时，先读取 [configuration.md](configuration.md)，再执行：

```bash
node <本Skill目录>/scripts/preflight.mjs account --account <账号标识>
node <本Skill目录>/scripts/resolve-config.mjs mode --json
node <本Skill目录>/scripts/resolve-config.mjs output-dir --json

node <本Skill目录>/scripts/publish-wechat-article.mjs \
  --html <article.html> --cover <cover.png> --title <标题> \
  --account <账号标识> --yes
```

不得输出 App Secret 或 Access Token。`draft/add` 不自动重试。
