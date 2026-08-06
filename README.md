# vision-mcp

给纯文本主模型（Claude Code / Codex 等）补齐视觉能力的轻量 MCP server。

当主模型遇到图片（截图、UI 图、流程图、报错截图、照片等）但自身无法理解时，
通过 MCP 工具把图片交给一个 **OpenAI 兼容的多模态模型**（Qwen-VL、GPT-4o、
Gemini、本地 vLLM 等）分析，再把返回的文本交还给主模型继续处理。

```
主模型(纯文本) ──调用 MCP 工具──▶ vision-mcp ──OpenAI Chat Completions──▶ 多模态模型
   Claude/Codex ◀──────文本结果──────┘                           Qwen-VL / GPT-4o / ...
```

## 特性

- 单文件 `server.py`，依赖仅 `mcp` 与 `Pillow`。
- 仅本地 stdio 运行，无硬编码绝对路径。
- 全部通过环境变量配置（`VISION_*`），可自定义任意 OpenAI 兼容视觉后端。
- 图片支持三种输入：本地路径、http(s) URL、base64 data URL。
- 两个工具：`vision_analyze`（通用分析）、`vision_ocr`（纯文本提取）。
- 可选自动缩放超大图片（需 Pillow）。

## 安装

```bash
cd vision-mcp
./install.sh          # 创建 .venv 并安装依赖
# 或手动：
# python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env` 填写，或在 MCP 客户端的 `env` 中直接设置。

| 环境变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `VISION_API_KEY` | 建议 | - | 视觉后端 API Key |
| `VISION_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `VISION_MODEL` | 否 | `qwen-vl-plus` | 视觉模型名 |
| `VISION_MAX_TOKENS` | 否 | `4096` | 单次最大输出 token |
| `VISION_TIMEOUT` | 否 | `120` | 请求超时（秒） |
| `VISION_MAX_IMAGE_BYTES` | 否 | `20971520` | 单图大小上限（字节） |
| `VISION_MAX_IMAGE_DIMENSION` | 否 | `4000` | 图片最大边长 px，超过自动缩小 |
| `VISION_AUTO_RESIZE` | 否 | `true` | 是否自动缩放图片 |
| `MCP_SERVER_NAME` | 否 | `vision-mcp` | MCP server 名称 |

### 常见后端示例

- 阿里云 DashScope（Qwen-VL，默认）：`VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，
  `VISION_MODEL=qwen-vl-plus`
- OpenAI：`VISION_BASE_URL=https://api.openai.com/v1`，`VISION_MODEL=gpt-4o`
- Gemini（OpenAI 兼容层）：`VISION_MODEL=gemini-2.0-flash`
- 本地 vLLM：`VISION_BASE_URL=http://localhost:8000/v1`，`VISION_API_KEY=not-needed`

## 接入 Claude Code

```bash
claude mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

带上环境变量（推荐用 shell 引用，避免密钥进命令行历史）：

```bash
claude mcp add vision-mcp -e VISION_API_KEY=sk-xxx \
  -e VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e VISION_MODEL=qwen-vl-plus \
  -- python /绝对/路径/vision-mcp/server.py
```

> 注意：`claude mcp add` 需要绝对路径，但这属于客户端配置而非代码里的硬编码。
> 若希望配置绝对路径也随项目走，可改用项目内 `.mcp.json`（见下）。

## 接入 Codex

```bash
codex mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

或写入 `~/.codex/config.toml`：

```toml
[mcp_servers.vision]
command = "python"
args = ["/绝对/路径/vision-mcp/server.py"]
env = { VISION_API_KEY = "sk-xxx", VISION_MODEL = "qwen-vl-plus" }
```

## 项目内 .mcp.json（Claude Code 专用，随项目走）

在项目根目录放 `.mcp.json`，绝对路径用 `${workspaceFolder}` 或环境变量注入：

```json
{
  "mcpServers": {
    "vision": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/self/ai-work/mcp/vision-mcp/server.py"],
      "env": {
        "VISION_API_KEY": "sk-xxx",
        "VISION_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "VISION_MODEL": "qwen-vl-plus"
      }
    }
  }
}
```

## 工具

### vision_analyze

通用图片理解。参数（`image` / `image_path` / `image_url` / `image_base64` 四选一）：

- `prompt`：具体分析要求，如 *"提取图中的报错信息和堆栈"*。
- `image` / `image_path` / `image_url` / `image_base64`：图片来源。

### vision_ocr

纯 OCR，逐字提取可见文本，保留换行和缩进，不做解释。

建议在主模型提示词中加入规则：「遇到图片时不要用 Read 工具读取，应调用
`vision_analyze` 或 `vision_ocr` 工具，并在 prompt 中说明具体要从图中获取什么。」

## 验证

```bash
cd vision-mcp
.venv/bin/python -c "import server; print('OK', server.SERVER_NAME)"
```

## License

MIT