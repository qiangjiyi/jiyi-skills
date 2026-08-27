#!/bin/bash
# wx-video-downloader 的服务生命周期管理。
# 复用 ~/.local/bin/wx-video 的安装布局（~/.local/share/qiaomu-wx-video/backend/current）。
#
# 用法:
#   service.sh status            查看运行状态（运行中退出码 0，未运行退出码 1）
#   service.sh start             确保后端在后台运行（已运行则不动），等健康检查通过
#   service.sh stop              停止后端并按启动前快照恢复系统代理（委托 wx-video stop）
#   service.sh health            探测 MCP 端点是否应答（运行中退出码 0）
#   service.sh ensure            前置依赖检测（只报告不改动；全部就绪退出码 0，有缺失退出码 2）
#   service.sh ensure --install  执行缺失项的安装（应先向用户展示检测报告并取得同意）
set -u

PORT=2022
MCP_URL="http://127.0.0.1:${PORT}/mcp"
BIN="wx_video_download"
BASE="$HOME/.local/share/qiaomu-wx-video/backend"
CURRENT="$BASE/current"
WRAPPER="$HOME/.local/bin/wx-video"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_WRAPPER="$SKILL_DIR/assets/wx-video"

backend_dir() {
  if [ -L "$CURRENT" ]; then readlink "$CURRENT"
  else ls -d "$BASE"/v* 2>/dev/null | sort | tail -1; fi
}

listening() {
  lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1
}

mcp_alive() {
  curl -s --noproxy '*' --max-time 5 -X POST "$MCP_URL" \
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1.0"}}}' \
    2>/dev/null | grep -q '"capabilities"'
}

cmd_status() {
  if listening; then
    pids=$(pgrep -f "$BIN" | tr '\n' ' ')
    echo "运行中 (PID: ${pids:-未知})，管理页 http://127.0.0.1:$PORT/ ，MCP $MCP_URL"
    return 0
  fi
  echo "未运行"
  return 1
}

cmd_start() {
  if listening && mcp_alive; then
    echo "后端已在运行，MCP 正常"
    return 0
  fi
  local d
  d=$(backend_dir)
  [ -z "$d" ] && { echo "未找到已安装的后端，请先运行: wx-video update"; return 1; }
  # 优先委托 wx-video 后台启动：由它负责「启动前代理快照 / 停止时恢复」
  if [ -x "$WRAPPER" ]; then
    "$WRAPPER" start --detach
  else
    (cd "$d" && nohup "./$BIN" >> app.log 2>&1 &)
  fi
  for i in $(seq 1 20); do  # 最多等 10 秒
    listening && mcp_alive && { echo "后端已就绪，MCP $MCP_URL"; return 0; }
    sleep 0.5
  done
  echo "启动超时，查看日志: $d/app.log" >&2
  return 1
}

cmd_stop() {
  if [ -x "$WRAPPER" ]; then
    "$WRAPPER" stop
  else
    pkill -INT -f "$BIN" && echo "已发送停止信号"
  fi
}

cmd_health() {
  if mcp_alive; then echo "MCP 端点正常: $MCP_URL"; return 0; fi
  echo "MCP 端点无响应" >&2; return 1
}

cmd_ensure() {
  local install=0
  [ "${1:-}" = "--install" ] && install=1
  local missing=0

  # 1) Node >= 18（mcp.mjs 依赖）
  if command -v node >/dev/null 2>&1 && node -e 'process.exit(Number(process.versions.node.split(".")[0])>=18?0:1)' 2>/dev/null; then
    echo "✓ Node.js $(node -v)"
  else
    echo "✗ Node.js 缺失或低于 18（MCP 客户端依赖）→ $( [ "$install" = 1 ] && echo 'brew install node' || echo '将执行 brew install node（需已装 Homebrew）')"
    if [ "$install" = 1 ]; then
      command -v brew >/dev/null 2>&1 && brew install node || { echo "  未找到 Homebrew，请手动安装 Node ≥18"; missing=1; }
    else
      missing=1
    fi
  fi

  # 2) wx-video 命令（服务管理 + 代理快照）
  if [ -x "$WRAPPER" ]; then
    echo "✓ wx-video 命令 ($WRAPPER)"
  elif [ -f "$ASSET_WRAPPER" ]; then
    echo "✗ wx-video 命令缺失 → $( [ "$install" = 1 ] && echo "从 skill assets 复制到 $WRAPPER" || echo "将从 skill assets 安装到 $WRAPPER" )"
    if [ "$install" = 1 ]; then
      mkdir -p "$(dirname "$WRAPPER")" && cp "$ASSET_WRAPPER" "$WRAPPER" && chmod +x "$WRAPPER" && echo "  已安装" || { echo "  安装失败"; missing=1; }
    else
      missing=1
    fi
  else
    echo "✗ wx-video 命令缺失，且 skill assets 中无安装源（skill 目录不完整）"
    missing=1
  fi

  # 3) 后端二进制
  local d
  d=$(backend_dir)
  if [ -n "$d" ] && [ -x "$d/$BIN" ]; then
    echo "✓ 后端已安装 ($(basename "$d"))"
  elif [ -x "$WRAPPER" ] || [ "$install" = 1 ]; then
    echo "✗ 后端未安装 → $( [ "$install" = 1 ] && echo '从 GitHub 官方 Release 下载（约 17MB）' || echo '将从 GitHub 官方 Release 下载最新版（约 17MB）')"
    if [ "$install" = 1 ]; then
      "$WRAPPER" update || missing=1
    else
      missing=1
    fi
  else
    echo "✗ 后端未安装（需先安装 wx-video 命令）"
    missing=1
  fi

  # 4) SunnyNet 根证书（MITM 解密必需；安装需要管理员权限，无法静默完成）
  if security find-certificate -c SunnyNet -a /Library/Keychains/System.keychain >/dev/null 2>&1 || security find-certificate -c SunnyNet >/dev/null 2>&1; then
    echo "✓ SunnyNet 根证书已信任"
  else
    echo "! SunnyNet 根证书未安装：首次启动后端时会尝试安装并弹出系统授权（或先在终端运行一次: sudo $WRAPPER)"
  fi

  # 5) ffmpeg（仅 MP3 模式需要；按需安装，不在自举范围内）
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "✓ ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)（MP3 模式可用）"
  else
    echo "! ffmpeg 未安装（仅影响 MP3 提取模式；需要时按 SKILL.md 的 MP3 小节经同意后 brew 安装）"
  fi

  if [ "$missing" = 1 ]; then
    echo "结论：存在缺失的关键依赖$( [ "$install" = 1 ] && echo '（部分安装未完成，见上）' || echo '；运行 service.sh ensure --install 安装（先取得用户同意）')"
    return 2
  fi
  echo "结论：关键依赖全部就绪$( [ "$install" = 1 ] && echo '，安装完成' )"
  return 0
}

case "${1:-}" in
  status) cmd_status ;;
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  health) cmd_health ;;
  ensure) shift; cmd_ensure "${1:-}" ;;
  *) echo "用法: service.sh status|start|stop|health|ensure [--install]"; exit 1 ;;
esac
