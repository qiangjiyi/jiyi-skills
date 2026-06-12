<div align="center">

# 🧹 jiyi-skills

**个人 AI Agent Skills 仓库**

一组可复用、可迭代的 AI Agent Skills，遵循 `SKILL.md` 约定，
面向 Claude Code、Codex 等支持 skills 的 Agent。

已开源：[**`session-analyzer`**](session-analyzer) — 会话分析与清理 ·
[**`skill-updater`**](skill-updater) — Skill 与插件更新同步

<br/>

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.x-3776AB.svg?logo=python&logoColor=white)](#)
[![Platform](https://img.shields.io/badge/platform-macOS-555.svg?logo=apple)](#)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#)
[![Scan](https://img.shields.io/badge/scan-read--only-success.svg)](#)

</div>

---

## 📦 仓库内容

```text
jiyi-skills/
├── session-analyzer/   # ✅ 已开源
├── skill-updater/      # ✅ 已开源
└── README.md
```

> 每个 skill 直接放在仓库根目录下，一个目录一个 skill，不再额外嵌套 `skills/` 层。

> 这是我个人长期维护的 skills 仓库，会逐步沉淀更多 skill。目前已开源 `session-analyzer` 和 `skill-updater`，其余仍在本地迭代。

---

## 🧹 session-analyzer

对本机三个 AI Agent —— **Codex / Antigravity / Claude Code** —— 的会话数据做一次**只读**分析，产出交互式网页，并支持在网页上一键清理。

**它能做什么**

- 🔍 **只读扫描**：`os.scandir` / `stat` / 只读 SQLite SELECT / 读 jsonl，全程绝不写盘。
- 📊 **三级统计**：按「Agent → 项目 → 会话」分层统计占用空间与会话数量。
- 🧭 **孤儿识别**：标记工作目录已删除、却仍残留会话的孤儿项目。
- 🖥️ **网页对比**：本地起服务（`127.0.0.1` + 随机端口 + 随机 token），三栏直观对比三个 Agent。
- 🗑️ **安全删除**：服务端白名单校验，只能删本次扫描列出的 id；Claude / Antigravity 默认移废纸篓（可逆），Codex 数据库驱动统一硬删（不可逆），删除前浏览器二次确认。
- 📄 **静态报告**：可选导出无删除按钮的只读 HTML，便于分享留存。

**快速开始**

```bash
cd session-analyzer

# 1. 只读扫描 → JSON
python3 scripts/scan.py > /tmp/session_scan.json

# 2. 起本地服务看报告 / 在网页上清理（Ctrl+C 停）
python3 scripts/server.py /tmp/session_scan.json

# 可选：导出一份只读静态报告
python3 scripts/build_report.py /tmp/session_scan.json ~/Desktop/session-report.html
```

**依赖**：纯 Python 3 标准库，零第三方依赖。macOS 开箱即用；Windows 代码已写但未实测（见 [SKILL.md](session-analyzer/SKILL.md)）。

> 详细工作流、删除语义与安全铁律见 [`session-analyzer/SKILL.md`](session-analyzer/SKILL.md)。

---

## 🔄 skill-updater

把本地安装的 skill 和 Claude Code 插件，一键同步到与远端一致的最新版本，并出一份中文汇总报告。skill 越装越多、开源 skill 更新又勤，一个个 `git pull` 很累——这个 skill 把更新固化成一条命令。

**它能做什么**

- 🔗 **三种来源全覆盖**：软链接背后的源仓库、目录自身即 git 仓库的 skill、Claude Code 插件。
- 🧭 **按仓库根去重**：从软链接目标向上回溯到 git 仓库根，多个 skill 指向同一仓库时只 pull 一次。
- 🛡️ **脏仓库自动跳过**：有本地未提交改动、本地领先远端或无 upstream 的仓库一律跳过并警告，绝不动你的改动。
- 🧩 **插件走官方 CLI**：以 `installed_plugins.json` 为准，对每个插件执行 `claude plugin update`。
- 📋 **汇总报告**：清楚列出更新了哪些、跳过了哪些及原因。

**快速开始**

```bash
cd skill-updater

# 直接更新（默认扫 ~/.claude/skills 和 ~/.codex/skills）
python3 scripts/sync.py

# 只预览不更新
python3 scripts/sync.py --dry-run

# 只更新 skill / 只更新插件
python3 scripts/sync.py --skills-only
python3 scripts/sync.py --plugins-only
```

**依赖**：纯 Python 3 标准库 + `git`；插件更新需 `claude` CLI。

> 触发场景、跳过规则与参数详见 [`skill-updater/SKILL.md`](skill-updater/SKILL.md)。

---

## 🧩 Skill 约定

每个 skill 在仓库根目录下独立成一个目录，入口是 `SKILL.md`：

```text
<skill-name>/
├── SKILL.md        # 触发场景、工作流、依赖、注意事项
├── scripts/        # 可执行脚本或辅助程序
├── references/     # 参考资料、规则、schema
└── assets/         # 模板、静态资源、示例文件
```

其中 `SKILL.md` 必需，`scripts/`、`references/`、`assets/` 按需添加。

---

## 📄 License

[MIT](LICENSE) © jiyi
