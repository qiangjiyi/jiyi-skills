#!/usr/bin/env python3
"""
skill-updater 核心脚本：把本地通过 git 安装/软链接的 skill，以及 Claude Code 插件，
统一拉取到与远端一致的最新版本，最后打印一份中文汇总报告。

设计要点（与确定性强相关，单独成脚本而非让模型每次手搓 git）：
- skill 安装目录里几乎都是软链接，多个软链接可能指向同一个源仓库的不同子目录，
  必须先按"仓库根"去重，再对每个仓库只 pull 一次。
- 仓库根用 `git rev-parse --show-toplevel` 自动向上回溯得到，软链接和"目录自身即仓库"
  两种情况一视同仁，无需特判。
- 有本地未提交改动、或本地领先远端（含已 diverge）的仓库一律跳过并警告，绝不动用户改动。
- 插件走官方 CLI `claude plugin update <name@marketplace>`，名单以 installed_plugins.json 为准。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_SCAN_DIRS = ["~/.claude/skills", "~/.codex/skills"]
INSTALLED_PLUGINS_JSON = "~/.claude/plugins/installed_plugins.json"


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def git(root, *args):
    return run(["git", "-C", str(root), *args])


def expand(p):
    return Path(os.path.expanduser(p))


# ---------------------------------------------------------------- skills ----

def collect_repos(scan_dirs):
    """返回 {repo_root: sorted([skill_name, ...])}，已按仓库根去重。"""
    repos = {}
    for raw in scan_dirs:
        base = expand(raw)
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.name.startswith("."):
                continue
            try:
                physical = entry.resolve()
            except OSError:
                continue
            if not physical.is_dir():
                continue
            res = git(physical, "rev-parse", "--show-toplevel")
            if res.returncode != 0:
                continue  # 不在任何 git 仓库内，跳过
            root = res.stdout.strip()
            repos.setdefault(root, set()).add(entry.name)
    return {r: sorted(names) for r, names in repos.items()}


def classify_and_update(root, dry_run):
    """对单个仓库分类并（非 dry-run 时）执行 ff-only 更新，返回结果 dict。"""
    name = Path(root).name

    # 1. 本地未提交改动 → 跳过
    dirty = git(root, "status", "--porcelain").stdout.strip()
    if dirty:
        return {"repo": name, "root": root, "state": "skipped",
                "note": "本地有未提交改动"}

    # 2. 无上游跟踪分支 → 跳过
    if git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode != 0:
        return {"repo": name, "root": root, "state": "skipped",
                "note": "无远端跟踪分支"}

    # 3. 拉取远端引用
    fetch = git(root, "fetch", "--quiet")
    if fetch.returncode != 0:
        return {"repo": name, "root": root, "state": "error",
                "note": "fetch 失败：" + (fetch.stderr.strip() or "未知错误")}

    behind = git(root, "rev-list", "--count", "HEAD..@{u}").stdout.strip() or "0"
    ahead = git(root, "rev-list", "--count", "@{u}..HEAD").stdout.strip() or "0"

    # 4. 本地领先（或与远端 diverge）→ 跳过
    if int(ahead) > 0:
        note = "本地领先远端 %s 个 commit（未推送）" % ahead
        if int(behind) > 0:
            note = "本地与远端已分叉（领先 %s / 落后 %s）" % (ahead, behind)
        return {"repo": name, "root": root, "state": "skipped", "note": note}

    # 5. 已是最新
    if int(behind) == 0:
        return {"repo": name, "root": root, "state": "uptodate", "note": ""}

    # 6. 干净且落后 → 可快进更新
    if dry_run:
        return {"repo": name, "root": root, "state": "would_update",
                "note": "将快进更新 %s 个 commit" % behind}

    merged = git(root, "merge", "--ff-only", "@{u}")
    if merged.returncode != 0:
        return {"repo": name, "root": root, "state": "error",
                "note": "ff-only 更新失败：" + (merged.stderr.strip() or "未知错误")}
    log = git(root, "log", "--oneline", "-n", behind, "HEAD@{1}..HEAD").stdout.strip()
    return {"repo": name, "root": root, "state": "updated",
            "note": "已更新 %s 个 commit" % behind, "log": log}


# --------------------------------------------------------------- plugins ----

def list_plugins():
    path = expand(INSTALLED_PLUGINS_JSON)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return sorted(data.get("plugins", {}).keys())


def update_plugin(name, dry_run):
    if dry_run:
        return {"plugin": name, "state": "would_update", "note": "将执行 claude plugin update"}
    res = run(["claude", "plugin", "update", name])
    out = (res.stdout + res.stderr).strip()
    if res.returncode != 0:
        return {"plugin": name, "state": "error", "note": out or "更新失败"}
    return {"plugin": name, "state": "done", "note": out.splitlines()[-1] if out else ""}


# ---------------------------------------------------------------- report ----

ICON = {
    "updated": "✅ 已更新", "would_update": "🔄 待更新", "uptodate": "✓ 已最新",
    "skipped": "⏭️  跳过", "error": "❌ 错误", "done": "✅ 已更新",
}


def main():
    ap = argparse.ArgumentParser(description="同步本地 skill 仓库与 Claude Code 插件到最新")
    ap.add_argument("--dry-run", action="store_true", help="只检测不实际更新")
    ap.add_argument("--skills-only", action="store_true", help="只更新 skill 仓库，不动插件")
    ap.add_argument("--plugins-only", action="store_true", help="只更新插件，不动 skill 仓库")
    ap.add_argument("--scan-dir", action="append", default=None,
                    help="额外的 skill 扫描目录（可多次）；默认 ~/.agents/skills 和 ~/.claude/skills")
    args = ap.parse_args()

    scan_dirs = args.scan_dir if args.scan_dir else DEFAULT_SCAN_DIRS

    print("=" * 60)
    print("skill-updater" + ("  [DRY-RUN 预览模式]" if args.dry_run else ""))
    print("=" * 60)

    skill_results = []
    if not args.plugins_only:
        repos = collect_repos(scan_dirs)
        print("\n## Skill 仓库（扫描目录：%s）" % ", ".join(scan_dirs))
        print("发现 %d 个去重后的 git 仓库。\n" % len(repos))
        for root in sorted(repos):
            r = classify_and_update(root, args.dry_run)
            r["skills"] = repos[root]
            skill_results.append(r)
            print("%-10s %-22s %s" % (ICON.get(r["state"], r["state"]), r["repo"], r["note"]))
            print("            ↳ skills: %s" % ", ".join(repos[root]))
            if r.get("log"):
                for line in r["log"].splitlines():
                    print("              · " + line)

    plugin_results = []
    if not args.skills_only:
        plugins = list_plugins()
        print("\n## Claude Code 插件（共 %d 个）" % len(plugins))
        for name in plugins:
            r = update_plugin(name, args.dry_run)
            plugin_results.append(r)
            print("%-10s %s  %s" % (ICON.get(r["state"], r["state"]), name, r["note"]))

    # 汇总
    def count(results, key, *states):
        return sum(1 for x in results if x[key] in states)

    print("\n" + "=" * 60)
    print("## 汇总")
    if not args.plugins_only:
        print("Skill 仓库：更新 %d ｜ 待更新 %d ｜ 已最新 %d ｜ 跳过 %d ｜ 错误 %d" % (
            count(skill_results, "state", "updated"),
            count(skill_results, "state", "would_update"),
            count(skill_results, "state", "uptodate"),
            count(skill_results, "state", "skipped"),
            count(skill_results, "state", "error"),
        ))
        skipped = [x for x in skill_results if x["state"] == "skipped"]
        if skipped:
            print("⚠️  以下仓库被跳过，需你手动处理：")
            for x in skipped:
                print("   - %s（%s）：%s" % (x["repo"], x["note"], x["root"]))
    if not args.skills_only:
        print("插件：处理 %d ｜ 错误 %d" % (
            count(plugin_results, "state", "done", "would_update"),
            count(plugin_results, "state", "error"),
        ))
    print("=" * 60)

    # 给编排层用的机器可读结果
    has_error = any(x["state"] == "error" for x in skill_results + plugin_results)
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
