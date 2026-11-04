---
name: wx-video-downloader
description: 通过本机 wx-video 服务（wx_channels_download）的 MCP 接口下载微信视频号视频，也支持抖音、公众号、知乎内容链接。支持三种交付模式：视频下载（实测画质自动选最高、按需解密）、封面图下载、MP3 音频提取（本地 ffmpeg）。自动管理服务启停、解析链接后直接下载。用户说「下载视频号视频」「下载这个视频」「抓下这条抖音」「下载公众号文章里的视频」「下载封面」「转成 MP3 / 提取音频」或给出 channels.weixin.qq.com / 抖音分享链接要求下载时使用。
---

# 视频号视频下载器

把用户给的内容链接经本机 wx-video 后端的 MCP 接口解析后，自动选实际最高画质下载为本地文件。
后端 = `~/.local/share/qiaomu-wx-video/backend/current`（由 `~/.local/bin/wx-video` 安装维护），
MCP 端点 = `http://127.0.0.1:2022/mcp`（streamable HTTP，无鉴权，仅本机回环）。

本 Skill 只做编排与确认；解析/下载默认通过 `scripts/mcp.mjs` 直连 MCP 完成，不操控浏览器，不读管理页 UI。两个例外：Plan B 直连下载（curl，见已知坑 A）与 MP3 提取（本地 ffmpeg），均在对应小节内给了完整命令。

## 支持范围

以 `get_platform_status` 实时结果为准，通常包括：视频号（分享链接 + 页面抓取）、抖音、公众号、知乎。

## 下载目录（按平台分目录）

根目录 `DOWNLOAD_ROOT=/Users/jiyi/Workspace/downloads`，按 `fetch_content` 结果里的平台标识（`platform` / `platform_id`；缺失时按链接域名判断）落到子目录：

| 平台标识 | 链接特征 | 子目录 |
|---|---|---|
| `wxchannels` | `weixin.qq.com/sph/`、`channels.weixin.qq.com` | `wechat-channels` |
| `douyin` | `douyin.com`、`v.douyin.com` | `douyin` |
| `wxmp` | `mp.weixin.qq.com` | `wechat-mp` |
| `zhihu` | `zhihu.com` | `zhihu` |
| 其他 | — | `others` |

下文的 `<平台目录>` 即 `$DOWNLOAD_ROOT/<子目录>`。下载前先 `mkdir -p "<平台目录>"`。视频、封面、MP3 都放进同一个平台目录。用户明确指定其他目录时以用户为准。

后端 `config.yaml` 的 `download.dir` 仍是 `~/Downloads`，只影响不经本 Skill 的下载（如微信里悬浮下载按钮）；本 Skill 每次都显式传 `download_dir`。

## 主流程

以下命令中的 `scripts/` 均指本 Skill 安装目录下的 `scripts/` 子目录（与 SKILL.md 同级），请按实际安装路径解析后执行。

1. **前置检测与自举**：先跑 `bash <skill目录>/scripts/service.sh ensure`（只检测不改动的安全操作，可随时执行）。退出码 0 → 依赖齐备，继续；退出码 2 → 把报告中的安装动作（下载后端二进制约 17MB / 复制 wx-video 命令 / brew 装 Node）展示给用户，**取得明确同意后**执行 `service.sh ensure --install`。报告中的 `!` 项（证书未装）提示用户：首次启动后端会弹系统授权，或先在终端运行一次 `sudo wx-video`。
2. **服务自检**：`service.sh status`；未运行则 `service.sh start`（启动会把系统代理指向 127.0.0.1:2023，这是抓取机制的一部分，勿手动改动。停止时由 `wx-video` 按「启动前快照」原样恢复系统代理——包括用户原有的代理设置，不会只做简单关闭。后台启动时会同时拉起看门狗：后端被强杀 / 崩溃，或关机注销时，看门狗自动按快照恢复，日志写入 `app.log` 的 `[watchdog]` 行）。启动失败时看 `backend/current/app.log` 并如实报告，不得重试超过一次。v260907 起 MCP 默认关闭且重启后复位，`service.sh start` 会自动调用 `/api/mcp/enable` 打开；MCP 报「MCP 服务未启用」时再跑一次 `service.sh start` 即可。
3. **平台状态**：`node <skill目录>/scripts/mcp.mjs call get_platform_status`，确认目标平台 available；不可用就报告原因并停止。视频号分享链接报缺元宝 Cookie 时，先自动运行 `scripts/yuanbao-cookie.mjs` 再复查（见「已知坑 C」）；`wxchannels:page` 不可用不影响分享链接。
4. **解析**：`node <skill目录>/scripts/mcp.mjs call fetch_content '{"url":"<链接>","timeout_seconds":300}' > <临时目录>/fetch.json`（链接含特殊字符时用单引号包裹 JSON，必要时转义）。结果存成文件，供下一步实测画质。
5. **实测画质并选最高**：`node <skill目录>/scripts/pick-variant.mjs <临时目录>/fetch.json`。脚本对每个画质的源地址只请求 1 个字节，从 Content-Range 读真实总大小，输出实际最大的 `variant_key`。**一律以实测大小为准，不信后端标注的分辨率和体积**（实测过「original 1080P 66MB」实际是 576P 的 6MB）。用户点名要某个画质时用用户的；输出 `variant_key: null`（文章、图文等无画质）时不传 `video_variant_key`。
6. **直接下载（无需确认）**：用户已授权解析成功后直接下载，不再停下来确认画质、体积或目录。`node <skill目录>/scripts/mcp.mjs call download_content '{"job_id":"<fetch 返回>","video_variant_key":"<第 5 步选出的>","download_dir":"<平台目录>","filename":"<短文件名>","wait_for_completion":true,"timeout_seconds":600}'`。
   - `download_dir` 按「下载目录」小节取平台目录。
   - `filename` 用 `<作者>_<标题前 20 字>`，去掉换行和 `/ : * ? " < > |`。视频号的「标题」常是几百字的整段描述，不传会生成超长文件名。后端会在后面接 `_<规格>` 和扩展名。
   - `existing_action` 保持默认 error，绝不静默覆盖同名文件。
   - 封面、MP3 只在用户明确要求时追加，不主动问。
   - 引擎下载失败一次，转 Plan B 直连下载（见「已知坑 A」），用的是第 5 步选出的那个画质的 url。
7. **校验与按需解密**：
   - 文件大小应等于第 5 步实测的字节数；`ffprobe -v error -show_entries stream=codec_name,width,height -show_entries format=duration -of compact "<文件>"` 报告实际分辨率与时长。
   - v260907 的引擎下载会自动解密。只有 `requires_decryption: true` **且** ffprobe 读不出视频流时，才用返回的 key 调 `decrypt_wxchannels_video '{"file_path":"...","key":"..."}'`（原地覆盖）。能正常读出的文件不要再解密，重复解密会把文件弄坏。
8. **收尾**：报告最终文件绝对路径、体积、实际分辨率；若用户要了封面/MP3，先按对应小节完成再收尾。询问用户是否 `service.sh stop`（会自动恢复系统代理）。用户没说就保持服务运行。

## 视频号的两种输入模式

- **分享链接（推荐）**：用户在微信里对视频「分享 → 复制链接」，把链接交给本 Skill，流程全自动。
- **页面抓取**：用户说「下载我刚在微信里看的视频」但没有链接时，指引其在微信中打开该视频号视频**播放几秒后暂停**（让代理捕获流量），然后 `fetch_content` 用最近捕获解析（`force_refresh: true`），其余流程相同。

## 分发与首次自举（开源分发用）

本 Skill 可直接分发，新机器首次运行时由 `service.sh ensure` 闭环补齐依赖（见主流程第 1 步）：

- **wx-video 命令**：从 skill 自带的 `assets/wx-video` 复制到 `~/.local/bin/`（服务管理与代理快照恢复的唯一来源；本机已有则不覆盖，注意与 assets 保持同步）；
- **后端二进制**：经 `wx-video update` 全新安装最新官方 Release（约 17MB，HTTPS 直连 GitHub，失败自动改走本机代理；代理地址由环境变量 `WX_VIDEO_PROXY` 指定，未设置时默认 `http://127.0.0.1:7890`）；
- **Node ≥ 18**（MCP 客户端依赖）：缺失时经 Homebrew 安装。

两项无法静默完成、需用户参与一次的：**SunnyNet 根证书**（首次启动后端弹系统授权，或 `sudo wx-video` 跑一次）；**元宝登录**（在 Chrome 里登录 yuanbao.tencent.com 一次，之后由 `yuanbao-cookie.mjs` 自动同步，见坑 C）。ffmpeg 仅 MP3 模式需要，按 MP3 小节按需安装。

环境限定：macOS（arm64/amd64）。代理快照/恢复只处理后端会改写的 HTTP/HTTPS 代理项。

## 铁律

- 用户给出链接即视为授权下载：解析成功后直接按实测最高画质调用 `download_content`，不再逐次确认（2026-10-27 用户明确要求）。
- `decrypt_wxchannels_video` 只在文件确实读不出视频流时调用（见主流程第 7 步）。
- 自举安装（下载后端二进制、复制 wx-video 到 ~/.local/bin、brew 装依赖）必须先把检测报告展示给用户并取得明确同意；仅检测（`ensure` 不带 `--install`）可随时执行。
- 只处理用户主动提供的链接；不批量抓取、不猜链接。
- 失败如实报告（MCP 客户端退出码 2 = 连接/协议错误，3 = 工具执行失败），不重试超过一次，不用占位文件伪装成功。
- 不修改后端 config.yaml；不展示 cookies.json 内容。
- 临时起的服务若为本 Skill 所启动，收尾时提醒用户停止或明确交接运行状态。

## 封面下载（可选）

`fetch_content` 结果自带封面直链：`content.cover_url`（微信 CDN 图片地址，无需解密）。用户要封面时直接 curl 下载，与视频同目录同名（扩展名 `.jpg`）：
`curl -s -A "MicroMessenger/8.0.49" -o "<平台目录>/<标题>.jpg" "<cover_url>"`
已验证返回标准 JPEG（约 540x720）。注意 cover_url 与视频直链同时效，需在同一轮解析结果里取。

## MP3 提取（可选）

用户要音频/MP3 时，对已下载的视频文件本地提取（引擎下载和 Plan B 直连下载的文件都适用）：

1. 依赖检查：`which ffmpeg`。缺失时经用户同意后 `brew install ffmpeg`（网络慢可先 `export HTTPS_PROXY=<本机代理地址>`）。
2. 提取命令（与视频同名、同目录、`.mp3` 后缀，VBR 高音质）：
   `ffmpeg -hide_banner -loglevel error -y -i "<视频.mp4>" -vn -c:a libmp3lame -q:a 2 "<视频.mp3>"`
3. 校验：`file` 显示 MPEG layer III 即成功，报告时长与体积（ffprobe -show_entries format=duration）。

原生工具的 MP3 能力在微信内置页面的 JS 里实现（lamejs），无 API 可调，故 Skill 用本地 ffmpeg 等价实现——效果相同且不依赖后端引擎。

## 已知坑与绕行（2026-08-27 实战验证）

### 坑 A：分享链接(sph)解析成功，但后端自带下载引擎失败（v260817 实测；v260907 引擎已能正常下载，失败时仍按下面的 Plan B）

**现象**：`download_content` 创建的任务 0 字节后失败（任务 status=6，日志显示 `endpoint preparation failed`），`force_refresh` + `existing_action:"overwrite"` 重试也一样。

**根因**：后端构建 endpoint 时用了被裁短的 URL 变体（约 392 字符，缺少部分参数），微信 CDN 对其一律返回 HTTP 400。而解析结果里同时存在的完整版直链（约 600+ 字符）是有效的。

**Plan B（直连下载，已验证 126MB 全量成功）**：
1. 从 `fetch_content` 结果取完整直链：`content_details[0].data.url`（**不是** `download_resources[].download_url`——那个就是坏的短版）。
2. 探测总大小并验证可用（应返回 HTTP 206 + `Content-Range: bytes 0-0/<总字节数>`）：
   `curl -s -A "MicroMessenger/8.0.49" -r 0-0 -o /dev/null -D - "<完整直链>"`
3. 全量下载到 `<平台目录>/<标题>.mp4`，必须带 MicroMessenger UA：
   `curl -s -A "MicroMessenger/8.0.49" -o "<输出文件>" -w "HTTP %{http_code} %{size_download}B\n" "<完整直链>"`
4. 校验：`%{size_download}` 与第 2 步的 Content-Range 总字节数**完全相等**；`file` 显示 ISO Media/MP4；再看文件尾部非全零。
5. 若 `requires_decryption: true` 且有 `decode_key`：用 curl 下载的文件路径 + key 调 `decrypt_wxchannels_video`（这正是该工具的官方设计——支持第三方下载器 + 事后解密）。key 为空且 `requires_decryption: false` 时无需解密。
6. 清理失败任务：`curl -s -X POST http://127.0.0.1:2022/api/v1/download_task/delete -H 'Content-Type: application/json' -d '{"task_ids":[<失败ID>],"delete_files":false}'`。

### 坑 B：分享链接有时效

`weixin.qq.com/sph/xxx` 短链约 1 小时内失效，过期后解析报「get feed info: 此内容暂时无法播放」。让用户**现复制现解析**；对旧链接不要反复重试。

### 坑 C：sph 解析器依赖元宝 Cookie

「视频号分享链接」解析走腾讯元宝服务，`get_platform_status` 报「缺少 yuanbao.tencent.com Cookie」时，**自动处理，不要让用户操作**（2026-10-28 实测）：

1. 运行 `node --no-warnings <skill目录>/scripts/yuanbao-cookie.mjs`。脚本从本机 Chrome 读取元宝登录 Cookie（`.tencent.com` 上的 `hy_token`/`hy_user` 等 `hy_*`，加上 `yuanbao.tencent.com` 上的 Cookie），以明文 CookieCloud 格式 POST 给 `/api/cookies/update`。后端会把它持久化到 `cookies.json`，重启后仍然有效。用户已于 2026-10-28 授权读取。
2. 再查一次平台状态，`wxchannels:sph` 应变为 available。
3. 脚本只有在 Chrome 里没有 `hy_token` 时才会失败，这说明元宝已经登出。这时才请用户在 Chrome 打开 https://yuanbao.tencent.com 扫码登录，登录后重跑脚本。

不要再用「刷新元宝页面让代理抓 Cookie」的老办法。本机 Chrome 装了 iGuge Helper 插件，它会接管 Chrome 的代理，元宝请求根本不经过后端；Clash Party 也会把系统代理改回 7890。

**分享链接解析不需要视频号页面连接**（2026-10-28 实测：`wxchannels:page` 处于 unavailable 时，sph 照样解析并下载成功），不要让用户去微信里打开视频号。只有「页面抓取」模式需要它。

旧版（v260817）在 Cookie 失效时不报错，只返回空的 exportId，最后表现为「此内容暂时无法播放」。

### 坑 D：微信内置下载按钮「换视频不更新」（上游 bug）

微信里悬浮下载按钮只认页面加载后捕获的第一个视频，切换视频不更新（macOS 微信 4.1.12 + v260817 实测）。因此**优先引导用户用分享链接**走本 Skill；页面抓取模式仅适用于「重开视频号页面后第一个打开的就是目标视频」的场景。

## 排障

- MCP 连不上 → `service.sh status` 看进程、`service.sh health` 看端点；再查 `backend/current/app.log`。
- 下载任务 0 字节失败 / `endpoint preparation failed` → 已知坑 A，直接转 Plan B 直连下载。
- 解析报「此内容暂时无法播放」→ 已知坑 B，链接过期，让用户重新复制分享链接。
- 平台状态报「缺少 yuanbao.tencent.com Cookie」→ 已知坑 C，自动运行 `scripts/yuanbao-cookie.mjs`。
- fetch 超时 → 页面抓取模式常见，指引用户重新播放/暂停后带 `force_refresh: true` 重试一次。
- 下载目录出现同名文件 → 换清晰度或让用户改名，不擅自 overwrite。
- `download_content` 报「已存在该下载内容」→ 同一视频已有下载任务（换画质也算同一个）。不要用 `existing_action:"overwrite"`，它会删掉旧任务的文件；改走 Plan B 直连下载第 5 步选出的画质，下完按第 7 步校验，读不出视频流再解密。
- 后端版本更新 → `wx-video update`（自动下载安装、保留 config、清理旧版、重启服务）。
