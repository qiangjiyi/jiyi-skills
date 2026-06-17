---
name: session-analyzer
description: >
  AI Agent 会话分析助手。只读扫描本机 Codex、Antigravity、Claude Code 三个 Agent
  累积的会话/对话数据，按「Agent → 项目 → 会话」三级层级统计各层占用空间与会话
  数量，标记工作目录已删除的孤儿会话，生成排版精美的交互式 HTML 报告，并可起
  本地服务在网页上按会话/按项目一键删除（默认移废纸篓、可逆）。扫描全程只读。
  务必在以下场景使用：用户说"会话太多""对话记录太多""Codex/Antigravity/Claude
  会话占空间""AI Agent 历史""项目删了会话还在""孤儿会话""session cleanup"、
  想看哪个 Agent 的会话最占地方，或想批量盘点某个项目的会话情况时。
  注意：本 skill 针对三个 AI Agent 的会话数据，不是整机磁盘分析（那是
  storage-analyzer，二者互补）。
---

# Session Analyzer

对本机三个 AI Agent（Codex / Antigravity / Claude Code）的会话数据做一次只读分析，
生成交互式 HTML 报告，并可在网页上一键删除。**确定性管线：先只读扫描 → 固定决策 → 固定执行**
（关 app / 兜底清理 / 报告形态都按写死的默认值跑，不再用 AskUserQuestion 询问；仅当用户主动要求偏离时才调整对应项）。

与 storage-analyzer 同构：**一个模板 + 两个入口**。模板 `report_template.html` 看注入的
`__DELETE_CONFIG__` 决定渲染成只读还是带删除按钮——`build_report.py` 注入 `null`（静态、
`file://` 双击、无删除），`server.py` 注入真实 `{token, endpoint, enabled}`（起本地服务、
有删除按钮）。

## 铁律

- **扫描只读。** `scan.py` 只做 `os.scandir`/`stat`/只读 SQLite SELECT/读 jsonl，绝不写盘。
  删除只发生在 `server.py`（经 `agent_delete.py`），且只删扫描里出现过的会话/项目。
  唯一的例外是扫描**之前**的 `precleanup.py`：它清空目录/卫星孤儿（默认废纸篓、可逆），
  但 `scan.py` 本身仍严格只读。
- **清理范围收死。** 空目录/孤儿清理只在各 Agent 自己的数据子树内自底向上进行
  （`agent_delete.prune_roots()` 列出的根），绝不删根本身、绝不越界——空目录有时是程序占位。
  判空时忽略 `.DS_Store`/`Thumbs.db`，否则只剩系统垃圾文件的目录会"扫了还删不干净"。
- **删除安全模型（server.py）。** 绑 127.0.0.1 + 随机端口 + 随机 token；每个 POST 校验
  token + Host（挡 DNS-rebinding）；只接受扫描里存在的 `(agent, scope, project_id, session_id)`，
  客户端无法指定任意路径/id；每个处理器只碰该 Agent 自己的数据目录。
- **默认移废纸篓（可逆）。** 文件级删除走废纸篓；但 **Codex 的 DB 行 / jsonl 索引行天生硬删**
  （数据库行没法进废纸篓），Antigravity 侧栏索引 `agyhub_summaries_proto.pb` 同样就地硬改——
  这些不可逆，UI 已用红色警示并二次确认。
- **路径、命令、thread id 原文展示，不翻译。** 不读取、不展示任何密钥/凭据内容。

## 执行流程

**确定性管线，三段式：先扫描 → 固定决策 → 固定执行。** 全程无 act-vs-ask 分支：开场永远先
只读扫描（不问、不可选）；扫到东西就按**写死的默认决策**直接往下跑，**不再用 AskUserQuestion
询问**。默认决策恒为：① 关闭 Codex / Antigravity，② 开场兜底清理，③ 生成可删除交互报告——
三项全做。**禁止**把关 app、兜底清理、报告形态当成可选项临场询问或边走边拍——那正是流程每次跑
都漂移的根源。**唯一的偏离来源**：用户在对话里主动提出（如「别关 app」「只要只读静态报告」
「都不要，只看摘要」）时，才按用户所说调整对应项；用户没说就一律走默认、不主动反问。

### Step 1 始终先做：只读扫描（不问、不可选）

```bash
bash scripts/run.sh scan.py > /tmp/session_scan.json
```

> **为什么用 `run.sh` 而不是 `python3 scripts/scan.py`：** agent 的 cwd 是当前项目目录
> （如 `/Users/jiyi/Projects/active/<proj>`），不是 skill 根。直接用相对路径会
> `can't open file '.../scripts/scan.py'`。`scripts/run.sh` 自定位 skill 根再 exec，
> 从任何 cwd 都能跑。下面 ① / ② / ③ 同理。

`scan.py` 自动探测三个 Agent 是否安装（数据目录是否存在），未装则跳过并标注。产出统一
JSON：每个 Agent → 项目 → 会话三级，含每层 size、会话数、mtime、摘要、孤儿标记。

`scan.py` 在 stderr 还会打一行自检 `[scan] agents=N sessions=... orphans=... size=...
in X.Xs`，方便 agent 区分「真扫到 0」vs「扫坏了」。脚本末行稳定输出 `✓ DONE`。
**空态快通道**：若 `sessions == 0 && orphans == 0`，stderr 多打一行
`✓ 本机 AI 会话状态干净（0 会话 / 0 孤儿），无需清理。`——此时**直接跳到 Step S 给摘要就结束**，
不问决策、不生成报告。

各 Agent 的会话/项目定义：
- **Codex**（`~/.codex/`）：会话 = `state_5.sqlite` 的 `threads` 行（含 `unknown` 等所有 source）；项目按 `cwd` 聚合。
- **Antigravity**（`~/.gemini/antigravity/`）：新版会话 = 侧栏索引 `agyhub_summaries_proto.pb` 里的每条记录（id/标题/时间/workspace 均解析自该 proto），按 workspace 路径归类成项目；兼容旧版未迁移时残留的 `conversations/<uuid>.pb`；无对应对话的 brain 目录单列「孤儿残留」。
- **Claude Code**（`~/.claude/`）：会话 = `projects/<编码路径>/<uuid>.jsonl`；项目 = 该目录（真实路径优先从 `history.jsonl` 反查）；0-jsonl 的空目录单列为「空/孤儿目录」。

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

### Step 2 固定决策（扫到东西直接按默认执行，不询问）

先给一句结论先行的摘要（合计占用 / 占用最大的 Agent / 孤儿数），然后**不再用 AskUserQuestion
询问**，直接按以下写死的默认决策进入 Step 3：

1. **关闭 Codex / Antigravity：关。** 避免后续删除撞到 app 占用文件；只读扫描已跑完，关不关
   都不影响结果。
2. **开场兜底清理：清。** 跑 `precleanup.py` 清空目录 / 卫星孤儿，默认移废纸篓、可逆。
3. **报告形态：可删除交互报告**（`server.py`，本 skill 核心价值）。

> 这三项默认已固化，**无需也不要发 AskUserQuestion**。只读静态报告（`build_report.py`）/
> 「都不要，只看摘要」仍是合法形态，但**仅当用户在对话里主动要求偏离时**才切换；用户没主动说，
> 就一律走上面三项默认，直接进 Step 3。

### Step 3 按默认固定执行（顺序写死，无分支）

顺序恒为：**① 关 app → ② 兜底清理 → ③ 生成可删除交互报告 → ④ Step S 摘要。** 默认三项全做、
逐步执行；仅当用户主动要求偏离（别关 app / 换只读报告 / 不要报告）时，才跳过或替换对应那一步。

**① 关闭 Codex / Antigravity**（默认执行；用户主动说「别关」时才跳过）

```bash
bash scripts/run.sh close_agents.py
```

`close_agents.py` 先 `osascript quit` 优雅退出、等不到再 `pkill` 强制结束，**只关这两个
app，不动其它进程**。脚本会逐步打印检测与关闭过程（如「⚠ 检测到 Codex 正在运行，
即将自动关闭 Codex…」「✓ 已关闭 Codex」），**agent 要把这些信息如实转述给用户**。

**② 开场兜底清理**（默认执行；用户主动说「别清」时才跳过）

```bash
bash scripts/run.sh precleanup.py        # 默认移废纸篓（可逆）；--hard 直接删
```

清掉三个 Agent 历史遗留的**空目录**（含只剩 `.DS_Store` 的），Claude
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
卫星文件、Claude jsonl+session 目录、Antigravity 卫星文件 + 侧栏索引 proto 改写）。

只读静态报告（仅分享/留存，无删除按钮，`file://` 打开碰不到本地服务）：

```bash
bash scripts/run.sh build_report.py /tmp/session_scan.json ~/Desktop/session-report.html && open ~/Desktop/session-report.html
```

`build_report.py` 注入 `__DELETE_CONFIG__ = null`，渲染成纯只读报告。默认输出
`~/Desktop/session-report.html`，第二个参数可指定任意路径。

**排障：网页上没有删除按钮** = 开的是静态报告（改用 `server.py`），或服务已被 Ctrl+C 停掉。

### Step S 对话里给摘要

报告生成后（或空态快通道直达此处），在对话里给结论先行的一段话：三个 Agent 合计占用、
占用最大的 Agent、孤儿会话总数、最该先关注的项。细节让用户看 HTML。

**扫描 JSON 结构**（读它做摘要时照此取值，别猜——`agents` 是 **list** 不是 dict，对它用
`.items()` 会报错）：

```jsonc
{
  "generated_at": 0, "scan_seconds": 0.0, "home": "/Users/<user>",
  "agents": [                      // ← list，直接遍历
    {
      "key": "codex",             // codex | antigravity | claude
      "name": "Codex",
      "installed": true,           // false 时 projects 为空，跳过
      "note": "...",
      "project_count": 0, "session_count": 0,
      "total_size": 0,             // 字节
      "orphan_session_count": 0,
      "multica_cleanable_count": 0,  // 仅 claude：可安全清理
      "multica_active_count": 0,     // 仅 claude：进行中，不纳入一键清理
      "projects": [                // ← list
        {
          "id": "...", "label": "...", "real_path": "...",
          "orphan": false, "session_count": 0, "size": 0,
          "multica_status": "cleanable",    // 仅 Multica 项目：cleanable | active
          "multica_workspace_path": "...", // 仅 Multica 项目
          "multica_cache_path": "...",     // 仅 Multica 项目
          "multica_extra_size": 0,         // 仅 Multica 项目
          "sessions": [            // ← list
            { "id": "...", "title": "...", "snippet": "...",
              "mtime": 0, "size": 0, "extra": {
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
│   ├── scan.py                    # 只读扫描三 Agent → JSON
│   ├── close_agents.py            # 关闭 Codex / Antigravity（Step 2 选了才跑）
│   ├── precleanup.py              # 开场兜底：清空目录 + Claude 卫星孤儿 + 陈旧进程状态文件（默认废纸篓）
│   ├── agyhub_summaries.py        # Antigravity 索引 .pb 解析 + 按 id 剔除（scan/删除共用）
│   ├── build_report.py            # 注入 DELETE_CONFIG=null → 静态只读报告（入口一）
│   ├── server.py                  # 本地服务，注入启用态配置 → 带删除的交互报告（入口二）
│   └── agent_delete.py            # 各 Agent 的删除处理器（server.py 调用）
└── assets/
    └── report_template.html       # 报告模板（只读/删除两态，看 __DELETE_CONFIG__ 切换）
```

## 依赖与运行前提

- 纯 **Python 3 标准库**，零第三方依赖。
- macOS 自带 `python3`、`osascript`（关闭 app、移废纸篓用），开箱即用；首次移废纸篓会弹访达
  自动化授权，点允许即可。
- Windows：命令里的 `python3` 改用 `python` 或 `py -3`；`close_agents.py` 用 `taskkill` 代替
  `pkill`、`server.py` 移废纸篓走 `SHFileOperationW`，已写但未在真实 Windows 实测。
- 本 skill 是 agent 驱动：扫描产出数据后由 agent 在对话里给摘要，交互报告由用户在浏览器里
  查看与删除（`server.py` 用完按 Ctrl+C 停，按钮即失效）。
