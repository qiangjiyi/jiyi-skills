---
name: xhs-card-publisher
description: 将中文文章、粘贴内容或 Markdown 文件自动格式化为白底知识卡风格的小红书多图；调用 jiyi-little-dancer-illustrations 生成并插入小舞伴配图，自动分页并导出逐张 PNG。用户提到“小红书图文排版”“文章转小红书图片”“Markdown 转卡片”“自动配图并导出图片”时使用。
---

# 小红书卡片发布器

将内容完成为可直接上传小红书的逐张 PNG，不创建 ZIP。默认复刻“写了就发”的白底知识卡视觉：1728×2304、白底、深灰文字、简洁作者栏、自然中文断行、标题层级和自动分页。

## 输入与输出

接受粘贴正文或 `.md` 路径。新任务必须创建 package：

```bash
node <THIS_SKILL_ROOT>/scripts/prepare-package.mjs --source <SOURCE.md> --slug <SLUG>
```

脚本输出的路径即 `<PACKAGE_DIR>`。最终输出固定为：

```text
/Users/jiyi/Workspace/exports/xhs-cards/<slug>-<timestamp>/
  source.md
  formatted.md
  article-illustrated.md
  illustrations/
  cards/
    01.png
    02.png
  manifest.json
```

不得创建 ZIP。保留 `formatted.md`、`article-illustrated.md` 与 `manifest.json`，使图片可以追溯和复渲染。

## 主流程

1. 读取原文，提炼标题、核心观点、段落结构与适合强调的短语。
2. 写入 `formatted.md`：原文保护优先。只允许添加自定义行内视觉标记：

   ```md
   {{bg:#FFF0B8|重点句}}
   {{color:#C2410C|强调色}}
   {{underline:solid|关键动作}}
   ```

   禁止增删、改写、压缩、重排或拆分用户原文的字句、段落、列表、表格、事实、数字、引用或链接；不要新增 `**`、`*`、标题、列表、引用、图片或表格语法。已有 Markdown 必须原样保留。标题、段落、表格、列表和顺序必须保持不变。

   写完后必须运行以下硬校验；仅输出 `passed: true` 才能进入配图和渲染：

   ```bash
   node <THIS_SKILL_ROOT>/scripts/verify-format-preservation.mjs \
     --source <PACKAGE_DIR>/source.md \
     --formatted <PACKAGE_DIR>/formatted.md
   ```

   校验会剥离允许新增的 `{{bg}}`、`{{color}}` 和 `{{underline}}` 标记后逐字比较。任何差异均视为失败，停止流程并保留差异，不得自行“修复”原文。
3. 原生调用 `$jiyi-little-dancer-illustrations`，明确说明：这是 `xhs-card-publisher` 的上游调用；输入为 `<PACKAGE_DIR>/formatted.md`；将其生成图和已插图文章交付到 `<PACKAGE_DIR>/illustrations/` 与 `<PACKAGE_DIR>/article-illustrated.md`。不得读取其 SKILL.md 后自行模拟配图或生图。
4. 若配图 Skill 返回 blocked / failed，停止，不用占位图假装完成。若其交付缺少 `article-illustrated.md`，停止并报告缺失的 handoff。
5. 使用渲染器：

   ```bash
   node <THIS_SKILL_ROOT>/scripts/render-cards.mjs \
     --input <PACKAGE_DIR>/article-illustrated.md \
     --output <PACKAGE_DIR>/cards \
     --title <ARTICLE_TITLE>
   ```

6. 检查命令生成的 `manifest.json`：每一页都必须是 1728×2304 PNG；确认至少输出一张图。只在通过后报告完成。

## 渲染约束

- 将图片单独放在一行，使用标准 Markdown：`![说明](相对路径)`；配图交接文件必须采用相对路径。
- 以 3:4（1728×2304）为标准高度。普通正文优先保持标准高度，当前页容纳不下时允许在段落内自然续到下一张卡片，但必须优先在句号、问号或叹号后换页，禁止在逗号、顿号等未完成句子处切断；只有单句本身超过标准卡片高度时，才允许句内兜底分页。绝不为了凑完整段落拉长页面。仅当完整配图可利用当前页已有的大块余白、且总高不超过额外 1/2（最高 1728×3280）时，才允许这张卡片按实际内容加长；表格和配图都不可拆分。
- 分页采用顺序填充：不为图片或表格预留空页；下一个块先尝试放入当前卡片，只有超过对应上限才换页。原 Markdown 的段落空行必须在卡片中显示为明确段间留白；图片与相邻正文则不额外插入空行。卡片四周使用稳定边距：上下各 34px、左右各 54px；末块另保留最少 26px 的内部安全留白，避免高亮、下划线或图片贴边造成“像被截断”的视觉。
- 默认每页显示双行作者栏：头像、昵称、金色视频号风格认证标识，以及一行简介文案（默认“关注我，把 AI 变成你的生产力”）。作者栏三项均可配置：`--avatar` 指定头像路径、`--name` 指定账号名称、`--bio` 指定名称下方简介；未传时分别使用 Skill 默认头像、“吉义AI”和默认简介。标题、二级标题、引用、图片前留出层次间距。
- 正文默认使用随 Skill 打包的霞鹜文楷手写体；文章标题与作者栏保留清晰的无衬线字。页面使用纯白背景，不使用整体圆角或外层边框。
- 配图是不可拆分块：任意配图放不下时必须整体进入下一页，禁止裁切、跨页或截断；使用无圆角、无边框的固定 3:4 全宽容器，图片完整 contain 显示。
- 配图前后不插入额外空行：配图与相邻正文块的间距只由原 Markdown 的既有块关系决定，渲染器不得额外增加图片外边距。
- Markdown 表格按独立表格块渲染：以 `| --- |` 分隔行识别表头；列等宽、单元格自动换行、表头浅蓝底、细灰边框、行高随最高单元格自适应。表头与单元格使用正文同款霞鹜文楷手写体，仅以字号与字重区分层级。表格不可跨页，放不下时整体进入下一页；单张卡片也放不下时停止并提示用户拆分表格。
- 排版只通过 Canvas/HTML 渲染规则改变视觉效果：暖白底、细边框、圆角图片容器、手写正文、无衬线标题、标题色条、淡色分节标签、引用与列表卡片；不得以“美化”为由重组正文内容。
- 不在图片上叠加额外标题、Logo、水印或“第 N 页”。

## 调整与复渲染

已有 package 的文字、图片或配色变更后，只更新相应 Markdown/素材并重新运行 `render-cards.mjs`。渲染器会安全替换 `cards/` 中的同名 PNG；不要删除 package 中的源文、提示词、配图 handoff 或 manifest。

## 完成口径

报告：文章标题、图片张数、`cards/` 目录与 `manifest.json` 的绝对路径；指出是否已完成小舞伴配图。不要把“已生成 HTML”或“可以预览”当作完成，必须存在实际 PNG。
