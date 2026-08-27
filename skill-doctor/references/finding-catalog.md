# 问题目录

## 源码问题

| ID | 等级 | 触发条件 | 建议 |
| --- | --- | --- | --- |
| `SRC-001` | P0 | 缺少 `SKILL.md` | 补充合法入口和 frontmatter |
| `SRC-002` | P1 | 出现子 Skill 名称，但没有明确原生 `Skill` 调度证据 | 使用运行时原生 Skill 工具，并记录真实调用 |
| `SRC-003` | P1 | 子 Skill 失败后存在手工、模拟、补写或脚本 fallback | 保持 blocked/failed，不由父级模拟子 Skill |
| `SRC-004` | P1 | 有 completed/产物声明，但缺少 exists/stat/manifest 等机器校验 | 把存在性、非空、路径边界交给脚本 |
| `SRC-005` | P1 | 声明并行，但没有汇聚、全量结果或部分失败规则 | 增加 join、汇聚 handoff 和失败传播 |
| `SRC-006` | P2 | 没有失败、阻塞或重试契约 | 为每个阶段定义错误状态和重试边界 |
| `SRC-007` | P2 | 子 Skill 多，但没有可识别文件型 handoff | 定义稳定产物或机器可读 manifest |

## 运行时问题

| ID | 等级 | 触发条件 | 建议 |
| --- | --- | --- | --- |
| `RUN-001` | P1 | 没有可读原始 Session | 补充 JSONL、rollout 或 thread ID |
| `RUN-002` | P1 | Session 中没有原生 Skill 调用 | 检查调度器、Skill 名称和 transcript 适配 |
| `RUN-003` | P1 | Session 包含错误/失败样事件 | 绑定阶段状态，失败不得继续伪完成 |
| `RUN-004` | P1 | 有产物但没有原生 Skill 调用 | 标记 artifact-only，检查是否父级/脚本代做 |
| `RUN-005` | P1 | manifest completed 与 Session/文件时间线冲突 | 以实际证据为准，修复完成门禁 |
| `RUN-006` | P2 | 子 Skill 调用存在，但缺少预期 handoff | 保持 partial/blocked，让子 Skill 自己补齐流程 |

## 结论措辞

使用以下准确表达：

- “源码声明了……”而不是“已经执行……”；
- “Session 中识别到原生调用事件……”而不是“业务逻辑完整成功……”；
- “产物存在，但目前只能证明 artifact-only……”；
- “缺少原始 Session，因此无法证明……”；
- “建议将……交给下游 Skill 自己完成，父级只负责调度和接收 handoff”。

禁止使用：

- “看起来应该执行了”；
- “有图片所以配图 Skill 肯定执行了”；
- “我已经帮子 Skill 补齐了”；
- “脚本返回 0 所以原生 Skill 已完成”。
