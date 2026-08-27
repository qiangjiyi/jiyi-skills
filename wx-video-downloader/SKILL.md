---
name: wx-video-downloader
description: 通过本机 wx-video 服务（wx_channels_download）的 MCP 接口下载微信视频号视频，也支持抖音、公众号、知乎内容链接。支持三种交付模式：视频下载（含清晰度选择与按需解密）、封面图下载、MP3 音频提取（本地 ffmpeg）。自动管理服务启停、解析链接、确认后下载。用户说「下载视频号视频」「下载这个视频」「抓下这条抖音」「下载公众号文章里的视频」「下载封面」「转成 MP3 / 提取音频」或给出 channels.weixin.qq.com / 抖音分享链接要求下载时使用。
---

# 视频号视频下载器

把用户给的内容链接经本机 wx-video 后端的 MCP 接口解析、确认后下载为本地文件。
后端 = `~/.local/share/qiaomu-wx-video/backend/current`（由 `~/.local/bin/wx-video` 安装维护），
MCP 端点 = `http://127.0.0.1:2022/mcp`（streamable HTTP，无鉴权，仅本机回环）。

本 Skill 只做编排与确认；解析/下载默认通过 `scripts/mcp.mjs` 直连 MCP 完成，不操控浏览器，不读管理页 UI。两个例外：Plan B 直连下载（curl，见已知坑 A）与 MP3 提取（本地 ffmpeg），均在对应小节内给了完整命令。

## 支持范围

以 `get_platform_status` 实时结果为准，通常包括：视频号（分享链接 + 页面抓取）、抖音、公众号、知乎。

## 主流程

以下命令中的 `scripts/` 均指本 Skill 安装目录下的 `scripts/` 子目录（与 SKILL.md 同级），请按实际安装路径解析后执行。

1. **前置检测与自举**：先跑 `bash <skill目录>/scripts/service.sh ensure`（只检测不改动的安全操作，可随时执行）。退出码 0 → 依赖齐备，继续；退出码 2 → 把报告中的安装动作（下载后端二进制约 17MB / 复制 wx-video 命令 / brew 装 Node）展示给用户，**取得明确同意后**执行 `service.sh ensure --install`。报告中的 `!` 项（证书未装）提示用户：首次启动后端会弹系统授权，或先在终端运行一次 `sudo wx-video`。
2. **服务自检**：`service.sh status`；未运行则 `service.sh start`（启动会把系统代理指向 127.0.0.1:2023，这是抓取机制的一部分，勿手动改动。停止时由 `wx-video` 按「启动前快照」原样恢复系统代理——包括用户原有的代理设置，不会只做简单关闭）。启动失败时看 `backend/current/app.log` 并如实报告，不得重试超过一次。
3. **平台状态**：`node <skill目录>/scripts/mcp.mjs call get_platform_status`，确认目标平台 available；不可用就报告原因并停止。
4. **解析**：`node <skill目录>/scripts/mcp.mjs call fetch_content '{"url":"<链接>","timeout_seconds":300}'`（链接含特殊字符时用单引号包裹 JSON，必要时转义）。向用户展示：标题、作者、时长、清晰度列表（video_variants，含规格与体积）、下载目录。
5. **确认门禁（必须）**：`download_content` 是写操作，且服务端协议要求调用前获得用户确认。必须把「标题 / 清晰度选项 / 预计体积 / 下载目录」展示给用户并得到明确同意后才可继续。用户只要了某个清晰度就选那个；未指定时列出选项让用户挑，不替用户默认。**用户未提及封面/MP3 时顺带问一句是否需要**（用户已明说则不重复问）；确认结果决定第 6 步之后是否追加「封面下载」「MP3 提取」小节。
6. **下载**：`node <skill目录>/scripts/mcp.mjs call download_content '{"job_id":"<fetch 返回>","video_variant_key":"<所选>","wait_for_completion":true,"timeout_seconds":600}'`。默认下载目录沿用后端配置（`~/Downloads`，文件名模板 `{{filename}}_{{spec}}`）；不传 `download_dir` 覆盖，除非用户明确指定。`existing_action` 保持默认 error，绝不静默覆盖同名文件。长任务放后台跑，完成后报告结果里的文件路径。**视频号分享链接（sph）来源的任务大概率因后端 bug 失败（见「已知坑 A」），失败一次即转 Plan B 直连下载，不要反复重试引擎。**
7. **按需解密**：仅当结果标记 `requires_decryption: true` 时，用返回的 key 调 `decrypt_wxchannels_video '{"file_path":"...","key":"..."}'`。解密**原地覆盖**原文件，执行前再口头告知用户一次。
8. **收尾**：报告最终文件绝对路径与体积；若用户要了封面/MP3，先按对应小节完成再收尾，把视频、封面、MP3 的路径一并报告。询问用户是否 `service.sh stop`（会自动恢复系统代理）。用户没说就保持服务运行。

## 视频号的两种输入模式

- **分享链接（推荐）**：用户在微信里对视频「分享 → 复制链接」，把链接交给本 Skill，流程全自动。
- **页面抓取**：用户说「下载我刚在微信里看的视频」但没有链接时，指引其在微信中打开该视频号视频**播放几秒后暂停**（让代理捕获流量），然后 `fetch_content` 用最近捕获解析（`force_refresh: true`），其余流程相同。

## 分发与首次自举（开源分发用）

本 Skill 可直接分发，新机器首次运行时由 `service.sh ensure` 闭环补齐依赖（见主流程第 1 步）：

- **wx-video 命令**：从 skill 自带的 `assets/wx-video` 复制到 `~/.local/bin/`（服务管理与代理快照恢复的唯一来源；本机已有则不覆盖，注意与 assets 保持同步）；
- **后端二进制**：经 `wx-video update` 全新安装最新官方 Release（约 17MB，HTTPS 直连 GitHub，失败自动改走本机代理；代理地址由环境变量 `WX_VIDEO_PROXY` 指定，未设置时默认 `http://127.0.0.1:7890`）；
- **Node ≥ 18**（MCP 客户端依赖）：缺失时经 Homebrew 安装。

两项无法静默完成、需用户参与一次的：**SunnyNet 根证书**（首次启动后端弹系统授权，或 `sudo wx-video` 跑一次）；**元宝登录**（Chrome 登录 yuanbao.tencent.com，见坑 C）。ffmpeg 仅 MP3 模式需要，按 MP3 小节按需安装。

环境限定：macOS（arm64/amd64）。代理快照/恢复只处理后端会改写的 HTTP/HTTPS 代理项。

## 铁律

- 未经用户确认绝不调用 `download_content` / `decrypt_wxchannels_video`。
- 自举安装（下载后端二进制、复制 wx-video 到 ~/.local/bin、brew 装依赖）必须先把检测报告展示给用户并取得明确同意；仅检测（`ensure` 不带 `--install`）可随时执行。
- 只处理用户主动提供的链接；不批量抓取、不猜链接。
- 失败如实报告（MCP 客户端退出码 2 = 连接/协议错误，3 = 工具执行失败），不重试超过一次，不用占位文件伪装成功。
- 不修改后端 config.yaml；不展示 cookies.json 内容。
- 临时起的服务若为本 Skill 所启动，收尾时提醒用户停止或明确交接运行状态。

## 封面下载（可选）

`fetch_content` 结果自带封面直链：`content.cover_url`（微信 CDN 图片地址，无需解密）。用户要封面时直接 curl 下载，与视频同目录同名（扩展名 `.jpg`）：
`curl -s -A "MicroMessenger/8.0.49" -o ~/Downloads/<标题>.jpg "<cover_url>"`
已验证返回标准 JPEG（约 540x720）。注意 cover_url 与视频直链同时效，需在同一轮解析结果里取。

## MP3 提取（可选）

用户要音频/MP3 时，对已下载的视频文件本地提取（引擎下载和 Plan B 直连下载的文件都适用）：

1. 依赖检查：`which ffmpeg`。缺失时经用户同意后 `brew install ffmpeg`（网络慢可先 `export HTTPS_PROXY=<本机代理地址>`）。
2. 提取命令（与视频同名、同目录、`.mp3` 后缀，VBR 高音质）：
   `ffmpeg -hide_banner -loglevel error -y -i "<视频.mp4>" -vn -c:a libmp3lame -q:a 2 "<视频.mp3>"`
3. 校验：`file` 显示 MPEG layer III 即成功，报告时长与体积（ffprobe -show_entries format=duration）。

原生工具的 MP3 能力在微信内置页面的 JS 里实现（lamejs），无 API 可调，故 Skill 用本地 ffmpeg 等价实现——效果相同且不依赖后端引擎。

## 已知坑与绕行（2026-08-27 实战验证）

### 坑 A：分享链接(sph)解析成功，但后端自带下载引擎必失败

**现象**：`download_content` 创建的任务 0 字节后失败（任务 status=6，日志显示 `endpoint preparation failed`），`force_refresh` + `existing_action:"overwrite"` 重试也一样。

**根因**：后端构建 endpoint 时用了被裁短的 URL 变体（约 392 字符，缺少部分参数），微信 CDN 对其一律返回 HTTP 400。而解析结果里同时存在的完整版直链（约 600+ 字符）是有效的。

**Plan B（直连下载，已验证 126MB 全量成功）**：
1. 从 `fetch_content` 结果取完整直链：`content_details[0].data.url`（**不是** `download_resources[].download_url`——那个就是坏的短版）。
2. 探测总大小并验证可用（应返回 HTTP 206 + `Content-Range: bytes 0-0/<总字节数>`）：
   `curl -s -A "MicroMessenger/8.0.49" -r 0-0 -o /dev/null -D - "<完整直链>"`
3. 全量下载到 `~/Downloads/<标题>.mp4`，必须带 MicroMessenger UA：
   `curl -s -A "MicroMessenger/8.0.49" -o "<输出文件>" -w "HTTP %{http_code} %{size_download}B\n" "<完整直链>"`
4. 校验：`%{size_download}` 与第 2 步的 Content-Range 总字节数**完全相等**；`file` 显示 ISO Media/MP4；再看文件尾部非全零。
5. 若 `requires_decryption: true` 且有 `decode_key`：用 curl 下载的文件路径 + key 调 `decrypt_wxchannels_video`（这正是该工具的官方设计——支持第三方下载器 + 事后解密）。key 为空且 `requires_decryption: false` 时无需解密。
6. 清理失败任务：`curl -s -X POST http://127.0.0.1:2022/api/v1/download_task/delete -H 'Content-Type: application/json' -d '{"task_ids":[<失败ID>],"delete_files":false}'`。

### 坑 B：分享链接有时效

`weixin.qq.com/sph/xxx` 短链约 1 小时内失效，过期后解析报「get feed info: 此内容暂时无法播放」。让用户**现复制现解析**；对旧链接不要反复重试。

### 坑 C：sph 解析器依赖元宝 Cookie

「视频号分享链接」解析走腾讯元宝服务，`get_platform_status` 报「缺少 yuanbao.tencent.com Cookie」时：指引用 **Chrome** 登录 https://yuanbao.tencent.com（微信扫码），后端可直接读 Chrome Cookie，无需其他配置。该凭据是内存态，后端重启后失效就重新登录一次。

### 坑 D：微信内置下载按钮「换视频不更新」（上游 bug）

微信里悬浮下载按钮只认页面加载后捕获的第一个视频，切换视频不更新（macOS 微信 4.1.12 + v260817 实测）。因此**优先引导用户用分享链接**走本 Skill；页面抓取模式仅适用于「重开视频号页面后第一个打开的就是目标视频」的场景。

## 排障

- MCP 连不上 → `service.sh status` 看进程、`service.sh health` 看端点；再查 `backend/current/app.log`。
- 下载任务 0 字节失败 / `endpoint preparation failed` → 已知坑 A，直接转 Plan B 直连下载。
- 解析报「此内容暂时无法播放」→ 已知坑 B，链接过期，让用户重新复制分享链接。
- 平台状态报「缺少 yuanbao.tencent.com Cookie」→ 已知坑 C，Chrome 登录元宝。
- fetch 超时 → 页面抓取模式常见，指引用户重新播放/暂停后带 `force_refresh: true` 重试一次。
- 下载目录出现同名文件 → 换清晰度或让用户改名，不擅自 overwrite。
- 后端版本更新 → `wx-video update`（自动下载安装、保留 config、清理旧版、重启服务）。
