#!/usr/bin/env bash
# vision-mcp 本地安装脚本：创建虚拟环境并安装依赖。
# 用法: ./install.sh   或   bash install.sh
set -euo pipefail

cd "$(dirname "$0")"

# 优先走 uv（若已安装）：一行同步环境 + 依赖
if command -v uv >/dev/null 2>&1; then
    echo "[vision-mcp] 检测到 uv，使用 uv sync 安装 ..."
    uv sync
    echo "[vision-mcp] 安装完成。虚拟环境: $(pwd)/.venv (uv 管理)"
    exit 0
fi

# 无 uv 时回退：探测原生解释器 + venv（可用环境变量 PYTHON 覆盖）
echo "[vision-mcp] 未检测到 uv，回退到 python venv 安装 ..."

# 自动探测可用的 Python 解释器（可用环境变量 PYTHON 覆盖）
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            PYTHON="$cand"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "[vision-mcp] 错误: 未找到 python3 / python，请先安装 Python 3.10+" >&2
    exit 1
fi
echo "[vision-mcp] 使用 Python: $($PYTHON --version 2>&1)"

if [ ! -d ".venv" ]; then
    echo "[vision-mcp] 创建虚拟环境 .venv ..."
    "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[vision-mcp] 升级 pip 并安装依赖 ..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "[vision-mcp] 安装完成。启动 / 接入方式见 README.md。"
echo "  虚拟环境 Python: $(pwd)/.venv/bin/python"