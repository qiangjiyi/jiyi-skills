---
name: skill-updater
description: >
  Skill 与插件更新同步助手。一键把本地通过 git 安装/软链接的 skill，以及 Claude Code
  插件，统一拉取到与 GitHub 远端一致的最新版本，并出一份中文汇总报告。务必在以下场景使用：
  用户说"更新 skill""同步 skill""拉一下最新的 skill""skill 有没有新版""更新插件"
  "update skills""sync skills""把 skill 都更新到最新""我那些开源 skill 该更新了"
  "plugin 更新""更新一下我的技能"，或任何想把本地装的 skill / 插件批量拉到最新的时候。
  本 skill 自动处理三种来源：软链接背后的源仓库、目录自身即 git 仓库的 skill、以及
  Claude Code 插件；脏仓库（有本地改动或本地领先远端）会自动跳过并警告，绝不动你的改动。
---

# Skill Updater

把本地安装的 skill 和 Claude Code 插件，一键同步到与远端一致的最新版本。

## 它解决的问题

skill 通常以**软链接**装在 agent 的 skill 目录里：`~/.claude/skills/<name>`（Claude Code）或
`~/.codex/skills/<name>`（Codex）指向源仓库里的某个子目录（比如 `.../baoyu-skills/skills/baoyu-comic`）。
默认同时扫这两个目录，不存在的自动跳过、重复仓库按仓库根去重。开源 skill 更新很勤，
skill 多了之后一个个 `git pull` 很累。这个 skill 把更新固化成一条命令，覆盖三种来源：

1. **软链接背后的源仓库** —— 从软链接目标向上回溯找到 git 仓库根，对仓库根 pull。
2. **目录自身即 git 仓库的 skill** —— 直接对该目录 pull（和上面统一处理，无需特判）。
3. **Claude Code 插件** —— 走官方 CLI `claude plugin update`，名单以 `installed_plugins.json` 为准。

多个软链接常指向同一个源仓库，脚本会**按仓库根去重**，每个仓库只 pull 一次。

## 怎么做

核心逻辑都在 `scripts/sync.py` 里（确定性操作，不要手搓 git）。**默认直接执行更新，不必先预览**——脏仓库（本地有改动/领先/无 upstream）本来就会自动跳过，不会动用户的东西，所以直接更新是安全的。流程：

1. **直接执行更新**（默认行为，用户说"更新 skill / 同步一下"就走这个）：
   ```bash
   python3 <skill-dir>/scripts/sync.py
   ```
   只有当用户**明确**要求"先看看 / 预览 / dry run / 先别真改"时，才先加 `--dry-run` 跑一遍给他看：
   ```bash
   python3 <skill-dir>/scripts/sync.py --dry-run
   ```
2. **呈现报告 + 提醒生效**。把脚本输出的中文汇总转述给用户，重点说清：
   - 哪些仓库更新了、更新了几个 commit；
   - **哪些仓库被跳过、为什么**（脏仓库需用户手动处理，这部分别一带而过）；
   - 插件更新后，**需要重启 Claude Code 或 reload 才生效**——务必提醒。

`<skill-dir>` 指本 skill 所在目录（即本 SKILL.md 的同级），用绝对路径调用脚本。

## 常用参数

- `--dry-run`：只检测不更新，预览将发生什么。
- `--skills-only`：只更新 skill 仓库，不动插件。
- `--plugins-only`：只更新插件，不动 skill 仓库。
- `--scan-dir <路径>`：追加额外的 skill 扫描目录（可多次）；默认扫 `~/.claude/skills` 和 `~/.codex/skills`。

按用户意图选参数：只提"更新插件"就 `--plugins-only`，只提"更新 skill"就 `--skills-only`，
笼统说"全更新一下"就不带 these（两者都更新）。

## 跳过规则（重要，别绕过）

脚本对这几类仓库**故意跳过、只警告不动手**，这是刻意的安全设计，不要试图 force pull 或 stash：

- **有本地未提交改动**：可能是用户正在改的 skill（如自己的 `jiyi-skills`），动了会丢工作。
- **本地领先远端 / 已分叉**：用户有没推送的 commit，ff-only 拉取会失败，强拉有风险。
- **无远端跟踪分支**：没有 upstream，不知道往哪拉。

这些会在报告末尾的"⚠️ 需手动处理"里列出仓库路径，交给用户自己决定。你要做的是把它们**清楚地**
转述给用户，而不是替他做主。

## 报告示例

```
## Skill 仓库（扫描目录：~/.claude/skills, ~/.codex/skills）
发现 7 个去重后的 git 仓库。
✅ 已更新   baoyu-skills   已更新 9 个 commit
⏭️  跳过    jiyi-skills    本地有未提交改动
...
## 汇总
Skill 仓库：更新 4 ｜ 已最新 1 ｜ 跳过 2 ｜ 错误 0
⚠️  以下仓库被跳过，需你手动处理：
   - jiyi-skills（本地有未提交改动）：/Users/jiyi/Projects/active/jiyi-skills
```
