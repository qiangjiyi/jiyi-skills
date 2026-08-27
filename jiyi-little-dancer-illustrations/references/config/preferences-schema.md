# 图像后端配置

小舞伴 Skill 把“文章配图决策”和“实际生图后端”分开。后端可以由当前调用方传入，也可以由小舞伴自己的 `EXTEND.md` 配置；没有配置时默认使用 `auto`。

## 配置字段

```yaml
preferred_image_backend: auto
image_concurrency: 1
```

允许值：

- `auto`：由当前运行时选择可用的原生图像 Skill，并在 handoff 中记录实际选择；
- `ask`：需要用户确认时使用，不适合无人值守流水线；
- `<backend-id>`：固定使用指定的原生图像 Skill，例如 `baoyu-image-gen`。

`image_concurrency`（1-4，默认 1）控制同一轮多张插图的生成并发数：1 为逐张串行；大于 1 时，允许在所有 prompt 通过 `validate-prompts.mjs` 校验后并发调用同一后端。并发只影响生成速度，不改变每张图的 prompt、参考图和命名规则；后端限流或出现失败时回退为串行重试。

## 解析优先级

从高到低：

1. 当前请求传入的后端 override；
2. 项目级 `.jiyi-skills/jiyi-little-dancer-illustrations/EXTEND.md`；
3. `${XDG_CONFIG_HOME:-$HOME/.config}/.jiyi-skills/jiyi-little-dancer-illustrations/EXTEND.md`；
4. `$HOME/.jiyi-skills/jiyi-little-dancer-illustrations/EXTEND.md`；
5. 默认值 `auto`。

公众号生产流水线不设置跨 Skill 的统一后端。被调用的下游 Skill 各自读取自己的 `EXTEND.md` 和运行时规则；小舞伴只负责记录自己实际解析出的后端，不读取父级 manifest 的图像后端字段。

## 执行约束

- 选定的后端必须通过当前运行时的原生 `Skill` 工具调用；不能运行后端内部脚本来冒充调用。
- 固定后端不可用时，任务标记为 `blocked`，不能静默切换或手工补图。
- 只有显式选择 `auto` 时，才允许运行时解析可用后端；实际后端必须写入 `illustration-handoff.json`。
- `image_backend.skill` 和 `image_backend.per_image[*].skill` 必须记录实际使用的后端。
