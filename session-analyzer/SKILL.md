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
生成交互式 HTML 报告，并可在网页上一键删除。流程：可选关 app → 扫描 → 出报告（默认带删除）。

与 storage-analyzer 同构：**一个模板 + 两个入口**。模板 `report_template.html` 看注入的
`__DELETE_CONFIG__` 决定渲染成只读还是带删除按钮——`build_report.py` 注入 `null`（静态、
`file://` 双击、无删除），`server.py` 注入真实 `{token, endpoint, enabled}`（起本地服务、
有删除按钮）。

## 铁律

- **扫描只读。** `scan.py` 只做 `os.scandir`/`stat`/只读 SQLite SELECT/读 jsonl，绝不写盘。
  删除只发生在 `server.py`（经 `agent_delete.py`），且只删扫描里出现过的会话/项目。
- **删除安全模型（server.py）。** 绑 127.0.0.1 + 随机端口 + 随机 token；每个 POST 校验
  token + Host（挡 DNS-rebinding）；只接受扫描里存在的 `(agent, scope, project_id, session_id)`，
  客户端无法指定任意路径/id；每个处理器只碰该 Agent 自己的数据目录。
- **默认移废纸篓（可逆）。** 文件级删除走废纸篓；但 **Codex 的 DB 行 / jsonl 索引行天生硬删**
  （数据库行没法进废纸篓），Antigravity 侧栏索引 `agyhub_summaries_proto.pb` 同样就地硬改——
  这些不可逆，UI 已用红色警示并二次确认。
- **路径、命令、thread id 原文展示，不翻译。** 不读取、不展示任何密钥/凭据内容。

## 执行流程

### Step 0（可选）关闭 Codex / Antigravity

```bash
python3 scripts/close_agents.py
```

`close_agents.py` 先 `osascript quit` 优雅退出、等不到再 `pkill` 强制结束，**只关这两个
app，不动其它进程**。脚本会逐步打印检测与关闭过程（如「⚠ 检测到 Codex 正在运行，
即将自动关闭 Codex…」「✓ 已关闭 Codex」），**agent 要把这些信息如实转述给用户**。

只读扫描本身不强制要求关闭这两个 app——放在开头是让用户尽早看到「要不要先关」，
避免看完报告去手工删时撞到 app 占用。

### Step 1 只读扫描

```bash
python3 scripts/scan.py > /tmp/session_scan.json
```

`scan.py` 自动探测三个 Agent 是否安装（数据目录是否存在），未装则跳过并标注。产出统一
JSON：每个 Agent → 项目 → 会话三级，含每层 size、会话数、mtime、摘要、孤儿标记。

`scan.py` 在 stderr 还会打一行自检 `[scan] agents=N sessions=... orphans=... size=...
in X.Xs`，方便 agent 区分「真扫到 0」vs「扫坏了」。脚本末行稳定输出 `✓ DONE`。
**空态快通道**：若 `sessions == 0 && orphans == 0`，stderr 多打一行
`✓ 本机 AI 会话状态干净（0 会话 / 0 孤儿），无需清理。`——此时可**直接进入 Step 3 给摘要
就结束**，不必生成报告。

各 Agent 的会话/项目定义：
- **Codex**（`~/.codex/`）：会话 = `state_5.sqlite` 的 `threads` 行（含 `unknown` 等所有 source）；项目按 `cwd` 聚合。
- **Antigravity**（`~/.gemini/antigravity/`）：新版会话 = 侧栏索引 `agyhub_summaries_proto.pb` 里的每条记录（id/标题/时间/workspace 均解析自该 proto），按 workspace 路径归类成项目；兼容旧版未迁移时残留的 `conversations/<uuid>.pb`；无对应对话的 brain 目录单列「孤儿残留」。
- **Claude Code**（`~/.claude/`）：会话 = `projects/<编码路径>/<uuid>.jsonl`；项目 = 该目录（真实路径优先从 `history.jsonl` 反查）；0-jsonl 的空目录单列为「空/孤儿目录」。

### Step 2 生成报告（两个入口，默认带删除）

**默认用一键删除模式（`server.py`）打开**，因为这个 skill 的核心价值就是网页上直接清理会话：

```bash
python3 scripts/server.py /tmp/session_scan.json   # 自动开浏览器，Ctrl+C 停
```

`server.py` 起在 127.0.0.1 + 随机端口 + 随机 token，把同一套模板注入启用态 `__DELETE_CONFIG__`。
三栏对比三个 Agent，可展开「项目 → 会话」树；每条会话有「删除」、每个项目有「删除整个项目」、
每个 Agent 有「🧹 一键清理 N 个孤儿会话」。删除默认移废纸篓（可逆），Codex/索引类硬删项会红色
二次确认。删除经 `agent_delete.py`，复刻各 Agent 原始清理工具的 removal set（Codex 行+jsonl+
卫星文件、Claude jsonl+session 目录、Antigravity 卫星文件 + 侧栏索引 proto 改写）。

仅当用户明确只想要一份可分享/留存的只读文件时，才用静态模式（无删除按钮，`file://` 打开
碰不到本地服务）：

```bash
python3 scripts/build_report.py /tmp/session_scan.json ~/Desktop/session-report.html && open ~/Desktop/session-report.html
```

`build_report.py` 注入 `__DELETE_CONFIG__ = null`，渲染成纯只读报告，可直接分享/留存。默认输出
`~/Desktop/session-report.html`，第二个参数可指定任意路径。

**排障：网页上没有删除按钮** = 开的是静态报告（改用 `server.py`），或服务已被 Ctrl+C 停掉。

### Step 3 对话里给摘要

报告生成后，在对话里给结论先行的一段话：三个 Agent 合计占用、占用最大的 Agent、
孤儿会话总数、最该先关注的项。细节让用户看 HTML。

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
      "projects": [                // ← list
        {
          "id": "...", "label": "...", "real_path": "...",
          "orphan": false, "session_count": 0, "size": 0,
          "sessions": [            // ← list
            { "id": "...", "title": "...", "snippet": "...",
              "mtime": 0, "size": 0, "extra": {} }
          ]
        }
      ]
    }
  ]
}
```

各 Agent 的 `total_size / session_count / project_count / orphan_session_count` 已预聚合，
摘要直接取用，不必自己累加 sessions。

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
│   ├── close_agents.py            # 可选：关闭 Codex / Antigravity
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
