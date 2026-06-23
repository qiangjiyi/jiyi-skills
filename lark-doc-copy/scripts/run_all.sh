#!/bin/bash
#
# 飞书文档复制 skill - 一键执行入口
#
# 按顺序执行所有步骤：
#   0. preflight.sh        - 环境自检（递归子调用可用 LARK_DOC_COPY_SKIP_PREFLIGHT=1 跳过）
#   1. 01_fetch_source.py   - 读取源文档+下载图片
#   2. 02_create_doc.py     - 创建新文档（默认根目录，可传第 2 参数指定目录）
#   3. 03_post_process.py   - 后处理（映射、锚点、图片、seq、嵌套）
#   3.5 process_cites.py    - 处理被引用的其它飞书文档（cite 递归复制+重指向）
#   4. 04_verify.py         - 内容与图片位置核验
#   5. 05_cleanup.py        - 清理临时文件
#
# 用法：
#   bash scripts/run_all.sh <source-doc-url> [target-folder-token]
#
# 第 2 参数（target-folder-token）主要供 cite 递归子调用使用：让被引用文档
# 与主文档落在同一目录。普通用户直接调用时省略即可（默认根目录）。

set -e

if [ -z "$1" ]; then
    echo "用法: bash scripts/run_all.sh <source-doc-url> [target-folder-token]"
    echo "示例: bash scripts/run_all.sh https://xxx.feishu.cn/docx/TOKEN"
    exit 1
fi

SOURCE_URL="$1"
TARGET_DIR_TOKEN="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "  飞书文档复制 - 一键执行"
echo "========================================"
echo "源文档: $SOURCE_URL"
[ -n "$TARGET_DIR_TOKEN" ] && echo "目标目录 token: $TARGET_DIR_TOKEN"
echo ""

# 第 0 步：自检（递归子调用跳过，避免重复探测登录态）
if [ "$LARK_DOC_COPY_SKIP_PREFLIGHT" != "1" ]; then
    echo ""
    echo "[执行] 第 0 步：环境自检"
    echo "----------------------------------------"
    bash "$SCRIPT_DIR/preflight.sh"
    if [ $? -ne 0 ]; then
        echo "❌ 自检失败，停止执行"
        exit 1
    fi
fi

# 第 0.5 步：尝试原生复制（drive files copy）—— 与 cite 引用文档处理同策略：
# 能原生复制就原生复制（保真最高、最快、无扒取重建的 seq/grid/图片 bug），
# 失败再退回扒取重建。成功时写 state.native_copy=True。
echo ""
echo "[执行] 第 0.5 步：尝试原生复制（drive files copy）"
echo "----------------------------------------"
if [ -n "$TARGET_DIR_TOKEN" ]; then
    python3 "$SCRIPT_DIR/00_try_native.py" --source "$SOURCE_URL" \
        --target-dir-token "$TARGET_DIR_TOKEN" --target-dir-name "引用文档同目录"
else
    python3 "$SCRIPT_DIR/00_try_native.py" --source "$SOURCE_URL"
fi

NATIVE_OK=$(python3 -c "import json
try:
    print('1' if json.load(open('state.json')).get('native_copy') else '0')
except Exception:
    print('0')")

if [ "$NATIVE_OK" = "1" ]; then
    echo ""
    echo "[跳过] 原生复制成功 → 跳过第 1/2/3 步（扒取重建），直接做 cite 重指向 + 核验"
    echo "----------------------------------------"
else
    # 第 1 步：读取源文档
    echo ""
    echo "[执行] 第 1 步：读取源文档 + 下载图片"
    echo "----------------------------------------"
    python3 "$SCRIPT_DIR/01_fetch_source.py" --source "$SOURCE_URL"

    # 第 2 步：创建新文档
    echo ""
    echo "[执行] 第 2 步：创建新文档"
    echo "----------------------------------------"
    if [ -n "$TARGET_DIR_TOKEN" ]; then
        python3 "$SCRIPT_DIR/02_create_doc.py" --source "$SOURCE_URL" \
            --target-dir-token "$TARGET_DIR_TOKEN" --target-dir-name "引用文档同目录"
    else
        python3 "$SCRIPT_DIR/02_create_doc.py" --source "$SOURCE_URL"
    fi

    # 第 3 步：后处理
    echo ""
    echo "[执行] 第 3 步：后处理（映射、锚点、图片、seq、嵌套、合并连续 blockquote）"
    echo "----------------------------------------"
    python3 "$SCRIPT_DIR/03_post_process.py"
fi

# 第 3.5 步：处理被引用文档（cite 递归复制 + 重指向）
echo ""
echo "[执行] 第 3.5 步：处理被引用的其它飞书文档（cite 递归）"
echo "----------------------------------------"
python3 "$SCRIPT_DIR/process_cites.py"

# 第 4 步：核验
echo ""
echo "[执行] 第 4 步：内容、图片位置、重复项核验"
echo "----------------------------------------"
python3 "$SCRIPT_DIR/04_verify.py"

# 第 4.5 步：在清理前，明确打印「真·主文档」链接（仅顶层）。
# 否则主文档链接从不被显式打印，用户只能从 cite 列表里猜——而 cite 列表里
# 可能出现同名引用文档，极易把引用副本误当成主文档（见 process_cites self 登记注释）。
# state.json 会被第 5 步清理，所以必须在清理前读取并打印。
if [ "${LARK_DOC_COPY_DEPTH:-0}" = "0" ]; then
    python3 - <<'PYEOF'
import json
try:
    s = json.load(open("state.json"))
    url = s.get("new_doc_url") or (f"https://feishu.cn/docx/{s.get('new_doc_id')}" if s.get("new_doc_id") else None)
    if url:
        print("")
        print("========================================")
        print("  📄 主文档副本（这才是你要的源文档副本）")
        print(f"  {url}")
        print("========================================")
except Exception:
    pass
PYEOF
fi

# 第 5 步：清理
echo ""
echo "[执行] 第 5 步：清理临时文件"
echo "----------------------------------------"
python3 "$SCRIPT_DIR/05_cleanup.py"

echo ""
echo "========================================"
echo "  ✅ 飞书文档复制全部完成"
echo "========================================"
