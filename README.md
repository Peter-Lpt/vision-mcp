# vision-mcp

为纯文本主模型（Claude Code / Codex / pi 等）补齐视觉能力的轻量工具。当主模型遇到图片但无法理解时，把图片交给 OpenAI 兼容的多模态后端（Qwen-VL、GPT-4o、Gemini、本地 vLLM 等）分析，返回文本结果。

提供两种形态，共用同一套视觉后端与配置键：

- **`server.py`** — MCP server（Python），供 MCP 客户端使用；
- **`pi-extensions/vision-mcp.ts`** — pi 扩展（单文件），供 pi 使用。

## pi 扩展安装

```bash
cp pi-extensions/vision-mcp.ts ~/.pi/agent/extensions/
# 图片缩放依赖（可选，未装则自动降级为不缩放）
cd ~/.pi/agent/extensions && npm i sharp
```

复制后重启 pi 或 `/reload` 即自动加载。扩展由 pi 自动发现，无需 `pi install`。

## MCP 安装

```bash
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
# macOS / Linux
./install.sh
```

## 安装后配置引导

两种形态各自需要一份配置文件，**api_key 必填，其余可留默认**。

**pi 扩展**：创建 `~/.pi/vision-mcp/config.json`（或设置 `VISION_CONFIG_PATH` 指向任意路径）：

```bash
mkdir -p ~/.pi/vision-mcp
cat > ~/.pi/vision-mcp/config.json <<'EOF'
{
  "api_key": "sk-你的视觉后端key",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "model": "qwen-vl-plus"
}
EOF
```

**MCP**：复制模板并填写：

```bash
cp config.example.json config.json   # 再编辑 api_key 等字段
```

**验证**：pi 扩展直接提问“分析这张图”即可；MCP 运行 `python server.py --check` 确认配置生效（api_key 脱敏）。

## 配置

优先级：`config.json` > 进程环境变量 > `.env` > 默认值。`config.json` 已被 gitignore，不入库。

```json
{
  "api_key": "sk-your-dashscope-api-key",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "model": "qwen-vl-plus",
  "max_tokens": 4096,
  "timeout": 120,
  "max_retries": 2,
  "retry_backoff": 2
}
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VISION_API_KEY` | - | 视觉后端 API Key |
| `VISION_BASE_URL` | dashscope | OpenAI 兼容端点 |
| `VISION_MODEL` | `qwen-vl-plus` | 视觉模型名 |
| `VISION_MAX_TOKENS` | `4096` | 单次最大输出 token |
| `VISION_TIMEOUT` | `120` | 请求超时（秒） |
| `VISION_MAX_RETRIES` | `2` | 瞬时故障重试次数 |
| `VISION_RETRY_BACKOFF` | `2` | 重试退避基数（秒） |
| `VISION_MAX_IMAGE_BYTES` | `20971520` | 单图大小上限（字节） |
| `VISION_MAX_IMAGE_DIMENSION` | `4000` | 图片最大边长 px，超限等比缩小 |
| `VISION_AUTO_RESIZE` | `true` | 是否自动缩放图片 |

常见后端：DashScope（默认）`VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`；OpenAI `model=gpt-4o`；本地 vLLM `base_url=http://localhost:8000/v1`。

## 检查配置

```bash
python server.py --check   # 打印生效配置，API key 脱敏
```

## MCP 接入

```bash
# Claude Code
claude mcp add vision-mcp -- python /绝对/路径/vision-mcp/server.py
# Codex
codex mcp add vision-mcp -- python /绝对/路径/vision-mcp/server.py
```

> 修改 MCP 配置后需重启客户端生效。

## 工具

- **`vision_analyze`** — 通用图片理解。参数：`prompt`（可选）、`image`/`image_path`/`image_url`/`image_base64`（四选一）。
- **`vision_ocr`** — 逐字提取图片文字。参数同 analyze（无 prompt）。
- **`vision_analyze_batch`** — 批量分析多张。参数：`items`（必填，每项为四选一图片来源，可带 `prompt`）、`prompt`（可选）、`concurrency`（默认 3，范围 1-8）。

## 开源

以源码形式开源，欢迎 Fork / Issue / PR。