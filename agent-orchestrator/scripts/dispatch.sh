#!/usr/bin/env bash
# 把一份任务 brief 派发给本机的某个 AI Agent，以 headless 方式执行。
#
# 用法:
#   dispatch.sh <agent> <workdir> <brief.md> [--write] [--timeout 秒] [--run-id ID]
#   dispatch.sh health                      # 探测各 Agent 是否可用
#
# agent: codex | zcode | cursor
# --write   允许改文件（默认只读，适合 review / 调研类任务）
#
# 退出码:
#   0   执行完成（是否合格由编排者 review 决定）
#   75  Agent 不可用：未安装 / 未登录 / 限额 / 额度不足 / 超时 → 编排者应降级
#   1   其他错误（参数错、brief 不存在等）
#
# 产物: <workdir>/.orchestrator/runs/<run-id>/{brief.md,raw.log,last.md,meta.json}

set -uo pipefail

UNAVAILABLE=75
CODEX_MODEL="${ORCH_CODEX_MODEL:-gpt-6-astra}"
# 用户全局 ~/.codex/config.toml 可能是 low：低推理强度下 Codex 常常两分钟交个框架就停，派发时显式覆盖
CODEX_EFFORT="${ORCH_CODEX_EFFORT:-high}"
ZCODE_APP_RES="/Applications/ZCode.app/Contents/Resources"
ZCODE_NODE="${HOME}/.zcode/server/node"
ZCODE_SHIM="${HOME}/.cache/agent-orchestrator/zcode-shim"

# 命中这些关键字即视为"不可用"，而不是任务本身失败
LIMIT_PATTERN='rate.?limit|usage limit|quota|insufficient|exceeded|too many requests|\b429\b|hit your|limit reached|credit|billing|not logged in|login required|unauthori[sz]ed|\b401\b|Select a model|model_not_found|额度|限额|余额|请登录|未登录'

die() { echo "dispatch: $*" >&2; exit 1; }

# 把 ZCode.app 内置的 CLI 复制到一个带 provider 配置的目录。
# 直接从 app 包里运行会因找不到 provider/zcode-builtin.json 而失败。
zcode_prepare() {
  local src="$ZCODE_APP_RES/glm/zcode.cjs"
  [[ -f "$src" && -x "$ZCODE_NODE" ]] || return 1
  mkdir -p "$ZCODE_SHIM"
  if [[ ! -f "$ZCODE_SHIM/zcode.cjs" || "$src" -nt "$ZCODE_SHIM/zcode.cjs" ]]; then
    cp "$src" "$ZCODE_SHIM/zcode.cjs" || return 1
  fi
  ln -sfn "$ZCODE_APP_RES/config/provider" "$ZCODE_SHIM/provider"
  ln -sfn "$ZCODE_APP_RES/glm/packages" "$ZCODE_SHIM/packages"
}

cursor_bin() { command -v cursor-agent || command -v agent || ls "$HOME/.local/bin/cursor-agent" 2>/dev/null; }

# run_with_timeout <秒> <log> cmd...   超时返回 124
run_with_timeout() {
  local secs=$1 log=$2; shift 2
  "$@" >>"$log" 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= secs )); then
      kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null
      return 124
    fi
    sleep 2; waited=$((waited + 2))
  done
  wait "$pid"
}

health() {
  local probe; probe=$(mktemp -d)
  printf '%-8s ' codex
  if command -v codex >/dev/null && codex login status >/dev/null 2>&1; then echo "ok (model=$CODEX_MODEL, effort=$CODEX_EFFORT)"; else echo "unavailable"; fi
  printf '%-8s ' zcode
  if zcode_prepare; then
    local out
    out=$(cd "$probe" && run_with_timeout 60 /dev/stdout "$ZCODE_NODE" "$ZCODE_SHIM/zcode.cjs" -p "只回复 OK" --mode plan --cwd "$probe" 2>&1 | tail -3)
    if grep -qiE "$LIMIT_PATTERN" <<<"$out"; then echo "unavailable: $(tail -1 <<<"$out")"; else echo "ok"; fi
  else echo "unavailable (ZCode.app 未安装)"; fi
  printf '%-8s ' cursor
  if [[ -n "$(cursor_bin)" ]]; then echo "ok ($(cursor_bin))"; else echo "unavailable (未安装 Cursor CLI)"; fi
  printf '%-8s ' grokbot
  if [[ -d "/Applications/Grok Bot.app" ]]; then echo "desktop（scripts/grokbot.mjs 界面自动化，会抢焦点）"; else echo "manual（未安装桌面端，需人工转交）"; fi
  printf '%-8s %s\n' doubao "manual（无 CLI，需人工转交）"
  rm -rf "$probe"
}

[[ "${1:-}" == "health" ]] && { health; exit 0; }

agent=${1:-}; workdir=${2:-}; brief=${3:-}
[[ -n "$agent" && -n "$workdir" && -n "$brief" ]] || die "用法: dispatch.sh <codex|zcode|cursor> <workdir> <brief.md> [--write] [--timeout 秒] [--run-id ID]"
shift 3
write=0; timeout_s=1800; run_id="$(date +%Y%m%d-%H%M%S)-$agent"
while (( $# )); do
  case $1 in
    --write) write=1 ;;
    --timeout) timeout_s=$2; shift ;;
    --run-id) run_id=$2; shift ;;
    *) die "未知参数 $1" ;;
  esac
  shift
done
[[ -d "$workdir" ]] || die "workdir 不存在: $workdir"
[[ -f "$brief" ]] || die "brief 不存在: $brief"
workdir=$(cd "$workdir" && pwd)

out="$workdir/.orchestrator/runs/$run_id"
mkdir -p "$out"
cp "$brief" "$out/brief.md"
log="$out/raw.log"; last="$out/last.md"
: >"$log"
started=$(date +%s)

case $agent in
  codex)
    command -v codex >/dev/null || exit $UNAVAILABLE
    sandbox=read-only; (( write )) && sandbox=workspace-write
    # 后台进程的 stdin 会被重定向到 /dev/null，所以 brief 走参数而不是 stdin
    run_with_timeout "$timeout_s" "$log" \
      codex exec -m "$CODEX_MODEL" -c "model_reasoning_effort=\"$CODEX_EFFORT\"" -C "$workdir" -s "$sandbox" --skip-git-repo-check -o "$last" "$(cat "$out/brief.md")"
    rc=$?
    ;;
  zcode)
    zcode_prepare || exit $UNAVAILABLE
    mode=plan; (( write )) && mode=yolo
    (cd "$workdir" && run_with_timeout "$timeout_s" "$log" \
      "$ZCODE_NODE" "$ZCODE_SHIM/zcode.cjs" -p "$(cat "$out/brief.md")" --mode "$mode" --cwd "$workdir" --no-color)
    rc=$?
    tail -c 20000 "$log" >"$last"
    ;;
  cursor)
    bin=$(cursor_bin); [[ -n "$bin" ]] || exit $UNAVAILABLE
    # 没有 --trust 时，headless 模式遇到未信任的目录会直接退出
    args=(-p --trust --output-format text)
    (( write )) && args+=(--force)
    (cd "$workdir" && run_with_timeout "$timeout_s" "$log" "$bin" "${args[@]}" "$(cat "$out/brief.md")")
    rc=$?
    tail -c 20000 "$log" >"$last"
    ;;
  *) die "未知 agent: $agent（grokbot / doubao 没有 CLI，请走人工转交）" ;;
esac

status=done
if (( rc == 124 )); then status=timeout
elif (( rc != 0 )) && grep -qiE "$LIMIT_PATTERN" "$log"; then status=unavailable
elif (( rc != 0 )); then status=failed
fi

cat >"$out/meta.json" <<EOF
{"agent":"$agent","run_id":"$run_id","write":$write,"exit_code":$rc,"status":"$status","seconds":$(( $(date +%s) - started ))}
EOF

echo "run: $out"
echo "status: $status (exit $rc)"
echo "----- last message -----"
tail -c 4000 "$last" 2>/dev/null

case $status in
  done) exit 0 ;;
  timeout|unavailable) exit $UNAVAILABLE ;;
  *) exit 1 ;;
esac
