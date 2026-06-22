#!/bin/bash
#
# 第 0 步：环境自检
#
# 验证：
# 1. Lark CLI 已安装
# 2. 依赖 skill 可加载
# 3. 当前用户以 user 身份登录
#
# 用法：bash scripts/preflight.sh
#
# 返回：
#   0 - 全部通过
#   1 - Lark CLI 未安装
#   2 - 依赖 skill 缺失
#   3 - 登录状态异常

set -e

echo "========================================"
echo "  飞书文档复制 - 环境自检"
echo "========================================"

# --- 0.1 Lark CLI 检查 ---
echo ""
echo "[1/3] 检查 Lark CLI..."

if ! command -v lark-cli &> /dev/null; then
    echo ""
    echo "❌ Lark CLI 未安装"
    echo ""
    echo "请先安装 Lark CLI："
    echo "  参考：https://open.feishu.cn/document/server-side-sdk/cli"
    echo "  或使用包管理器安装"
    echo ""
    exit 1
fi

LARK_VERSION=$(lark-cli --version 2>&1 | head -1)
echo "  ✅ 已安装: $LARK_VERSION"

# --- 0.2 依赖 skill 检查 ---
echo ""
echo "[2/3] 检查依赖 skill..."

MISSING_SKILLS=()
for skill in lark-doc lark-shared; do
    SKILL_PATH="$HOME/.claude/skills/$skill/SKILL.md"
    if [ ! -f "$SKILL_PATH" ]; then
        MISSING_SKILLS+=("$skill")
    fi
done

if [ ${#MISSING_SKILLS[@]} -gt 0 ]; then
    echo ""
    echo "❌ 依赖 skill 未找到：${MISSING_SKILLS[*]}"
    echo ""
    echo "请确认这些 skill 已正确安装到 ~/.claude/skills/ 目录下。"
    echo "本 skill 依赖以下 Lark skill："
    echo "  - lark-doc: 文档操作（fetch/create/update/media-insert/media-preview）"
    echo "  - lark-shared: 认证和全局参数"
    echo ""
    exit 2
fi

echo "  ✅ lark-doc 可加载"
echo "  ✅ lark-shared 可加载"

# --- 0.3 登录状态检查 ---
echo ""
echo "[3/3] 检查登录状态..."

# 注意：`lark-cli auth status` 的输出本身就是 JSON，没有 --format 选项。
if ! AUTH_RESULT=$(lark-cli auth status 2>&1); then
    echo ""
    echo "❌ 无法获取认证状态"
    echo ""
    echo "请尝试重新登录："
    echo "  lark-cli auth login"
    echo ""
    exit 3
fi

IDENTITY=$(echo "$AUTH_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('identity', ''))" 2>/dev/null || echo "")
USER_STATUS=$(echo "$AUTH_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('identities', {}).get('user', {}).get('status', ''))" 2>/dev/null || echo "")
USER_AVAILABLE=$(echo "$AUTH_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('identities', {}).get('user', {}).get('available', ''))" 2>/dev/null || echo "")
USER_NAME=$(echo "$AUTH_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('identities', {}).get('user', {}).get('userName', ''))" 2>/dev/null || echo "")
USER_OPENID=$(echo "$AUTH_RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('identities', {}).get('user', {}).get('openId', ''))" 2>/dev/null || echo "")

if [ "$IDENTITY" != "user" ]; then
    echo ""
    echo "❌ 当前身份: $IDENTITY"
    echo ""
    echo "本 skill 操作的是用户自己的云空间，需要 user 身份。"
    echo ""
    if [ "$IDENTITY" = "bot" ]; then
        echo "请执行以下命令切换到 user 身份："
        echo "  lark-cli auth login"
    else
        echo "请执行以下命令登录："
        echo "  lark-cli auth login"
    fi
    echo ""
    exit 3
fi

# available=True 即可用；status 为 needs_refresh 时 lark-cli 会在下次 user API
# 调用时自动刷新 token，属于正常可用状态，不应拦截。
if [ "$USER_AVAILABLE" != "True" ]; then
    echo ""
    echo "❌ user 身份不可用: status=$USER_STATUS, available=$USER_AVAILABLE"
    echo ""
    echo "请重新登录："
    echo "  lark-cli auth login"
    echo ""
    exit 3
fi

echo "  ✅ 已登录为: $USER_NAME ($USER_OPENID)"

# --- 全部通过 ---
echo ""
echo "========================================"
echo "  ✅ 环境自检全部通过"
echo "========================================"
echo ""
echo "可以开始执行飞书文档复制流程。"
echo ""

exit 0
