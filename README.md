# vision-mcp

为纯文本主模型补齐视觉能力的轻量 MCP server。当主模型遇到图片（截图、UI 图、流程图、报错截图等）但无法理解时，通过 MCP 工具把图片交给 OpenAI 兼容的多模态后端（Qwen-VL、GPT-4o、Gemini、本地 vLLM 等）分析，返回文本结果。

```
主模型(纯文本) ──调用 MCP 工具──▶ vision-mcp ──Chat Completions──▶ 多模态模型
   Claude/Codex ◀──────文本结果───────◀──────────────────────────   Qwen-VL / GPT-4o / ...
```

## 安装

先安装 MCP server 本体（Python）：

```bash
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
# macOS / Linux
./install.sh
```

然后按你的客户端接入：

### Claude Code

```bash
claude mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

### Codex

```bash
codex mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

> 修改 MCP 配置后需重启客户端生效。

### pi

```bash
cp pi-extensions/vision-mcp.ts ~/.pi/agent/extensions/
# 图片缩放依赖（可选，未装则自动降级为不缩放）
cd ~/.pi/agent/extensions && npm i sharp
```

复制后重启 pi 或 `/reload` 自动加载，无需 `pi install`。

## 配置

优先级：`config.json` > 进程环境变量 > `.env` > 默认值。`config.json` 已被 gitignore，不入库。

```bash
cp config.example.json config.json   # 再编辑 api_key 等字段
```

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

**pi 扩展**额外读取 `~/.pi/vision-mcp/config.json`（或用 `VISION_CONFIG_PATH` 指定），键与上述一致。

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

## 工具

- **`vision_analyze`** — 通用图片理解。参数：`prompt`（可选）、`image`/`image_path`/`image_url`/`image_base64`（四选一）。
- **`vision_ocr`** — 逐字提取图片文字。参数同 analyze（无 prompt）。
- **`vision_analyze_batch`** — 批量分析多张。参数：`items`（必填，每项为四选一图片来源，可带 `prompt`）、`prompt`（可选）、`concurrency`（默认 3，范围 1-8）。

## 开源

以源码形式开源，欢迎 Fork / Issue / PR。