# Claude Code / Codex Session 适配

## 目录

- [共同规则](#共同规则)
- [Claude Code](#claude-code)
- [Codex](#codex)
- [session-analyzer](#session-analyzer)
- [隐私](#隐私)

## 共同规则

原始 Session 是证据源，扫描脚本只读，不改写、不删除、不补写。解析器遇到未知事件时保留计数和缺口，不根据文本猜测成 Skill 调用。

统一记录：

- `timestamp`、来源 Agent、Session 文件和行号；
- Tool 名称和调用类型；
- 原生 `Skill` 调用及目标 Skill 名称；
- 调用结果、错误和重试；
- 与目标 package 的文件时间线。

## Claude Code

典型输入是 `~/.claude/projects/<encoded-project>/<session-id>.jsonl`。重点识别：

- `message.content[]` 中的 `tool_use`；
- `tool_use.name == "Skill"`；
- `tool_use.input.skill`；
- `tool_result` 和错误文本；
- 同一时间窗口内的文件/manifest 变化。

仅看到 `Read`、`Bash`、`Write` 或 `Edit` 不等于看到原生 `Skill` 调用。父级读取子 Skill 文件后运行脚本，必须标记为“脚本/手工路径”，不能升级为 `runtime-proven`。

## Codex

Codex 支持两种输入：

1. 直接提供 rollout JSONL；
2. 提供 `--thread-id`，脚本从 `~/.codex/state_5.sqlite` 的 `threads.rollout_path` 只读解析 rollout 文件。

Codex 数据库只用于解析 thread 元数据和 rollout 路径，不修改数据库。rollout 中常见事件包括：

- `session_meta`；
- `response_item`；
- `payload.type == "custom_tool_call"`；
- `custom_tool_call_output`。

当前 Codex 工具层出现 `exec` 并不等于出现原生 `Skill` 调用。只有事件本身的工具名是 `Skill`，或运行时明确提供了可识别的 Skill 调度事件，才计入 `native_skill_calls`。

## session-analyzer

`session-analyzer` 负责发现 Codex、Claude Code 和 Antigravity 的 Agent → 项目 → Session 盘点。Skill Doctor 复用它的扫描 JSON：

```bash
bash /Users/jiyi/Projects/active/jiyi-skills/session-analyzer/scripts/run.sh \
  scan.py --json-out <SCAN_JSON>
```

扫描 JSON 可以绑定 Agent、项目、Session 标题、时间和大小，但不替代原始 transcript。要诊断 Tool/Skill 调用，仍需提供 Claude JSONL、Codex rollout JSONL 或可解析的 Codex thread ID。

不要调用 `session-analyzer` 的删除、清理、服务或报告入口来完成 Skill Doctor 诊断；它们不属于本 Skill 的职责。

## 隐私

报告中禁止写入：

- access token、API key、cookie、Authorization；
- 完整用户 prompt、模型响应或工具参数；
- 可直接复用的签名 URL；
- 与诊断无关的文件正文。

只保留：脱敏摘要、文件相对路径、行号、事件类型、调用 ID 摘要、文件大小/时间和必要的哈希。
