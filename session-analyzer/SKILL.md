---
name: session-analyzer
description: >
  AI Agent 会话分析助手。只读扫描本机 Codex、Antigravity、Claude Code、ZCode 四个
  Agent 累积的会话/对话/任务数据，按「Agent → 项目 → 会话」三级层级统计各层占用空间
  与会话数量，标记工作目录已删除的孤儿会话，生成排版精美的交互式 HTML 报告，并可起
  本地服务在网页上按会话/按项目一键删除（默认移废纸篓、可逆）。扫描全程只读。
  务必在以下场景使用：用户说"会话太多""对话记录太多""Codex/Antigravity/Claude/
  ZCode 会话占空间""AI Agent 历史""项目删了会话还在""孤儿会话""session cleanup"
  "Codex 侧栏还有旧标题""no rollout found""幽灵条目""侧栏残留"、
  想看哪个 Agent 的会话最占地方，或想批量盘点某个项目的会话情况时。
  注意：本 skill 针对四个 AI Agent 的会话数据，不是整机磁盘分析（那是
  storage-analyzer，二者互补）。
---

# Session Analyzer

对本机四个 AI Agent（Codex / Antigravity / Claude Code / ZCode）的会话数据做一次只读分析，
依据扫描快照同步清理 `~/.claude.json` 中没有真实 session 的项目配置，再生成交互式 HTML 报告并可在网页上一键删除。**确定性管线：只读扫描 → Claude 配置清理 → 固定决策 → 固定执行**
（关 app / 兜底清理 / 报告形态都按写死的默认值跑，不再用 AskUserQuestion 询问；仅当用户主动要求偏离时才调整对应项）。

与 storage-analyzer 同构：**一个模板 + 两个入口**。模板 `report_template.html` 看注入的
`__DELETE_CONFIG__` 决定渲染成只读还是带删除按钮——`build_report.py` 注入 `null`（静态、
`file://` 双击、无删除），`server.py` 注入真实 `{token, endpoint, enabled}`（起本地服务、
有删除按钮）。

## 铁律

- **扫描只读，配置清理独立。** `scan.py` 只做 `os.scandir`/`stat`/只读 SQLite SELECT/读 jsonl，
  绝不修改任何 Agent 数据源；`--json-out` 仅写调用方指定的扫描产物。扫描之后由独立的
  `cleanup_claude_config.py` 只过滤 `~/.claude.json` 顶层 `projects`，不属于
  `precleanup.py` 或 `agent_delete.prune_roots()`。会话删除仍只发生在 `server.py`（经
  `agent_delete.py`），且只删扫描里出现过的会话/项目。
- **Claude 配置清理 fail closed。** 只以真实 Claude session 的 `extra.cwd` 并集判活；
  `extra.claude_kind == "orphan_dir"` 不算 session，目录仍存在也不保留配置。扫描快照以 `0600`
  原子写入，清理前和提交前都复核实时 `(project_id, session_id, cwd)` 分布与快照完全一致。配置清理
  仅保留最近一次修改前的固定整文件备份，权限 `0600`；检测到 session 分布变化、快照/配置结构
  异常、并发变化、备份或原子替换失败时立即停止。静态报告和本地服务还会校验同时绑定扫描摘要、
  当前 HOME 和清理后配置摘要的 `0600` 成功标记；清理未成功、配置又发生变化或快照/标记来自其他
  HOME 时都无法启动。日志和摘要只报状态/数量，不输出项目键、OAuth、account、MCP 等配置内容。
- **清理范围收死。** 空目录/孤儿清理只在各 Agent 自己的数据子树内自底向上进行
  （`agent_delete.prune_roots()` 列出的根），绝不删根本身、绝不越界——空目录有时是程序占位。
  判空时忽略 `.DS_Store`/`Thumbs.db`，否则只剩系统垃圾文件的目录会"扫了还删不干净"。
- **删除安全模型（server.py）。** 绑 127.0.0.1 + 随机端口 + 随机 token；每个 POST 校验
  token + Host（挡 DNS-rebinding）；只接受扫描里存在的 `(agent, scope, project_id, session_id)`，
  客户端无法指定任意路径/id；每个处理器只碰该 Agent 自己的数据目录。
- **默认移废纸篓（可逆）。** 文件级删除走废纸篓；但 **Codex 与 ZCode 的 DB 行 / jsonl
  索引行天生硬删**（数据库行没法进废纸篓），Antigravity 侧栏索引 `agyhub_summaries_proto.pb`
  同样就地硬改——这些不可逆，UI 已用红色警示并二次确认。
- **Codex 侧栏状态文件（`.codex-global-state.json`）剪除纪律。** 它被运行中的 ChatGPT
  （内嵌 Codex）随时重写，因此**改它要求 App 已完全退出**：执行前 `pgrep` 校验 ChatGPT /
  遗留 Codex App 主进程，检测不到进程列表时保守拒绝；`codex` CLI 引擎进程不写此 Electron
  文件，不参与门禁。剪除只碰确证的 thread-id 命名空间（`projectless-thread-ids` /
  `pinned-thread-ids` / `thread-titles.titles` / `thread-titles.order` /
  `thread-workspace-root-hints`），**严禁**「凡不在 threads 表的 uuid 一律删」式宽匹配——
  文件里有约 197 个非 thread 的 uuid（`thread-project-assignments`、`thread-descriptions-v1`
  等几十个 key），误伤会损坏应用状态。改前整文件备份到固定单份
  `~/.codex/.codex-global-state.json.session-analyzer.bak`（`0600`，每次覆盖）；同目录临时
  文件 + `os.replace` 原子替换；写前重读，mtime/内容有变化即中止；文件非 Electron 规范
  紧凑格式（round-trip 不恒等）一律拒绝，保证「只删该删的条目、其余逐字节保留」。幂等：
  无命中即 no-op，不写文件、不产生备份；key 缺失/结构变体逐 key 跳过。
- **路径、命令、thread id 原文展示，不翻译。** 不读取、不展示任何密钥/凭据内容。

## 执行流程

**确定性管线，四段式：只读扫描 → Claude 配置清理 → 固定决策 → 固定执行。** 全程无
act-vs-ask 分支：开场永远先只读扫描，随后必须清理 `~/.claude.json` 的陈旧项目配置；两步均
不问、不可选。配置清理成功且扫到会话/孤儿时，按**写死的默认决策**直接往下跑，**不再用
AskUserQuestion 询问**。默认决策恒为：① 关闭 ChatGPT（含 Codex）/ Antigravity，② 开场兜底
清理，③ 生成可删除交互报告——三项全做。**禁止**把关 app、兜底清理、报告形态当成可选项
临场询问或边走边拍——那正是流程每次跑都漂移的根源。**唯一的偏离来源**：用户在对话里主动
提出（如「别关 app」「只要只读静态报告」「都不要，只看摘要」）时，才按用户所说调整对应项；
用户没说就一律走默认、不主动反问。Claude 配置清理属于固定的扫描后维护步骤，不因报告形态改变
而跳过。

### Step 1 始终先做：只读扫描（不问、不可选）

```bash
bash scripts/run.sh scan.py --json-out /tmp/session_scan.json
```

> **推荐用 `--json-out` 写文件**（而不是 `>` 重定向）：快照通过同目录临时文件以 `0600`
> 权限原子写入，既避免 stdout/stderr 混叠导致 JSON 解析失败，也避免 `/tmp` 中的 cwd、摘要等
> 会话信息被其他本地用户读取。重定向写法仍可用，但调用方必须自行保证输出分离和文件权限。

> **为什么用 `run.sh` 而不是 `python3 scripts/scan.py`：** agent 的 cwd 是当前项目目录
> （如 `/Users/jiyi/Projects/active/<proj>`），不是 skill 根。直接用相对路径会
> `can't open file '.../scripts/scan.py'`。`scripts/run.sh` 自定位 skill 根再 exec，
> 从任何 cwd 都能跑。下面 ① / ② / ③ 同理。

`scan.py` 自动探测四个 Agent 是否安装（数据目录是否存在），未装则跳过并标注。产出统一
JSON：每个 Agent → 项目 → 会话三级，含每层 size、会话数、mtime、摘要、孤儿标记。

`scan.py` 在 stderr 还会打一行自检 `[scan] agents=N sessions=... orphans=... size=...
in X.Xs`，方便 agent 区分「真扫到 0」vs「扫坏了」。脚本末行稳定输出 `✓ DONE`。
扫描结束后无论是否为空态，都必须先执行 Step 1.5；**空态快通道**只能发生在配置清理成功之后。
若 `sessions == 0 && orphans == 0`，stderr 多打一行
`✓ 本机 AI 会话状态干净（0 会话 / 0 孤儿），无需清理。`——完成 Step 1.5 后直接跳到 Step S，
不问决策、不关闭 app、不跑 `precleanup.py`、不生成报告。

各 Agent 的会话/项目定义：
- **Codex**（`~/.codex/`）：会话 = `state_5.sqlite` 的 `threads` 行（含 `unknown` 等所有 source）；项目按 `cwd` 聚合。
  扫描同时只读解析 Electron 侧栏状态文件 `~/.codex/.codex-global-state.json`（侧栏列表不读
  数据库，只删 DB 行会留下点开报「no rollout found」的**侧栏幽灵条目**）：存活会话若仍被该
  文件引用，标记 `extra.codex_ui_residue`；「UI 命名空间里有 id、threads 表已无」的纯幽灵
  单列为 agent 层 `ghost_ui_entry_count`（明细在 `ghost_ui_entries`）。
- **Antigravity**（`~/.gemini/antigravity/`）：新版会话 = 侧栏索引 `agyhub_summaries_proto.pb` 里的每条记录（id/标题/时间/workspace 均解析自该 proto），按 workspace 路径归类成项目；兼容旧版未迁移时残留的 `conversations/<uuid>.pb`；无对应对话的 brain 目录单列「孤儿残留」。
- **Claude Code**（`~/.claude/`）：会话 = `projects/<编码路径>/<uuid>.jsonl`；项目 = 该编码目录。
  真实路径按「session JSONL 顶层 `cwd` → `history.jsonl` 的 project → 编码目录启发式解码」取值；
  JSONL cwd 即使已不存在仍是权威来源，不继续回退。每个真实 session 都把该路径写入
  `extra.cwd`；同一编码目录可包含多个不同 cwd，配置清理取所有 session cwd 的并集，不能只看
  项目级 `real_path`。编码 `project.id` 始终作为删除身份，`label`/`real_path` 只用于展示和孤儿
  判断。匹配 `/_skill-runtime/releases/<digest>/runner` 的已清理临时目录会标记为「临时 Skill
  运行目录已清理」；0-jsonl 的空目录单列为 `extra.claude_kind = "orphan_dir"`，不算真实 session。
- **ZCode**（`~/.zcode/`）：会话 = `cli/db/db.sqlite` 的 `session` 行（`task_type` 区分
  interactive / subagent_child 子代理，子代理随父会话级联删除）；项目按 `session.path`
  （真实工作目录）聚合。对话正文存共享库的 `message`/`part`/`session_entry`（字节计入会话
  size，删行不缩小 db.sqlite 文件）；任务状态来自 `v2/tasks-index.sqlite` 的 `tasks` 行
  （task_id 与会话 id 一一对应，删除时同步清行）。逐会话卫星 = `cli/{artifacts,exec,
  image-cache,agents}/sess_<id>/` 与 `cli/rollout/model-io-sess_<id>.jsonl`；无对应 DB 会话的
  卫星/索引行单列「孤儿残留」。不归属单会话的数据（`cli/log/`、`exec/shell-snapshots`、
  `v2/checkpoints|logs|crash` 等）不扫。最近 10 分钟内仍有更新的会话标记 `extra.zcode_live`
  且删除被拒绝（防止删到正在运行的会话——删除 zcode 会话无需也无法先关 zcode 进程）。

**Multica 增强**：当 Claude Code 的项目目录匹配 Multica workspace 模式（`*multica-workspaces-<ws_id>-<task_prefix>-workdir`）时，scan.py 自动：
1. 查找对应的 `~/multica_workspaces/<ws_id>/<task_id>/` 目录，读取 `.gc_meta.json` 判断任务完成状态
2. 查找对应的 `~/Library/Caches/claude-cli-nodejs/<project_id>/` CLI 缓存
3. 将完成状态写入 session 的 `extra.multica` 字段（`status: "completed"|"cleanable"` + `task_kind` + 路径）
4. 将 workspace 任务目录和 CLI 缓存的体积追加到项目 size 中
5. 重构正确的显示路径和 orphan 判断（覆盖 `_decode_claude()` 的错误路径解码）
6. 统计 `multica_cleanable_count` 到 agent 层

两种状态（优先从 Multica API 获取 issue 状态，API 不可用时回退到本地 gc_meta）：
- **cleanable**：issue 已终态（done/cancelled），任务不再需要，可安全一键清理（不显示 badge）
- **active**：issue 未终态（backlog/todo/in_progress/in_review/blocked 等），任务可能仍在使用，不纳入一键清理

判断逻辑：以 **issue 状态**而非 task 状态为准——即使 task 已 completed，只要 issue 还没 done，对应的 Claude 会话就不应被清理。

API 调用方式：
1. 读取 `~/.multica/config.json` 获取 token/workspace_id/server_url
2. `GET /api/issues` 获取所有 issue 及其状态
3. `GET /api/agents` 获取所有 agent
4. 逐 agent `GET /api/agents/{id}/tasks` 获取完整任务列表（含 issue_id）
5. 用 task_id 前 8 位匹配 Claude 项目目录，关联到 issue 状态

报告中：
- 可清理的 Multica 会话无特殊 badge，默认纳入一键清理
- 不建议清理的 Multica 会话标记 ⚠️ 橙色 badge「Multica 不建议清理」，单独删除需确认，不纳入一键清理
- Claude 分区新增「🧹 一键清理 N 个 Multica 可清理会话」按钮（仅 cleanable）
- 摘要区新增 Multica 可清理/不建议清理计数
- 删除 Multica 会话时，自动同时清理 workspace 任务目录和 CLI 缓存

### Step 1.5 固定执行：清理 Claude 全局项目配置（不问、不可选）

```bash
bash scripts/run.sh cleanup_claude_config.py /tmp/session_scan.json
```

`cleanup_claude_config.py` 先校验扫描快照属于当前 HOME、是当前用户拥有的普通 `0600` 文件，再根据
其中 `extra.claude_kind == "session"` 的 `(project.id, session.id, extra.cwd)` 集合过滤
`~/.claude.json` 顶层 `projects`：没有任何真实 session 的项目配置全部删除，**即使对应工作目录仍
存在也删除**；其他顶层键和保留项目值完整保留。清理前和提交前会重新读取 `~/.claude/projects`
及 `history.jsonl`，实时 session 身份/cwd 与快照有任何增删或变化就以 `session_state_changed` 停止，
要求重新运行 Step 1。实际删除前将原文件完整备份到固定的
`~/.claude.json.session-analyzer.bak`（`0600`），每次覆盖最近一次备份，不无限增长；发生冲突时不会
覆盖既有备份。

Claude Code 没有公开可复用的 `~/.claude.json` 锁协议，因此脚本采用实时 session 复核、配置写前
重读、同目录临时文件和 `os.replace` 原子替换；这能拒绝可检测的并发更新并避免半写，但无法彻底
消除最后一次检查与 replace 之间的极短无锁竞态。检测到 `session_state_changed`、快照/配置异常、
并发变化、备份或替换失败时退出非零，**必须立即停止整个管线**：保留 `/tmp/session_scan.json`，
不关闭 app、不跑 `precleanup.py`、不生成静态或交互报告；报告入口本身也会拒绝缺少成功标记或标记
与快照摘要不匹配的请求。只向用户报告脚本输出的通用失败类别并建议从 Step 1 重试，不显示异常
原文或配置内容。配置不存在、没有待删项均是成功 no-op，仍会生成绑定该快照的 `0600` 成功标记。

成功后转述 `removed=N` 和 `backup=created`（若有），但绝不转述项目键或配置值。然后才判断空态：
空态直达 Step S；非空态进入 Step 2。

### Step 2 固定决策（扫到东西直接按默认执行，不询问）

先给一句结论先行的摘要（合计占用 / 占用最大的 Agent / 孤儿数），然后**不再用 AskUserQuestion
询问**，直接按以下写死的默认决策进入 Step 3：

1. **关闭 ChatGPT / Antigravity：关。** 避免后续删除撞到 app 占用文件；只读扫描已跑完，关不关
   都不影响结果。**说明**：2026-07-10 起 OpenAI 把独立 Codex APP 合并进了 ChatGPT APP，会话
   数据（`~/.codex/`）现由 ChatGPT 进程持有，所以删除前要关的是 ChatGPT。脚本里同时保留
   老的 "Codex" 进程名作为 legacy 兜底。
2. **开场兜底清理：清。** 跑 `precleanup.py` 清空目录 / 卫星孤儿，默认移废纸篓、可逆。
3. **报告形态：可删除交互报告**（`server.py`，本 skill 核心价值）。

> 这三项默认已固化，**无需也不要发 AskUserQuestion**。只读静态报告（`build_report.py`）/
> 「都不要，只看摘要」仍是合法形态，但**仅当用户在对话里主动要求偏离时**才切换；用户没主动说，
> 就一律走上面三项默认，直接进 Step 3。

### Step 3 按默认固定执行（顺序写死，无分支）

顺序恒为：**① 关 app → ② 兜底清理 → ③ 生成可删除交互报告 → ④ Step S 摘要。** 默认三项全做、
逐步执行；仅当用户主动要求偏离（别关 app / 换只读报告 / 不要报告）时，才跳过或替换对应那一步。

**① 关闭 ChatGPT / Antigravity**（默认执行；用户主动说「别关」时才跳过）

```bash
bash scripts/run.sh close_agents.py
```

`close_agents.py` 先 `osascript quit` 优雅退出、等不到再 `pkill` 强制结束，**只关这两类
app，不动其它进程**。脚本会逐步打印检测与关闭过程（如「⚠ 检测到 ChatGPT 正在运行，
即将自动关闭 ChatGPT…」「✓ 已关闭 ChatGPT」），**agent 要把这些信息如实转述给用户**。

> **为什么关的是 ChatGPT 而不是 Codex**：OpenAI 已于 2026-07-10 把独立 Codex APP 合并进
> ChatGPT APP，会话数据（`~/.codex/`）现由 ChatGPT 进程持有；老版独立 Codex APP 用户
> 脚本也兼容（同时检测 `Codex` 进程名）。
>
> **ZCode 不需要（也不能）关**：正在运行的 zcode CLI 进程往往就是执行本 skill 的 agent
> 自身，强杀等于自杀。ZCode 的删除安全由「最近 10 分钟仍有活动的会话拒绝删除」兜底
> （`agent_delete.delete_zcode_sessions` 删前实时复核 `session.time_updated`）。

**② 开场兜底清理**（默认执行；用户主动说「别清」时才跳过）

```bash
bash scripts/run.sh precleanup.py        # 默认移废纸篓（可逆）；--hard 直接删
```

清掉四个 Agent 历史遗留的**空目录**（含只剩 `.DS_Store` 的），Claude
`session-env`/`file-history`/`tasks` 里**对应会话已不存在的卫星孤儿**，以及 Claude
`sessions/<pid>.json` 里**进程已不存在（或 pid 被非 Claude 进程复用）的陈旧运行时状态文件**
——某个 Claude CLI 异常退出没清掉自己的状态文件时留下的残渣。这些都是删会话/退出时没收
干净的残渣——逐会话删不收空了的 `projects/<dir>`、Codex 删 rollout 留下的空日期目录、旧
工具遗留的空壳/孤儿——`scan.py`（只读）看不到也不展示。脚本在 stderr 列出清理项、stdout
末行输出 `✓ DONE`，**agent 把清理条数转述给用户**。

> `sessions/<pid>.json` 的判定靠 `ps -p <pid>` 双重校验：进程不存在、或进程名不含 `claude`
> （pid 被复用）才算孤儿；活着的 claude 进程一律保留（含当前正在跑的会话自身），非类 Unix
> 环境查不到进程名时保守保留、绝不误删。

> 新产生的残渣已由删除链路就地收掉（删完会话即清空了的父目录 / 空日期目录），所以这一步
> 主要是补历史欠账。

**③ 生成报告**（默认走可删除交互报告；用户主动要求「只读静态」/「都不要」时才换形态或跳过）

可删除交互报告（默认）。`server.py` 是常驻服务（`serve_forever`），**必须彻底脱离 agent 的
输出管道后台运行**，否则会被 harness 回收（报 `exit 144`，但进程其实没死，反而堆叠出互相
flock 死锁的僵尸实例）。固定用下面这一条起（macOS 无 `setsid`，用 `nohup` + `disown`；
端口监听可能被沙箱拦，需关沙箱跑）：

```bash
nohup bash scripts/run.sh server.py /tmp/session_scan.json </dev/null >/tmp/session-analyzer-server.log 2>&1 &
disown
```

起好后**另起一条命令**读 `/tmp/session-analyzer-server.log` 拿 URL（形如
`http://127.0.0.1:<port>/`）转告用户；服务的 pid:port 也写在 `/tmp/session-analyzer-server.lock`。
脚本自带单实例约束，重复起会自动接管旧实例。用完让用户 `kill <pid>` 停（停掉后删除按钮即失效）。

`server.py` 起在 127.0.0.1 + 随机端口 + 随机 token，把同一套模板注入启用态 `__DELETE_CONFIG__`。
三栏对比三个 Agent，可展开「项目 → 会话」树；每条会话有「删除」、每个项目有「删除整个项目」、
每个 Agent 有「🧹 一键清理 N 个孤儿会话」。删除默认移废纸篓（可逆），Codex/索引类硬删项会红色
二次确认。删除经 `agent_delete.py`，复刻各 Agent 原始清理工具的 removal set（Codex 行+jsonl+
卫星文件+侧栏状态剪除、Claude jsonl+session 目录、Antigravity 卫星文件 + 侧栏索引 proto 改写）。

**Codex 侧栏幽灵条目（👻 区块）**：Codex 分区顶部单列「侧栏幽灵条目（N）」，展开可看每条的
id/标题/出现位置，配「🧹 一键清理」按钮。清理走同一 `/action` 通道（`scope: "ghost_ui"`），
**客户端不传任何 id**——ghost 集合由 `agent_delete.cleanup_codex_ui_ghosts()` 在执行时从
threads 表 + 状态文件**实时重算**（绝不信任扫描快照，防止误删扫描后才新开的会话），规则=
uuid 形态 ∧ 在白名单命名空间 ∧ 不在 threads 表。App 未退出、数据库被锁、文件非规范格式、
并发改动任一命中都会整单拒绝且状态文件保持原样。会话行的 👻「侧栏残留」badge 表示该存活
会话仍被状态文件引用（删除链路会在同一次操作里一并剪除）。

只读静态报告（仅分享/留存，无删除按钮，`file://` 打开碰不到本地服务）：

```bash
bash scripts/run.sh build_report.py /tmp/session_scan.json ~/Desktop/session-report.html && open ~/Desktop/session-report.html
```

`build_report.py` 注入 `__DELETE_CONFIG__ = null`，渲染成纯只读报告。默认输出
`~/Desktop/session-report.html`，第二个参数可指定任意路径。

**排障：网页上没有删除按钮** = 开的是静态报告（改用 `server.py`），或服务已被 Ctrl+C 停掉。

### Step S 对话里给摘要

报告生成后（或配置清理成功后由空态快通道直达此处），在对话里给结论先行的一段话：全部
Agent 合计占用、占用最大的 Agent、孤儿会话总数、最该先关注的项，以及 Claude 配置清理删除条目数和
是否创建备份。细节让用户看 HTML；摘要不得展示任何 `~/.claude.json` 项目键或值。

**扫描 JSON 结构**（读它做摘要时照此取值，别猜——`agents` 是 **list** 不是 dict，对它用
`.items()` 会报错）：

```jsonc
{
  "generated_at": 0, "scan_seconds": 0.0, "home": "/Users/<user>",
  "agents": [                      // ← list，直接遍历
    {
      "key": "codex",             // codex | antigravity | claude | zcode
      "name": "Codex",
      "installed": true,           // false 时 projects 为空，跳过
      "note": "...",
      "project_count": 0, "session_count": 0,
      "total_size": 0,             // 字节
      "orphan_session_count": 0,
      "ghost_ui_entry_count": 0,     // 仅 codex：侧栏状态文件里的幽灵条目数（UI 有 id、threads 表已无）
      "ghost_ui_entries": [          // 仅 codex：幽灵明细（id/title/出现的白名单 key）
        { "id": "...", "title": "...", "keys": ["projectless-thread-ids"] }
      ],
      "multica_cleanable_count": 0,  // 仅 claude：可安全清理
      "multica_active_count": 0,     // 仅 claude：进行中，不纳入一键清理
      "projects": [                // ← list
        {
          "id": "...", "label": "...", "real_path": "...",
          "orphan": false,
          "orphan_reason": null,       // missing_workdir | temporary_skill_runtime | null
          "session_count": 0, "size": 0,
          "multica_status": "cleanable",    // 仅 Multica 项目：cleanable | active
          "multica_workspace_path": "...", // 仅 Multica 项目
          "multica_cache_path": "...",     // 仅 Multica 项目
          "multica_extra_size": 0,         // 仅 Multica 项目
          "sessions": [            // ← list
            { "id": "...", "title": "...", "snippet": "...",
              "mtime": 0, "size": 0, "extra": {
                "claude_kind": "session", // 真实 Claude session；0-jsonl 合成项为 orphan_dir
                "cwd": "/path/from-jsonl-or-history-or-fallback", // 仅真实 Claude session
                "codex_ui_residue": true, // 仅 codex：该存活会话仍被侧栏状态文件引用
                // 仅 ZCode 会话另有：task_type（interactive|subagent_child）、parent_id、
                // archived、task_status/model（来自 v2 任务索引）、zcode_live（10 分钟内活跃）
                "multica": {            // 仅 Multica 会话
                  "workspace_id": "...", "task_id": "...",
                  "task_prefix": "...", "status": "cleanable",  // cleanable | active
                  "task_kind": "issue|autopilot_run|chat"
                }
              }
            }
          ]
        }
      ]
    }
  ]
}
```

各 Agent 的 `total_size / session_count / project_count / orphan_session_count` 已预聚合，
摘要直接取用，不必自己累加 sessions。`multica_cleanable_count / multica_active_count`
仅 Claude Code agent 有值，摘要中可单独呈现。

## 与 storage-analyzer 的区别

两者同构（一模板两入口、扫描只读、网页可一键删），互补之处在范围：

- `storage-analyzer`：扫整机磁盘，按 `🟢自动清理 / 🟡需判断 / 🔴谨慎清理` 给出可执行处置。
- `session-analyzer`（本 skill）：只扫三个 AI Agent 的会话数据，按「Agent → 项目 → 会话」
  盘点占用，网页上按会话/项目删除，复刻各 Agent 原始清理工具的 removal set。

## 文件结构

```text
session-analyzer/
├── SKILL.md
├── scripts/
│   ├── scan.py                    # 只读扫描四 Agent → JSON（含 Codex 侧栏残留/幽灵标记）
│   ├── codex_ui_state.py          # Codex 侧栏状态文件唯一读写入口：解析/白名单剪除/门禁写
│   ├── cleanup_claude_config.py   # 扫描后过滤 ~/.claude.json 顶层 projects
│   ├── close_agents.py            # 关闭 ChatGPT（含 Codex）/ Antigravity（默认执行）
│   ├── precleanup.py              # 开场兜底：清空目录 + Claude 卫星孤儿 + 陈旧进程状态文件（默认废纸篓）
│   ├── agyhub_summaries.py        # Antigravity 索引 .pb 解析 + 按 id 剔除（scan/删除共用）
│   ├── build_report.py            # 注入 DELETE_CONFIG=null → 静态只读报告（入口一）
│   ├── server.py                  # 本地服务，注入启用态配置 → 带删除的交互报告（入口二）
│   └── agent_delete.py            # 各 Agent 的删除处理器（server.py 调用）
├── tests/
│   ├── test_scan.py               # Claude cwd/orphan + 快照权限 + Codex 只读回归测试
│   ├── test_cleanup_claude_config.py # 配置清理安全、session 竞态和备份测试
│   ├── test_codex_ui_state.py     # 侧栏状态解析/剪除/门禁/幂等/删除链路收尾测试
│   └── test_report_gate.py        # 清理成功标记与报告入口闸门测试
└── assets/
    └── report_template.html       # 报告模板（只读/删除两态，看 __DELETE_CONFIG__ 切换）
```

## Codex 侧栏幽灵条目：边界与已知残余风险

- **`state_5.sqlite` 的 `projects` 行不自动删除。** 项目下所有会话删光后 `projects` 行会
  残留，侧栏项目分组随之残留；但无法在本机确证 Codex 原生删除是否清理这些行
  （Codex/ChatGPT 是闭源 Electron 应用，无公开删除协议可复刻），按「不确定则不做」原则，
  本 skill 不碰 `projects` 表。如需手工清理：退出 ChatGPT 后用 sqlite3 删除
  `projects` 中无任何 `threads.cwd` 引用的行（操作前自行备份 `state_5.sqlite`）。
- **账号云同步可能拉回幽灵条目。** Codex 侧栏列表部分来自账号云端（跨设备同步）。本机
  状态文件清理后重开 App，个别旧条目可能被云端重新拉回（点开仍报 no rollout found）。
  这是预期内的残余风险：再次运行本 skill 清理即可；被云拉回且无法本地根除的条目，在
  Codex 侧栏对条目手动删除一次（其原生删除会同步到云端），之后不会再回来。
- **为什么 CLI `codex` 进程不参与运行门禁**：`.codex-global-state.json` 是 Electron 侧栏
  状态，只有 ChatGPT App（内嵌 Codex）/遗留独立 Codex App 会重写；Rust 的 `codex` CLI
  引擎不碰它。若把 CLI 进程也纳入 pgrep 门禁，日常 CLI 会话会让清理永远不可用。

## 依赖与运行前提

- 纯 **Python 3 标准库**，零第三方依赖。
- macOS 自带 `python3`、`osascript`（关闭 app、移废纸篓用），开箱即用；首次移废纸篓会弹访达
  自动化授权，点允许即可。
- Windows：命令里的 `python3` 改用 `python` 或 `py -3`；`close_agents.py` 用 `taskkill` 代替
  `pkill`、`server.py` 移废纸篓走 `SHFileOperationW`，已写但未在真实 Windows 实测。
- 本 skill 是 agent 驱动：扫描产出数据后由 agent 在对话里给摘要，交互报告由用户在浏览器里
  查看与删除（`server.py` 用完按 Ctrl+C 停，按钮即失效）。
