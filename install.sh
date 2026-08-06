#!/usr/bin/env bash
# vision-mcp 本地安装脚本：创建虚拟环境并安装依赖。
# 用法: ./install.sh   或   bash install.sh
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
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