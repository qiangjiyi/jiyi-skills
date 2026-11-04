#!/usr/bin/env bash
# 编排用 worktree 管理：在「主工作区有未提交改动」时也能安全派发与集成，全程不在主分支上产生提交。
#
# 用法（<repo> 为主仓库路径）:
#   worktree.sh baseline  <repo>              用临时暂存区把当前工作区（含未跟踪文件）快照成分支 orch/base；不动真实暂存区 / HEAD
#   worktree.sh add       <repo> <task-id>    从 orch/base 建 worktree（<repo>/../.orch-wt/<task-id>），并软链 node_modules
#   worktree.sh commit    <repo> <task-id> <message>
#                                             review 通过后在 worktree 里提交（自动避开 node_modules 软链接）
#   worktree.sh integrate <repo> <branch>     把 orch/base..<branch> 的改动直接写回主工作区（不提交）；
#                                             主工作区在 baseline 之后改过、且与 <branch> 不一致的文件会中止，不覆盖
#   worktree.sh clean     <repo>              删除全部 .orch-wt worktree 与 orch/* 分支（需用户同意后再跑）
#
# 为什么需要：worktree 只能从提交创建，直接 `worktree add HEAD` 会丢掉主工作区未提交的改动；
# gitignore 里的 `node_modules/` 只匹配目录，匹配不到软链接，`git add -A` 会把软链接提交进去。

set -euo pipefail
die() { echo "worktree: $*" >&2; exit 1; }

cmd=${1:-}; repo=${2:-}
[[ -n "$cmd" && -n "$repo" ]] || die "用法见文件头"
repo=$(cd "$repo" && pwd)
wt_root="$(dirname "$repo")/.orch-wt"

# 当前工作区（含未跟踪、不含忽略文件）对应的 tree 对象，不动真实暂存区
worktree_tree() {
  local idx; idx=$(mktemp)
  cp "$repo/.git/index" "$idx"
  GIT_INDEX_FILE="$idx" git -C "$repo" add -A
  GIT_INDEX_FILE="$idx" git -C "$repo" write-tree
  rm -f "$idx"
}

case $cmd in
  baseline)
    tree=$(worktree_tree)
    commit=$(git -C "$repo" commit-tree "$tree" -p HEAD -m "orch: baseline snapshot of working tree")
    git -C "$repo" branch -f orch/base "$commit"
    echo "orch/base -> $commit"
    echo "提醒：确认快照里没有密钥 / 数据库 / 本地配置（它们应在 .gitignore 里）："
    git -C "$repo" ls-tree -r --name-only orch/base | grep -iE '\.env$|\.db$|secret|credential|cookies|\.pem$|\.key$' || echo "  未发现可疑文件"
    ;;
  add)
    task=${3:-}; [[ -n "$task" ]] || die "缺少 task-id"
    git -C "$repo" rev-parse -q --verify orch/base >/dev/null || die "先跑 baseline"
    mkdir -p "$wt_root"
    git -C "$repo" worktree add -q "$wt_root/$task" -b "orch/$task" orch/base
    [[ -d "$repo/node_modules" ]] && ln -s "$repo/node_modules" "$wt_root/$task/node_modules"
    echo "$wt_root/$task"
    ;;
  commit)
    task=${3:-}; msg=${4:-}; [[ -n "$task" && -n "$msg" ]] || die "缺少 task-id 或 message"
    dir="$wt_root/$task"
    link=0; [[ -L "$dir/node_modules" ]] && { rm "$dir/node_modules"; link=1; }
    git -C "$dir" add -A
    git -C "$dir" commit -qm "$msg"
    (( link )) && ln -s "$repo/node_modules" "$dir/node_modules"
    git -C "$dir" log --oneline -1
    ;;
  integrate)
    branch=${3:-}; [[ -n "$branch" ]] || die "缺少分支名"
    current=$(worktree_tree)
    conflicts=()
    while IFS= read -r f; do
      git -C "$repo" diff --quiet "$current" "$branch" -- "$f" || conflicts+=("$f")
    done < <(git -C "$repo" diff --name-only orch/base "$current")
    (( ${#conflicts[@]} == 0 )) || die "以下文件在主工作区 baseline 之后改过且与 $branch 不一致，先手动处理：${conflicts[*]}"
    while IFS=$'\t' read -r st f; do
      case $st in
        A|M) mkdir -p "$repo/$(dirname "$f")"; git -C "$repo" show "$branch:$f" > "$repo/$f" ;;
        D) rm -f "$repo/$f" ;;
      esac
    done < <(git -C "$repo" diff --name-status --no-renames orch/base "$branch")
    echo "已把 orch/base..$branch 写回 $repo 工作区（未提交）"
    ;;
  clean)
    for d in "$wt_root"/*/; do
      [[ -d "$d" ]] || continue
      rm -f "$d/node_modules"
      git -C "$repo" worktree remove --force "$d"
    done
    git -C "$repo" worktree prune
    git -C "$repo" for-each-ref --format='%(refname:short)' 'refs/heads/orch/' | xargs -r -n1 git -C "$repo" branch -D
    rmdir "$wt_root" 2>/dev/null || true
    echo "已清理"
    ;;
  *) die "未知命令 $cmd" ;;
esac
