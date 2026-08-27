---
name: skill-doctor
description: "诊断和优化由多个 Skill 串联或并行组成的 Agent 工作流。用于检查目标 Skill 源码中的父子 Skill 调度、原生调用约束、handoff/产物契约、失败降级和串并行依赖；也用于分析一次 Claude Code 或 Codex Session 的真实 Tool/Skill 调用链、执行产物和失败原因。默认只读诊断，用户确认后才修改目标 Skill。"
---

# Skill Doctor

## 目标

对目标 Skill 做证据化诊断，不把“源码写了调用”“文件已经存在”或“Agent 声称完成”当成真实执行。输出源码推断、运行时证据、冲突、风险和可实施优化方案；只有用户明确确认后才修改目标 Skill。

本 Skill 支持 Claude Code 和 Codex。会话盘点优先复用 `session-analyzer` 的扫描结果，但不复制它的清理或 HTML 报告职责。

## 铁律

- 诊断阶段只读，不修改目标 Skill、Session、manifest、产物或外部系统。
- 读取子 Skill 的 `SKILL.md`、运行子 Skill 的脚本、让父 Agent 模仿子 Skill，都不算子 Skill 被原生调用。
- 子 Skill 失败、调用证据缺失或 handoff 不完整时，判定为 `blocked` / `failed` / `missing-evidence`，不得手工补产物后报告成功。
- 每条结论标记证据：`runtime-proven`、`source-inferred`、`artifact-only`、`missing-evidence` 或 `conflict`。
- 不输出 token、API key、cookie、Authorization、完整敏感参数或完整 prompt；报告只保留脱敏摘要、哈希、文件和行号。
- 实施前必须展示修改范围并等待用户确认；未确认时只生成 patch 建议。

## 入口与模式

```text
source     目标 Skill 源码诊断
execution  单次 Claude/Codex Session 与产物诊断
full       先做源码诊断，再做执行诊断并合并建议
```

输入可以是：

- `--target <SKILL_DIR>`：目标 Skill 根目录；
- `--session <JSONL>`：Claude Code JSONL 或 Codex rollout JSONL；
- `--thread-id <ID>`：Codex thread ID，脚本从 `state_5.sqlite` 只读解析 rollout 路径；
- `--codex-db <PATH>`：可选 Codex SQLite 路径；
- `--scan <PATH>`：`session-analyzer` 生成的扫描 JSON，用于绑定 Agent/项目/Session 元数据；
- `--package <DIR>`：目标 Skill 本次执行的 package 或产物目录；
- `--out <DIR>`：报告输出目录，默认 `/Users/jiyi/Workspace/exports/skill-doctor/` 下的时间目录。

只给目标 Skill 时运行 `source`；给出 Session、thread、scan 或 package 时运行 `full`，除非用户指定其他模式。

## 标准流程

### 1. 识别输入

确认目标 Skill 根目录、诊断模式和执行样本。没有可读 Session 时，可以继续做源码诊断，但必须在报告中声明运行时证据缺失，不得推断“已执行”。

### 2. 运行确定性源码扫描

```bash
bash <THIS_SKILL_ROOT>/scripts/run-doctor.sh source \
  --target <SKILL_DIR> \
  --out <REPORT_DIR>
```

脚本负责扫描 `SKILL.md`、`agents/`、`references/`、`scripts/`，输出 `source-graph.json` 和 `source-report.md`。不要在主 Agent 中重复手工统计文件、Skill 名称或产物路径。

重点查看：

- 父级是否声明了原生 `Skill` 调用；
- 子 Skill 是否被普通脚本、复制逻辑或手工 fallback 替代；
- 阶段是否有明确输入、输出、handoff 和失败传播；
- 并行分支是否有汇聚、结果完整性检查和部分失败处理；
- “completed” 是否有真实文件检查和运行时调用证据。

### 3. 运行确定性 Session 归一化

Claude Code：

```bash
bash <THIS_SKILL_ROOT>/scripts/run-doctor.sh execution \
  --target <SKILL_DIR> \
  --session <CLAUDE_JSONL> \
  --scan <SESSION_ANALYZER_JSON> \
  --package <PACKAGE_DIR> \
  --out <REPORT_DIR>
```

Codex：

```bash
bash <THIS_SKILL_ROOT>/scripts/run-doctor.sh execution \
  --target <SKILL_DIR> \
  --thread-id <CODEX_THREAD_ID> \
  --codex-db <HOME>/.codex/state_5.sqlite \
  --scan <SESSION_ANALYZER_JSON> \
  --package <PACKAGE_DIR> \
  --out <REPORT_DIR>
```

脚本只读解析 Claude JSONL、Codex rollout JSONL 和 Codex SQLite thread 元数据，提取脱敏的 Tool/Skill 调用、时间线、错误、重试和文件证据。没有原始 transcript 时，`session-analyzer` 快照只能证明 Session 存在，不能证明某个子 Skill 被调用。

### 4. 交叉验证并生成报告

```bash
bash <THIS_SKILL_ROOT>/scripts/run-doctor.sh full \
  --target <SKILL_DIR> \
  --session <SESSION_JSONL> \
  --scan <SESSION_ANALYZER_JSON> \
  --package <PACKAGE_DIR> \
  --out <REPORT_DIR>
```

报告至少回答：

1. 源码声称调用了哪些子 Skill？
2. Session 中哪些调用真实发生？
3. 哪些子 Skill 只被读取、模拟或绕过？
4. 子 Skill 是否完整执行了自身流程并交付 handoff？
5. 是否存在父级补产物、伪造 manifest、失败后静默降级？
6. 串行、并行和汇聚阶段的依赖是否成立？
7. 最小修改方案是什么，修改后如何验证？

### 5. 实施优化

先向用户展示：

- P0/P1/P2/P3 问题；
- 具体文件和行号；
- 拟修改内容；
- 不修改的范围；
- 回归命令和预期证据。

收到明确确认后，由父 Agent 按已确认范围执行 patch。诊断脚本本身始终只读。修改完成后必须重新运行源码扫描、目标 Skill 自带校验脚本和至少一个 fixture/历史 Session 回归。任一关键校验失败，都报告为未完成或阻塞。

## 输出

每次诊断都写入独立报告目录：

```text
source-graph.json       # 源码图谱和 source-inferred findings
execution-trace.json    # 归一化 Session 和 runtime evidence
diagnostic-report.json  # 合并证据、问题和建议
diagnostic-report.md    # 用户可读报告
```

`diagnostic-report.md` 采用“结论 → 证据 → 影响 → 根因 → 建议 → 验证计划”顺序。报告中明确区分：

- `runtime-proven`：Session/Tool/文件时间线直接支持；
- `source-inferred`：源码支持，但没有运行时证明；
- `artifact-only`：只有产物，不能证明原生 Skill 调用；
- `missing-evidence`：应该有证据但没有找到；
- `conflict`：源码、Session、manifest 或产物互相矛盾。

完整字段和问题目录见 [references/diagnostic-contract.md](references/diagnostic-contract.md)、[references/session-adapters.md](references/session-adapters.md) 和 [references/finding-catalog.md](references/finding-catalog.md)。
