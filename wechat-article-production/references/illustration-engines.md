# 正文配图引擎选择

正文配图阶段统一使用“一个阶段、两个原生引擎、各自解析图像后端”的设计。生产 Skill 负责选择、传递文章和接收 handoff；真正的分析、构图、提示词、生图和 QA 由被选中的下游 Skill 负责。

图像生成后端属于下游配图 Skill 的内部执行环节，不属于 `wechat-article-production` 的直接调度阶段。父 Skill 不直接调用图像后端；被选中的下游 Skill 必须通过原生 `Skill` 工具读取自己的配置并调用实际后端，再由该后端负责 provider、重试、缓存和参考图规则。

在 Claude Code 中，原生 `Skill` 工具会把下游 Skill 加载到当前 Agent，而不是自动创建一个已经完成的独立子进程。工具返回后，当前 Agent 必须继续执行被加载 Skill 的完整分析、构图、Prompt、生图和 QA 流程；只有下游交付 handoff 后，父流程才恢复机器校验。

## 两个引擎

| Skill | 定位 | 更适合 | 视觉倾向 |
| --- | --- | --- | --- |
| `baoyu-article-illustrator` | 通用文章插画引擎 | 信息图、流程、框架、比较、时间线、叙事场景，以及需要多种风格的文章 | 通过 Type × Style × Palette 组合视觉方案 |
| `jiyi-little-dancer-illustrations` | 吉义“小舞伴”个人 IP 配图引擎 | 个人品牌文章、方法论、工作流、观点、状态变化、抽象隐喻和需要角色参与的内容 | 16:9、纯白留白、黑色手绘线稿、粉色为主的批注、小舞伴的舞蹈身体语言 |

默认选择 `jiyi-little-dancer-illustrations`，因为本公众号内容默认使用吉义个人 IP 和小舞伴视觉体系。只有用户明确要求宝玉配图、通用文章配图或点名 `baoyu-article-illustrator` 时，才切换到宝玉配图。

每个图片下游都必须在完成报告中声明自己实际调用的后端。后端内部使用什么 provider，不由父级或文章配图 Skill 伪造；必须以各自的实际运行记录为准。

## 选择优先级

按以下顺序解析：

1. 自动化调用中的 `--illustration-skill`；
2. 用户当前请求中的明确选择；
3. 默认的 `jiyi-little-dancer-illustrations`。

可以识别的自然语言信号包括：

- `baoyu-article-illustrator`：宝玉配图、宝玉插画、通用文章配图、信息图配图；
- `jiyi-little-dancer-illustrations`：小舞伴、小舞伴配图、我的 IP、个人 IP 配图、吉义 IP、舞蹈感配图。

如果用户同时明确指定两个引擎，以 CLI 参数为准；没有 CLI 参数且自然语言仍然冲突，不要猜测，应询问用户。

## 自动化参数

初始化 execution manifest 时传入：

```bash
node <THIS_SKILL_ROOT>/scripts/execution-manifest.mjs init \
  --file <PACKAGE_DIR>/execution-manifest.json \
  --package-dir <PACKAGE_DIR> \
  --execution-stages prepare,format,cover,illustrate,typeset,validate,publish \
  --illustration-skill jiyi-little-dancer-illustrations
```

可用值只有：

- `baoyu-article-illustrator`；
- `jiyi-little-dancer-illustrations`。

不传 `--illustration-skill` 时，脚本写入默认值 `jiyi-little-dancer-illustrations`。

## 统一 handoff

无论使用哪个引擎，生产 package 都需要最终具备：

```text
illustrations/
  outline.md
  outline-validation.json # 小舞伴模式必需
  prompts/
  prompt-validation.json  # 小舞伴模式必需
  logs/*.jsonl  # 图像后端运行日志，不放进 prompts/
  illustration-handoff.json  # 小舞伴模式必需
  *.png
article-illustrated.md
```

小舞伴模式额外要求：

```text
illustrations/ip-reference.png
illustrations/illustration-handoff.json
```

`ip-reference.png` 必须是小舞伴 Skill 内置的
`assets/little-dancer-reference-sheet.png` 的 package 副本。它必须作为真实图像输入传给生图后端；提示词中只出现一个文件路径，不算使用参考图。handoff 顶层还必须记录：

```json
{
  "reference_asset": {
    "source": "<skill>/assets/little-dancer-reference-sheet.png",
    "package_path": "illustrations/ip-reference.png",
    "used_for_identity": true
  },
  "image_backend": {
    "skill": "<actual-backend-used-by-little-dancer>",
    "per_image": [
      {
        "prompt_file": "illustrations/prompts/01-example.md",
        "output": "illustrations/01-example.png",
        "skill": "<actual-backend-used-for-this-image>",
        "aspect": "16:9",
        "reference_used_for_identity": true
      }
    ]
  },
  "prompt_validation": "illustrations/prompt-validation.json",
  "identity_anchors": ["双侧小马尾", "浅粉蝴蝶结发带与小发髻", "浅粉蝴蝶结短袖上衣", "浅粉长裤与橙色爱心", "白色运动鞋"]
}
```

下游 Skill 可以按自己的规则生成中间文件或使用自己的输出目录，但在被 `wechat-article-production` 原生调用时，必须自己完成上述 handoff。生产 Skill 只在完成下游调用后检查输出是否存在和路径是否可解析；不得改写图片内容、覆盖位图文字、决定插图位置、插入图片引用或替换下游 Skill 的创作决策。

如果下游只交付图片或 outline，没有交付 `article-illustrated.md`，`illustrate` 必须保持 `failed`/`blocked`，不能由主 Agent 用临时脚本补出 handoff。

## 配图阶段完成证据

配图阶段至少需要三类证据：

1. manifest 中存在准确的原生配图 Skill 调用；
2. package 中存在 outline、prompt、最终图片和 `article-illustrated.md`；
3. 下游完成报告注明实际图像后端，或明确说明运行时采用的原生后端分支。

只看到图片文件、只看到图像后端日志，或只看到父 Skill 手写的 `backend=...`，都不能单独证明配图 Skill 完整执行。

manifest 必须同时记录：

```json
{
  "scope": {
    "illustration_skill": "jiyi-little-dancer-illustrations"
  },
  "stages": [
    {
      "id": "illustrate",
      "skill": "jiyi-little-dancer-illustrations"
    }
  ]
}
```
