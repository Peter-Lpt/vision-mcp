# vision-mcp

给纯文本主模型（Claude Code / Codex 等）补齐视觉能力的轻量 MCP server。

当主模型遇到图片（截图、UI 图、流程图、报错截图、照片等）但自身无法理解时，
通过 MCP 工具把图片交给一个 **OpenAI 兼容的多模态模型**（Qwen-VL、GPT-4o、
Gemini、本地 vLLM 等）分析，再把返回的文本结果交还给主模型。

```
主模型(纯文本) ──调用 MCP 工具──▶ vision-mcp ──Chat Completions──▶ 多模态模型
   Claude/Codex ◀──────文本结果───────◀──────────────────────────   Qwen-VL / GPT-4o / ...
```

## 特性

- 单文件 `server.py`，运行时依赖仅 `mcp` 和 `Pillow`；
- 仅本地 stdio 运行，无硬编码绝对路径；
- 配置优先级：`config.json` > 进程环境变量 > `.env` 文件 > 默认值；
- 图片支持三种输入：本地路径、http(s) URL、base64 data URL（三种输入均受大小/尺寸限制约束）；
- 三个工具：`vision_analyze`（通用分析）、`vision_ocr`（逐字提取文本）、`vision_analyze_batch`（并发批量分析）；
- `vision_instructions` MCP prompt：自动教会主模型何时调用视觉工具，无需手工提示；
- 可选自动缩放超长边图片（需 Pillow），并自动修正 EXIF 旋转（含手机/相机照片）；
- 对 429 / 5xx / 网络抖动自动重试（次数与退避可配置）；
- `python server.py --check` 打印生效配置（API key 脱敏），便于排查问题；
- Windows / macOS / Linux 均可安装。

## 安装

Windows（PowerShell）：

```powershell
cd vision-mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS / Linux（bash）：

```bash
cd vision-mcp
./install.sh          # 或 bash install.sh
```

手动安装：

```bash
python -m venv .venv
# Windows: .venv\Scripts\pip install -r requirements.txt
# Linux/macOS: .venv/bin/pip install -r requirements.txt
```

## 配置

配置按以下优先级生效（高到低）：

1. `config.json`（与 `server.py` 同目录，已被 gitignore，不会入库）；
2. 进程环境变量（如 MCP 客户端 `env` 中设置的 `VISION_*`）；
3. `.env` 文件（与 `server.py` 同目录，复制 `.env.example` 改名即可）；
4. 内置默认值。

`config.json` 示例（复制 `config.example.json` 填写）：

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

环境变量 / `.env` 项：

| 环境变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `VISION_API_KEY` | 建议 | - | 视觉后端 API Key |
| `VISION_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `VISION_MODEL` | 否 | `qwen-vl-plus` | 视觉模型名 |
| `VISION_MAX_TOKENS` | 否 | `4096` | 单次最大输出 token |
| `VISION_TIMEOUT` | 否 | `120` | 请求超时（秒） |
| `VISION_MAX_RETRIES` | 否 | `2` | 瞬时故障最大重试次数 |
| `VISION_RETRY_BACKOFF` | 否 | `2` | 重试退避基数（秒），第 n 次等待 `base * 2^n` |
| `VISION_MAX_IMAGE_BYTES` | 否 | `20971520` | 单图大小上限（字节，20MB） |
| `VISION_MAX_IMAGE_DIMENSION` | 否 | `4000` | 图片最大边长 px，超过自动等比缩小 |
| `VISION_AUTO_RESIZE` | 否 | `true` | 是否自动缩放图片（需要 Pillow） |
| `MCP_SERVER_NAME` | 否 | `vision-mcp` | MCP server 名称 |
| `VISION_DOTENV_PATH` | 否 | `server.py 同目录/.env` | 自定义 .env 路径 |

常见后端示例：

- 阿里云 DashScope（Qwen-VL，默认）：`VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，`VISION_MODEL=qwen-vl-plus`
- OpenAI：`VISION_BASE_URL=https://api.openai.com/v1`，`VISION_MODEL=gpt-4o`
- Gemini（OpenAI 兼容层）：`VISION_MODEL=gemini-2.0-flash`
- 本地 vLLM：`VISION_BASE_URL=http://localhost:8000/v1`，`VISION_API_KEY=not-needed`

## 检查配置

启动 MCP 前可先打印生效配置（API key 会脱敏）：

```bash
python server.py --check
```

输出示例：

```
server_name         : vision-mcp
base_url            : https://dashscope.aliyuncs.com/compatible-mode/v1
model               : qwen-vl-plus
api_key             : sk-4d****af86
max_tokens          : 4096
timeout             : 120.0s
max_retries         : 2
...
```

## 接入 Claude Code

```bash
claude mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

或带环境变量（推荐用 shell 引用，避免密钥进命令行历史）：

```bash
claude mcp add vision-mcp -e VISION_API_KEY=sk-xxx \
  -e VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e VISION_MODEL=qwen-vl-plus \
  -- python /绝对/路径/vision-mcp/server.py
```

## 接入 Codex

```bash
codex mcp add vision-mcp -- \
  python /绝对/路径/vision-mcp/server.py
```

或写入 `~/.codex/config.toml`（Windows 示例）：

```toml
[mcp_servers.vision]
type = "stdio"
command = "C:/你的/项目路径/vision-mcp/.venv/Scripts/python.exe"
args = ["C:/你的/项目路径/vision-mcp/server.py"]
```

也可以放在项目目录的 `.codex/config.toml`，仅对该项目生效。

> 注意：修改 MCP 配置后，需要**新开一个会话 / 重启客户端**才会加载新 server。

## 项目内 .mcp.json（Claude Code 专用）

在项目根目录放 `.mcp.json`，绝对路径可用 `${workspaceFolder}` 注入：

```json
{
  "mcpServers": {
    "vision": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/vision-mcp/server.py"],
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

### vision_instructions（MCP prompt）

服务器注册了一个名为 `vision_instructions` 的 MCP prompt，内容教会主模型：

- 什么时候该调用视觉工具（看到图片路径 / URL / 截图时，而不是用 Read 猜）；
- 三个工具各适合什么场景；
- 图片来源参数（`image_path` 传本地路径、`image` 传 URL / base64）怎么选。

支持 MCP prompt 的客户端（Claude Code、Codex 等）会在会话开始时自动注入，
无需任何额外配置；不支持的客户端也不影响，工具 docstring 里同样写明了触发场景。

### vision_analyze

通用图片理解。参数：

- `prompt`（可选）：具体分析要求，例如 *"提取图中的报错信息和堆栈"*，省略时默认详细描述；
- `image` / `image_path` / `image_url` / `image_base64`（四选一）：图片来源。

### vision_ocr

纯 OCR，逐字提取可见文本，不做解释。参数同上（无 prompt）。

### vision_analyze_batch

批量分析多张图片，适合多张截图对比（如 before/after UI 测试）、一次查看多张图表、多页文档等场景。

参数：

- `items`（必填）：图片列表，每项为 `{"image_path": ...}` 或 `{"image": ...}` 等（四选一），可选的 `"prompt"` 覆盖该项的单独分析要求；
- `prompt`（可选）：未单独指定 prompt 的图片使用的默认要求；
- `concurrency`（可选，默认 3）：并发请求数，范围 1-8。

返回按输入顺序编号的结果，单张失败不影响其他图片（失败项带原因）。

## 开源

本项目以源码形式开源，欢迎 Fork / 提交 Issue / PR。

## 常见问题

**MCP server 已配置但工具不出现？**

1. 确认配置写入的位置正确（Codex：`~/.codex/config.toml` 或项目 `.codex/config.toml`）；
2. 执行 `codex mcp list` 确认 server 状态为 enabled；
3. 新开会话或重启客户端，MCP server 只在会话启动时加载；
4. 直接运行 `python server.py --check` 确认配置无误，再手动跑一次握手：

```bash
python server.py
```

如果进程能正常启动且不报错，server 本身没有问题。

**图片太大 / 报格式不支持？**

- 提高 `VISION_MAX_IMAGE_BYTES`，或先压缩图片；
- 仅支持 png / jpg / jpeg / webp / gif / bmp；带透明通道或调色板图片会自动转 RGB；
- 超过 `VISION_MAX_IMAGE_DIMENSION` 的图片会自动等比缩小（需安装 Pillow）。

**视觉 API 报错？**

- 检查 `--check` 输出的 base_url、model、api_key 是否与后端一致；
- 429 / 5xx / 网络抖动会自动重试，可在日志中看到重试提示。
