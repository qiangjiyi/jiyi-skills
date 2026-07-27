# 微信发布配置

## 配置文件位置

按以下顺序查找（进程环境变量优先级最高）：

1. `--env-file` 指定的文件
2. `WECHAT_PUBLISHER_ENV_FILE` 指定的文件
3. 当前工作目录或文章 HTML 所在目录的 `.env.local`、`.env`
4. `~/.config/wechat-pipeline/.env.local`、`~/.config/wechat-pipeline/.env`

推荐放在 `~/.config/wechat-pipeline/.env`。修改前必须取得用户明确同意。

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
| `WECHAT_API_BASE` | 微信 API 地址 | `https://api.weixin.qq.com` |
| `WECHAT_PROXY_URL` | 代理 URL | — |
| `WECHAT_PIPELINE_OUTPUT_DIR` | 产物根目录 | `$XDG_DATA_HOME/wechat-pipeline/exports` 或 `$HOME/.local/share/wechat-pipeline/exports` |
| `WECHAT_PIPELINE_ILLUSTRATION_MODE` | 配图模式 | `auto-recommended` |
| `WECHAT_PIPELINE_LAYOUT_MODE` | 排版模式 | `auto-recommended` |
| `WECHAT_PIPELINE_RECOMMENDATION_MODE` | 统一模式默认值 | — |
| `WECHAT_UPLOAD_CONCURRENCY` | 图片上传并发数（1-8） | `3` |
| `WECHAT_PIPELINE_MINIMAL_RUNTIME` | 隔离诊断模式 | `0` |
| `WECHAT_PIPELINE_AUTOCORRECT_CLI` | autocorrect-node CLI 路径 | 自动扫描 npx 缓存 |

模式可选值：`auto-recommended`（自动采用推荐项）、`confirm`（保留确认门）。封面图不使用上述变量，`baoyu-cover-image` 原生支持 `quick_mode`。

## 作者映射

正常发布只传 `--account`，作者来自 `WECHAT_<ACCOUNT>_AUTHOR`。`--author` 仅用于临时覆盖。作者字段最长 16 个字符，缺少映射时预检和发布都会停止。
