# 微信发布配置

## 配置文件位置

按以下顺序查找（进程环境变量优先级最高）：

1. `--env-file` 指定的文件
2. `WECHAT_ARTICLE_PRODUCTION_ENV_FILE` 指定的文件
3. 当前工作目录或文章 HTML 所在目录的 `.env.local`、`.env`
4. `~/.config/wechat-article-production/.env.local`、`~/.config/wechat-article-production/.env`

推荐放在 `~/.config/wechat-article-production/.env`。迁移或修改配置文件前必须取得用户明确同意。

## 多账号配置

```dotenv
WECHAT_ACCOUNTS=jiyi,xiyue

WECHAT_JIYI_APP_ID=
WECHAT_JIYI_APP_SECRET=
WECHAT_JIYI_AUTHOR=吉义

WECHAT_XIYUE_APP_ID=
WECHAT_XIYUE_APP_SECRET=
WECHAT_XIYUE_AUTHOR=兮悦
WECHAT_XIYUE_AUTHOR_BIO=
```

账号标识转大写，非字母数字字符替换为下划线（如 `brand-cn` → `WECHAT_BRAND_CN_*`）。

不要配置 `WECHAT_ACCESS_TOKEN`；脚本每次发布时动态获取。

## 可选配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WECHAT_ARTICLE_PRODUCTION_ENV_FILE` | 指定发布配置文件 | — |
| `WECHAT_API_BASE` | 微信 API 地址 | `https://api.weixin.qq.com` |
| `WECHAT_PROXY_URL` | 代理 URL | — |
| `WECHAT_UPLOAD_CONCURRENCY` | 默认图片上传并发数（1-8） | `3` |

本 Skill 的 standalone publisher 不读取配图、排版、Stage Runner 或产物目录模式变量；这些阶段由各自下游 Skill 和当前工作流管理。命令行 `--upload-concurrency` 会覆盖环境变量值。

`publish-wechat-image-text.mjs` 使用同一份账号配置，但不读取作者字段：微信贴图以图片、标题与纯文本说明创建 `article_type=newspic` 草稿。

## 作者映射

正常发布只传 `--account`，作者来自 `WECHAT_<ACCOUNT>_AUTHOR`。`--author` 仅用于临时覆盖。作者字段最长 16 个字符，缺少映射时预检和发布都会停止。
