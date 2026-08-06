#!/usr/bin/env python3
"""vision-mcp - 给纯文本主模型补齐视觉能力的轻量 MCP server。

原理：主模型（Claude/Codex 等）看到图片但无法理解时，通过 MCP 工具把图片交给
一个 OpenAI 兼容的多模态模型（如 Qwen-VL、GPT-4o、Gemini、本地 vLLM 等）分析，
再把返回的文本结果交还给主模型。

- 仅本地 stdio 运行，无硬编码绝对路径；
- 配置优先读取 config.json，其次环境变量，最后默认值（见 README）；
- 图片支持本地路径 / http(s) URL / base64 data URL 三种输入方式。
"""
import base64
import io
import json
import mimetypes
import os
import urllib.request
import urllib.error
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 mcp，请先执行: pip install -r requirements.txt"
    )

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # 未安装 Pillow 时禁用自动缩放，但基础功能仍可用


# --------------------------------------------------------------------------- #
# 配置（优先读取 config.json，其次环境变量，最后默认值）
# --------------------------------------------------------------------------- #
def _load_config() -> dict:
    """从 config.json 加载配置"""
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"警告：读取 config.json 失败: {e}，将使用环境变量")
    return {}


_config = _load_config()


def _get_config(key: str, env_key: str, default: str = "") -> str:
    """优先从 config.json 获取，其次环境变量"""
    # 先尝试配置文件
    if key in _config:
        return str(_config[key]).strip()
    # 再尝试环境变量
    return os.environ.get(env_key, default).strip()


BASE_URL = _get_config("base_url", "VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
API_KEY = _get_config("api_key", "VISION_API_KEY")
MODEL = _get_config("model", "VISION_MODEL", "qwen-vl-plus")
MAX_TOKENS = int(_get_config("max_tokens", "VISION_MAX_TOKENS", "4096") or 4096)
TIMEOUT = float(_get_config("timeout", "VISION_TIMEOUT", "120") or 120)
MAX_IMAGE_BYTES = int(_get_config("max_image_bytes", "VISION_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
MAX_IMAGE_DIMENSION = int(_get_config("max_image_dimension", "VISION_MAX_IMAGE_DIMENSION", "4000"))
SERVER_NAME = _get_config("server_name", "MCP_SERVER_NAME", "vision-mcp")

# 图片自动缩放开关：未安装 Pillow 时强制关闭
AUTO_RESIZE = bool(_get_config("auto_resize", "VISION_AUTO_RESIZE", "true").lower() in {"1", "true", "yes", "on"})
if AUTO_RESIZE and Image is None:
    AUTO_RESIZE = False

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

mcp = FastMCP(SERVER_NAME)


# --------------------------------------------------------------------------- #
# 图片解析
# --------------------------------------------------------------------------- #
def _resolve_image(image: str) -> str:
    """把三种输入方式统一解析为 data: URL（. 本地路径 / http(s) / data:）。

    返回可直接塞进 OpenAI image_url 的字符串。
    """
    image = image.strip()
    if not image:
        raise ValueError("image 不能为空")

    # 已是 data URL -> 直接透传
    if image.startswith("data:"):
        return image

    # http(s) URL -> 原样透传（交给服务端拉取）
    if image.startswith(("http://", "https://")):
        return image

    # base64 裸串（无 data: 前缀）-> 尝试按 png 包装
    if "," in image and image.split(",")[0].isascii():
        prefix, payload = image.split(",", 1)
        if prefix.startswith("data:") and prefix.endswith(";base64"):
            return image

    # 其余按本地文件路径处理
    p = Path(image).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {p}")

    ext = p.suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError(f"不支持的格式 {ext or '(无扩展名)'}; 仅支持 {sorted(_ALLOWED_EXT)}")
    mime = _MIME_BY_EXT.get(ext) or mimetypes.guess_type(image)[0] or "application/octet-stream"

    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片过大: {len(raw)//1024}KB > {MAX_IMAGE_BYTES//1024}KB，请压缩后再试"
        )

    if AUTO_RESIZE:
        raw, mime = _maybe_resize(raw, mime)

    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


def _maybe_resize(raw: bytes, mime: str) -> tuple[bytes, str]:
    """图片任一边超过 MAX_IMAGE_DIMENSION 时等比缩小并转 JPEG。"""
    try:
        buf = io.BytesIO(raw)
        img = Image.open(buf)
        w, h = img.size
        if max(w, h) <= MAX_IMAGE_DIMENSION:
            return raw, mime
        ratio = MAX_IMAGE_DIMENSION / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return raw, mime  # 缩放失败则原样发送


# --------------------------------------------------------------------------- #
# 调用 OpenAI 兼容视觉后端
# --------------------------------------------------------------------------- #
def _call_vision(content: list) -> str:
    """调用 Chat Completions 接口，返回纯文本。"""
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"视觉 API 返回 {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}") from e

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"视觉 API 响应格式异常: {data}") from e

    content_text = msg.get("content")
    if isinstance(content_text, str):
        return content_text
    if isinstance(content_text, list):
        parts = [p.get("text", "") for p in content_text if isinstance(p, dict) and p.get("type") == "text"]
        if parts:
            return "".join(parts)
    # 部分供应商把推理内容放在 reasoning / reasoning_content
    for key in ("reasoning", "reasoning_content"):
        if msg.get(key):
            return str(msg[key])
    raise RuntimeError(f"视觉 API 返回空文本: {data}")


def _image_content(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url}}


# --------------------------------------------------------------------------- #
# MCP 工具
# --------------------------------------------------------------------------- #
@mcp.tool()
def vision_analyze(
    prompt: str,
    image: str = "",
    image_path: str = "",
    image_url: str = "",
    image_base64: str = "",
) -> str:
    """通用图片理解：把图片交给配置的多模态模型分析，返回文本描述。

    当主模型无法理解图片内容（截图、UI 图、流程图、报错截图、照片等）时使用。
    不要用 Read 工具读取图片文件。

    Args:
        prompt: 具体分析要求，例如 "提取图中的报错信息和堆栈"。
        image: 图片，可为本地路径、http(s) URL 或 data: 前缀的 base64 URL。
        image_path: 本地图片路径（与 image 互斥，二选一）。
        image_url: 图片 URL（与 image 互斥，二选一）。
        image_base64: 裸 base64 图片内容（与 image 互斥，二选一）。

    Returns:
        视觉模型返回的文本分析。
    """
    data_url = _pick_image(image, image_path, image_url, image_base64)
    content = [
        _image_content(data_url),
        {"type": "text", "text": prompt},
    ]
    return _call_vision(content)


@mcp.tool()
def vision_ocr(
    image: str = "",
    image_path: str = "",
    image_url: str = "",
    image_base64: str = "",
) -> str:
    """纯 OCR：从图片中逐字提取可见文本，不做解释。

    适用于日志截图、终端截图、代码截图、错误弹窗、文档截图等。

    Args:
        image: 图片，可为本地路径、http(s) URL 或 data: 前缀的 base64 URL。
        image_path: 本地图片路径（与 image 互斥，二选一）。
        image_url: 图片 URL（与 image 互斥，二选一）。
        image_base64: 裸 base64 图片内容（与 image 互斥，二选一）。

    Returns:
        保留换行缩进的原始文本。
    """
    data_url = _pick_image(image, image_path, image_url, image_base64)
    content = [
        _image_content(data_url),
        {"type": "text", "text":
            "请逐字转录图中所有可见文本，保留换行和缩进结构。"
            "只输出文字内容，不要解释，不要添加任何评注。"
            "无法识别的字符用 [?] 代替。"},
    ]
    return _call_vision(content)


def _pick_image(image: str, image_path: str, image_url: str, image_base64: str) -> str:
    """从多个可选参数中确定唯一图片来源。"""
    provided = [v for v in (image, image_path, image_url, image_base64) if v and v.strip()]
    if len(provided) > 1:
        raise ValueError("image / image_path / image_url / image_base64 只能填写一个")
    if not provided:
        raise ValueError("请提供图片：image / image_path / image_url / image_base64 任选其一")

    if image_base64:
        return f"data:image/png;base64,{image_base64}"
    if image_url:
        if image_url.startswith("data:"):
            return image_url
        return image_url
    return _resolve_image(provided[0])


if __name__ == "__main__":
    mcp.run()
