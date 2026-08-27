# 已知坑与修复手册（2026-08-27 全流程首跑实录沉淀）

按阶段排列。每条含现象、根因、正确处理方式。首跑在 ZCode 运行时完成（非 Claude Code），坑按实际踩中顺序收录。

## 跨阶段通用

### 坑 T1：stage-complete 的输出声明是硬门禁

- 现象：输出清单里把 `05-bind-into-skill.png` 误写成 `.json`，stage-complete 直接把阶段标记为 failed。
- 正确做法：提交前 `ls` 核对每个声明文件名；failed 后可重新 stage-start 再 stage-complete 恢复，不需要新建 package。
- 特别注意：illustrate 阶段的 outputs **必须包含 `illustrations/ip-reference.png`**（最小 handoff 检查项），只列生成图片会报 handoff incomplete。

### 坑 T2：invoked-skill 名必须与运行时实际可解析的 Skill 名一致

- 现象：typeset 登记为 `gzh-design-skill`（本机实际名为 `gzh-design`），native-skill-transcript 别名表不认，校验失败。
- 正确做法：stage-start 前先确认 Skill 工具可解析的名字（加载失败会报 Skill not found），登记与实际调用用同一个名字。

### 坑 T3：非 Claude Code 运行时的 transcript 校验

- 现象：validate 的 native-skill-transcript 只扫 `~/.claude/projects/**/*.jsonl`；ZCode 等运行时的真实调用不在那里，检查报 no matching transcript。
- 正确做法（按优先级）：
  1. **首选**：为该运行时补 transcript 适配器，或给 validate-package 增加 `--transcript-file` 覆盖参数（上游改造）；
  2. 次选：由用户明确知情后，用真实调用记录生成 Claude Code 格式镜像（时间戳、skill 名、args 含 package 路径都必须真实），并在完成报告中**披露该适配**；
  3. 禁止：用虚构事件填充镜像；也不得静默略过——那属于伪造完成状态。
- 镜像文件格式注意：`json.dumps` 必须用 `separators=(',',':')`（校验器搜的是无空格的 `"name":"Skill"`）；timestamp 必须是字符串。

### 坑 T4：源文件在 package 外是脆弱依赖

- 现象：prepare 把 manifest.source 指向 package 外的绝对路径并记哈希；用户迭代稿件（内容替换）或清理文件后，source-integrity 失败（ENOENT 或哈希不符）。
- 正确做法：用户改稿属正常流程。改稿后重新计算 sha256 并更新 manifest.source.sha256（当前无官方子命令，脚本更新可接受，需在报告披露）；产物文件缺失时按 manifest 记录路径补回与 package/source.md 一致的副本。长期方案：prepare 应把源文件复制进 package 并指向内部副本。

## cover / illustrate 阶段（图像后端）

### 坑 I1：全局 `.baoyu-skills/.env` 里的 `OPENAI_IMAGE_API_DIALECT=ratio-metadata` 会击穿 codex-cli

- 现象：`Invalid imageApiDialect for codex-cli: ratio-metadata`，生图直接失败。
- 根因：该 env 是给 OpenAI 兼容网关的；baoyu-image-gen 会自读该 .env，进程级 unset 无效。
- 正确做法：显式传 `--imageApiDialect openai-native`。实测该值对 codex-cli（封面，无参考图）和 openai 网关（配图，带参考图）都有效，可作为生图调用的默认兜底参数。

### 坐 I2：`--ref` 与 EXTEND 钉死的 codex-cli 冲突

- 现象：小舞伴强制要求参考图真实传入，但 EXTEND `default_provider: codex-cli` 且 codex-cli 不支持 `--ref`；传了 `--ref` 也不会自动切换后端。
- 正确做法：需要参考图的生图调用必须显式 `--provider <ref-capable>`（本机验证可用：openai 网关 + openai-native）。仅封面这类无参考图任务才可用 codex-cli。

### 坑 I3：封面 Prompt frontmatter 校验要求比模板更严

- 现象：validate-cover-prompt 要求 frontmatter 含 mood/font/aspect/lang，且 `type` 必须填**视觉类型**（metaphor/hero/...）而非 "cover"。
- 正确做法：写 Prompt 时直接按完整八字段写（type/palette/rendering/text/mood/font/aspect/lang），跳过第一次必然失败的往返。

## typeset 阶段

### 坑 P1：主题库与生产校验器的标签黑名单不一致（最重要）

- 现象：gzh-design 橄榄手记组件库大量使用 `figure/figcaption/svg`，其自带校验通过；但生产 validate-package 的 FORBIDDEN_TAGS 把这三者全部判为微信不兼容。
- 根因：两套校验器黑名单不同步，双源真相。
- 正确做法（调度层）：**在调用 gzh-design 的 prompt 中显式附加生产级黑名单**，要求排版时直接用 `section`/`p` 替代 figure/figcaption、不用 svg（装饰位用文字或 span 色块）。这比事后修文件合规（见坑 P2 的越界警示）。
- 若已产出含禁标签的 HTML：把错误清单交回 gzh-design 重排；**不要由父流程直接改 article.html**（首跑曾直接脚本替换，属契约越界，虽内容零变化也应报备）。

### 坑 P2：normalize-typeset 是“移动”不是“复制”

- 现象：排版产物 `*_排版_*.html` 被 normalize 重命名为 article.html 后，对原文件名的后续操作全部 ENOENT。
- 正确做法：任何 HTML 修复都应发生在**排版 Skill 的源产物**上，随后重新 `wrap_preview.py` → 把 `article_预览.html` 改名为 `article-preview.html` → 重跑 validate-package。直接改 article.html 能跑通但违反契约。

## validate / publish 阶段

### 坑 V1：validate 阶段重跑窗口

- 现象：validate 已是 completed 后，stage-start 拒绝重启（Cannot start validate from status completed）。
- 正确做法：validate 的 stage-complete 只在报告 ok:true 后登记；中途失败就让阶段停在 running/failed 并修问题，报告文件本身可以反复重生成。

### 坑 V2：publish 的参数形态

- 正确命令（无 --package 参数）：
  `node publish-wechat-article.mjs --html <pkg>/article.html --cover <pkg>/cover/cover.png --title "<标题>" --account jiyi --summary "<摘要>" --result-file <pkg>/publish-result.json [--dry-run] [--yes]`
- 先 --dry-run（inputsValidated 应为 true），再去掉 --dry-run 加 --yes 真实发布（仅建草稿，单次尝试）。

## 流程设计层（上游改进建议，待技能作者采纳）

1. prepare 应支持"用户改稿"路径：重算 source 哈希的官方子命令，或直接以 package 内 source.md 为唯一真相。
2. validate-package 的 FORBIDDEN_TAGS 应与 gzh-design 主题库对齐（要么放宽 svg/figure，要么通知主题库改造）。
3. native-skill-transcript 应支持运行时适配器或 --transcript-file 参数。
