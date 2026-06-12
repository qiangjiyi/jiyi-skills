<div align="center">

# 🧹 jiyi-skills

**个人 AI Agent Skills 仓库**

一组可复用、可迭代的 AI Agent Skills，遵循 `SKILL.md` 约定，
面向 Claude Code、Codex 等支持 skills 的 Agent。

当前开源：[**`session-analyzer`**](session-analyzer) — AI Agent 会话分析与清理助手

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
└── README.md
```

> 每个 skill 直接放在仓库根目录下，一个目录一个 skill，不再额外嵌套 `skills/` 层。

> 这是我个人长期维护的 skills 仓库，会逐步沉淀更多 skill。目前仅开源 `session-analyzer`，其余仍在本地迭代。

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
