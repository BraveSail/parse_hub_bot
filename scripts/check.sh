#!/usr/bin/env bash
# 提交前的静态检查: ruff (风格/未用变量) + pylint (真错误)。
#
# 为什么要有 pylint: ruff 抓不到"名字导错模块"这类问题 ——
#   from parsehub.utils.helpers import get_parse_author_name   # 真名在 plugins.helpers
# 这种延迟 import (写在函数体内) 在模块加载时不报错, 只在该分支执行时炸,
# 测试若没覆盖那条分支就会一路绿到生产 (2026-10-04 实际踩过, 用户报 ImportError)。
# pylint 的 E0611 (no-name-in-module) / E0611 正好抓它。
#
# 用法: bash scripts/check.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

echo "==> ruff check"
uvx ruff check . || exit 1

echo
echo "==> pylint --errors-only (导入错 / 未定义名 / 调用签名不符)"
if ! "$PY" -c "import pylint" 2>/dev/null; then
    echo "    !! pylint 未安装: uv pip install pylint"
    exit 1
fi
"$PY" -m pylint --errors-only plugins/ services/ core/ repo/ utils/ db/ lib/src/parsehub
echo
echo "==> 完成 (上面若有既存问题, 新增的必须先修掉)"
