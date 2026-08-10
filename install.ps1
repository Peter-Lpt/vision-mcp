# vision-mcp Windows 安装脚本：创建虚拟环境并安装依赖。
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1   （自动探测 py > python > python3）
#       或 .\install.ps1 [-Python py3]                        （显式指定解释器）
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 优先走 uv（若已安装）：一行同步环境 + 依赖
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Host "[vision-mcp] 检测到 uv，使用 uv sync 安装 ..."
    & uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync 失败" }
    Write-Host "[vision-mcp] 安装完成。虚拟环境: $PSScriptRoot\.venv (uv 管理)"
    exit 0
}

# 无 uv 时回退：探测原生解释器 + venv（可用 -Python 覆盖）
Write-Host "[vision-mcp] 未检测到 uv，回退到 python venv 安装 ..."

# 自动探测可用的 Python 解释器：py > python > python3（可用 -Python 覆盖）
if ([string]::IsNullOrWhiteSpace($Python)) {
    foreach ($cand in @("py", "python", "python3")) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) {
            $Python = $cand
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    Write-Host "[vision-mcp] 错误: 未找到 py / python / python3，请先安装 Python 3.10+"
    exit 1
}

Write-Host "[vision-mcp] 使用 Python: $((& $Python --version 2>&1))"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[vision-mcp] 创建虚拟环境 .venv ..."
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
}

Write-Host "[vision-mcp] 升级 pip 并安装依赖 ..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败" }

Write-Host ""
Write-Host "[vision-mcp] 安装完成。接入方式见 README.md"
Write-Host "  虚拟环境 Python: $venvPython"
