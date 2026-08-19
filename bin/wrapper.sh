#!/usr/bin/env bash
# vision-mcp 启动 wrapper:自举 Python 依赖后 exec 真正的 server.py。
# 供 MCP stdio 客户端(.mcp.json / claude mcp add 等)调用,路径按本目录相对解析,
# 仓库拷贝到任意位置均可运行。首次调用会创建 .venv(POSIX / macOS / Linux);
# Windows 请用 `python3` 直启或 install.ps1。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$(cd "${HERE}/.." && pwd)"          # = vision-mcp 根目录
SERVER="${MCP_DIR}/server.py"
VENV_PY="${MCP_DIR}/.venv/bin/python"

ensure_deps() {
    # 残缺 venv（site-packages 有包但解释器缺失/依赖不可导入）也会被重建
    if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import mcp" >/dev/null 2>&1; then
        return 0
    fi
    echo "[vision-mcp] 初始化 Python 虚拟环境并安装依赖 ..." >&2
    rm -rf "$MCP_DIR/.venv"
    if command -v uv >/dev/null 2>&1; then
        (cd "$MCP_DIR" && uv sync)
    else
        local py="${PYTHON:-python3}"
        if ! command -v "$py" >/dev/null 2>&1; then
            echo "[vision-mcp] 错误: 未找到 python3/python,请先安装 Python 3.10+" >&2
            exit 1
        fi
        "$py" -m venv "$MCP_DIR/.venv"
        "$VENV_PY" -m pip install --upgrade pip >/dev/null
        "$VENV_PY" -m pip install -r "$MCP_DIR/requirements.txt"
    fi
}

ensure_deps
exec "$VENV_PY" "$SERVER" "$@"