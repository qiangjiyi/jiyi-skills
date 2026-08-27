# 诊断契约

## 目录

- [证据等级](#证据等级)
- [报告结构](#报告结构)
- [状态与严重度](#状态与严重度)
- [实施契约](#实施契约)

## 证据等级

| 等级 | 含义 | 可以证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| `runtime-proven` | 原始 Session 中直接出现 Tool/Skill 事件，且时间线可匹配 | 某个调用事件确实被记录 | 子 Skill 内部每一步都成功，除非有子 Skill handoff/报告 |
| `source-inferred` | 源码、配置或脚本表达了意图 | 目标 Skill 设计上打算如何执行 | 历史运行真的按此执行 |
| `artifact-only` | package 中存在文件或报告 | 某个文件在指定时间存在 | 文件由哪个 Skill 生成、业务逻辑是否完整执行 |
| `missing-evidence` | 应有证据但未找到 | 证据缺口本身 | 调用没有发生，除非运行时数据完整可读 |
| `conflict` | 源码、Session、manifest 或文件状态不一致 | 存在不一致，需要人工确认 | 哪一方一定正确 |

规则：

1. 只有 `runtime-proven` 加上完整 handoff，才能把子 Skill 判断为“已调用并完成”；
2. 只有文件存在不能升级 `artifact-only`；
3. Session 文件不可读时，必须报告 `missing-evidence`，不能推断调用成功或失败；
4. 源码中写着“原生调用”但没有运行时事件，仍然是 `source-inferred`；
5. 子 Skill 返回错误后父级继续写产物，至少报告 P1，并检查是否应为 `blocked`/`failed`。

## 报告结构

### `source-graph.json`

```json
{
  "schema_version": "1.0",
  "target": "/absolute/path/to/skill",
  "skill": "skill-name",
  "files": [{"path": "SKILL.md", "bytes": 123}],
  "skills": [{"name": "child-skill", "evidence": []}],
  "edges": [{"from": "parent", "to": "child-skill", "relation": "declared-call", "evidence": {}}],
  "artifacts": [{"path": "handoff.json", "evidence": []}],
  "control_flow": {},
  "findings": [],
  "limitations": []
}
```

### `execution-trace.json`

Session 归一化只保留：

- Agent 类型、Session 路径、行数、大小、时间；
- Tool 名称、调用类型、Skill 名称、调用 ID 的脱敏摘要；
- 参数摘要，不保留完整 prompt 或敏感值；
- Tool/Skill 结果的状态摘要；
- 错误样事件；
- session-analyzer 快照中匹配到的 Agent/项目/Session 元数据；
- package 文件路径、大小、修改时间和安全的 manifest 摘要。

### `diagnostic-report.json`

```json
{
  "status": "needs-optimization",
  "findings": [
    {
      "id": "RUN-002",
      "severity": "P1",
      "confidence": "high",
      "evidence_type": "missing-evidence",
      "title": "Session 中没有识别到原生 Skill 调用",
      "impact": "无法证明子 Skill 被真正触发",
      "evidence": [],
      "recommendation": "补充原生调度和 transcript 校验"
    }
  ],
  "recommendation": {
    "sequence": [],
    "apply_requires_confirmation": true
  }
}
```

## 状态与严重度

| 等级 | 语义 | 默认处理 |
| --- | --- | --- |
| P0 | 入口、证据或安全边界缺失，无法可信诊断/执行 | 阻塞，不实施 |
| P1 | 会导致子 Skill 未执行、伪完成、错误继续或关键 handoff 丢失 | 优先修复，未修复前不报告完成 |
| P2 | 可观测性、并行汇聚、重试或产物校验不完整 | 纳入优化计划 |
| P3 | 文档、命名或可维护性问题 | 可延后处理 |

报告状态：

- `blocked`：存在 P0 或输入不足；
- `needs-optimization`：存在 P1；
- `healthy-with-limitations`：没有已识别 P0/P1，但仍可能存在未覆盖的运行时事件。

## 实施契约

诊断 Skill 不直接修改目标 Skill。确认后的实施由父 Agent 执行：

1. 展示拟改文件、关键 diff、不会改动的文件和验证命令；
2. 用户明确确认；
3. 只执行确认范围内的 patch；
4. 运行目标 Skill 的自带检查和 Skill Doctor 重新扫描；
5. 使用 Claude Code 与 Codex 的 fixture/真实 Session 回归；
6. 回归失败时保持 `failed`，不手工修正报告。
