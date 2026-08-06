# vision-mcp Windows 安装脚本：创建虚拟环境并安装依赖。
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1
#       或 .\install.ps1 [-Python python]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

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
