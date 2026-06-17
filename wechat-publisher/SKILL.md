---
name: wechat-publisher
description: 把已经做好的内容发布到微信公众号草稿箱。支持两种模式——微信贴图（article_type=newspic，短文 + 1-20 张图片）和微信公众号文章（article_type=news，从 markdown 渲染带主题/配色的 HTML）。两种模式都通过 HTTP Worker 代理通道调用微信 API，本地不直连。当用户说「微信贴图一键发布」「把图片卡片发公众号」「发到公众号草稿箱」「newspic 草稿」「发布公众号文章」「markdown 渲染成公众号文章」「把 markdown 发公众号」时使用。本技能只负责发布，不负责生成或渲染图片。
---

# 微信公众号发布（双模式）

把本地已有内容（图片卡片 / markdown 文章）发布到微信公众号草稿箱。两种 `article_type` 走同一个 HTTP 代理通道。

职责边界：
- **贴图模式**（newspic）：源文件 + 本地图片 → 上传成永久素材拿 media_id → 调 `draft/add` 建图贴草稿。**不生成图片、不渲染卡片。**
- **文章模式**（news）：markdown → 渲染成带主题/配色的 HTML → 上传内联图片并改写 src 为 mmbiz URL → 上传封面 → 调 `draft/add` 建文章草稿。**主题和颜色由内置 4 套主题 + 13 种预设提供。**

两种模式**不互相替代**：
- 贴图适合短贴（朋友圈卡片、9 张图介绍），content 是纯文本
- 文章适合长文（教程、深度分析），content 是带样式的 HTML

## 与 baoyu-post-to-wechat 的差异

| 维度 | wechat-publisher（这个） | baoyu-post-to-wechat |
|------|--------------------------|----------------------|
| 贴图（newspic） | ✓ 一等公民 | ✗ 标 ✗，实际代码支持但 SKILL.md 误导 |
| 文章（news） | ✓ 主题/配色 + 4 套主题 | ✓ 同样有 |
| 代理方式 | **HTTP Worker 代理**（信封协议） | SSH SOCKS5 隧道 |
| 部署成本 | 部署一个 Cloudflare Worker | 任意一台 sshd 可达的机器 |
| 入口 | Python `publish.py` | TypeScript `wechat-api.ts` |
| 主题来源 | 借用 `baoyu-md` npm 包 | 借用 `baoyu-md` npm 包（同源） |

## 子命令

```bash
publish.py newspic [args...]    # 贴图（image-text, article_type=newspic）
publish.py article  [args...]    # 文章（markdown, article_type=news）
```

## 工作流

1. **确定输入**，按子命令分：
   - `newspic`：可选源文件（`.md` / `.yaml` / `.yml` / `.json`）或显式 `--title/--content/--image`。不传则读当前目录 `source.md`。
   - `article`：必须显式 `--markdown <path>`，主题/颜色通过 `--theme` / `--color` 传。
2. **加载 env 配置**：依次查找「源文件旁的 `.env.local` → 源文件旁的 `.env` → 技能目录下的 `.env.local` → 技能目录下的 `.env`」，最后回落到进程环境变量。
3. **解析账号**：命令行 `--account` → 源文件 `account`（newspic 模式）→ 唯一一个已配置的账号。配置了多个又没指定则报错。
4. **校验 + 渲染**（仅 article）：调 `scripts/render_markdown.mjs`（薄包装 `baoyu-md`）生成带主题/配色的 HTML + 图片占位符。
5. **发布前确认**：除非用户明确带 `--yes`，先展示目标账号、标题、正文/HTML 预览、图片数、封面来源，等用户确认。发布是对外动作（写入公众号草稿箱），不要跳过确认。
6. 运行 `scripts/publish.py` 对应子命令。
7. 回报返回的草稿 `media_id`。

## 贴图模式（newspic）

### source.md 格式（推荐，保留不变）

```markdown
---
account: personal
author: "即刻内容工作室"
images:
  - cards/card-01.png
  - cards/card-02.png
---

# 这周值得收藏的 AI 工具

整理成 6 张卡片，适合快速看完。
```

`title` 也可以写在 frontmatter 里；H1 解析优先。`content` 必须是**纯文本**（newspic 限制 ≤1200 字符，title 限制 ≤20 字符，images 1-20 张）。

### 直接传参

```bash
python3 publish.py newspic --account personal \
  --title "这周值得收藏的 AI 工具" \
  --content "整理成 6 张卡片，适合快速看完。" \
  --image cards/card-01.png --image cards/card-02.png
```

`--image` 可重复多次。

## 文章模式（article）

### 输入：markdown

```markdown
---
title: 文章标题
author: 作者名
coverImage: imgs/cover.png
description: 文章摘要（可选，不写则从首段自动生成）
---

# 文章标题

正文段落。

![图片说明](imgs/inline-01.png)

## 子标题

正文继续。

[外链](https://example.com) 会被默认改成底部引用 [1]。

```js
console.log("代码块带语法高亮");
```
```

`title` 优先级：`--title` > frontmatter `title` > 第一个 H1/H2 > 文件名。
`author` 优先级：`--author` > frontmatter `author`。
`cover` 优先级：`--cover` > frontmatter `coverImage/featureImage/cover/image` > `imgs/cover.png` > 第一张内联图。

### 主题与颜色

**主题**（4 选 1）：`default` / `grace` / `simple` / `modern`
- `default`：朴素基础款
- `grace`：复古优雅（带装饰元素）
- `simple`：极简纯净
- `modern`：现代几何（强色块）

**颜色**（13 预设或 hex）：
- 预设：`blue` / `green` / `vermilion` / `yellow` / `purple` / `sky` / `rose` / `olive` / `black` / `gray` / `pink` / `red` / `orange`
- 或写 `#rrggbb` 自定义

```bash
python3 publish.py article \
  --markdown article.md \
  --theme grace \
  --color blue
```

### 命令行覆盖 frontmatter

`--title` / `--author` / `--summary` / `--cover` 都优先于 frontmatter。

### 引用处理（cite）

默认情况下，markdown 里的普通外链会被转成底部引用（参考 baoyu-md 的 `citeStatus: true`）。如果想保留内联链接，加 `--no-cite`。

## 代理通道（HTTP Worker）

两种模式都通过同一个 `WECHAT_PROXY_URL` 调用微信 API：

- **JSON 调用**：`POST { "url", "method", "data" }`
- **上传调用**：`POST { "url", "method": "UPLOAD", "fileData" (base64), "fileName", "mimeType", "fieldName": "media" }`

未设置代理时直连 `https://api.weixin.qq.com`。所有请求都带浏览器 `User-Agent`，避免代理前置的 Cloudflare 把脚本默认 UA 当机器人拦截。

### 贴图模式涉及的端点

- `cgi-bin/token`（拿 access_token）
- `cgi-bin/material/add_material`（永久图片素材）
- `cgi-bin/draft/add`（`article_type=newspic`）

### 文章模式额外涉及的端点

- `cgi-bin/media/uploadimg`（正文图片，临时素材，**返回 mmbiz URL 即可直接用**）
- `cgi-bin/material/add_material`（封面，永久素材）

## env 配置

密钥放在 git 之外的真实 `.env.local`（或 `.env`），仓库里只提交 `.env.example`。

```env
WECHAT_PROXY_URL=https://your-proxy.example.com/
WECHAT_API_BASE=https://api.weixin.qq.com

WECHAT_ACCOUNTS=personal,company

WECHAT_PERSONAL_APP_ID=wx...
WECHAT_PERSONAL_APP_SECRET=...

WECHAT_COMPANY_APP_ID=wx...
WECHAT_COMPANY_APP_SECRET=...
```

账号别名映射规则：`别名`大写、非字母数字转下划线后，拼成 `WECHAT_<别名>_APP_ID` / `WECHAT_<别名>_APP_SECRET`。单账号也可直接用 `WECHAT_APP_ID` / `WECHAT_APP_SECRET`。

可选的直传 token 字段（配了就跳过用 appid/secret 换 token）：

```env
WECHAT_PERSONAL_ACCESS_TOKEN=
WECHAT_ACCESS_TOKEN=
```

绝不要把真实密钥写进 `SKILL.md`、`.env.example`、要分享的源文件或对话输出里。

## 命令汇总

`publish.py` 路径：`<技能目录>/scripts/publish.py`。

```bash
# 贴图
python3 publish.py newspic                        # 读当前目录 source.md
python3 publish.py newspic source.yaml --dry-run  # 显式源文件
python3 publish.py newspic --account personal --yes

# 文章
python3 publish.py article article.md --theme grace --color blue
python3 publish.py article article.md --theme default --cover imgs/cover.png
python3 publish.py article article.md --dry-run --account jiyi

# env 显式指定（通常不用，env 放在技能目录会被自动发现）
python3 publish.py newspic --env-file /path/to/.env
```

## 依赖

- Python 3.10+
- Node.js（用于 article 模式跑 `baoyu-md`）
- `npm install` 安装 `baoyu-md` + `baoyu-chrome-cdp` 到技能目录的 `node_modules/`
