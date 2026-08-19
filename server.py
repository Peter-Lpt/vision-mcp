#!/usr/bin/env python3
"""vision-mcp - 给纯文本主模型补齐视觉能力的轻量 MCP server。

原理：主模型（Claude Code / Codex 等）看到图片但无法理解时，通过 MCP 工具把图片交给
一个 OpenAI 兼容的多模态模型（如 Qwen-VL、GPT-4o、Gemini、本地 vLLM 等）分析，
再把返回的文本结果交还给主模型。

特性：
- 仅本地 stdio 运行，无硬编码绝对路径；
- 配置优先级：config.json > 进程环境变量 > .env 文件 > 默认值（见 README）；
- 图片支持本地路径 / http(s) URL / base64 data URL 三种输入方式；
- 对 429 / 5xx / 网络抖动自动重试（次数与退避可配置）；
- `vision_instructions` MCP prompt 自动教会主模型何时调用视觉工具；
- `vision_analyze_batch` 支持并发批量分析多张图片；
- `python server.py --check` 可打印生效配置，便于排查接入问题。
"""

import base64
import hashlib
import http.client
import importlib.metadata
import io
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "0.1.0"

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise SystemExit("缺少依赖 mcp，请先执行: pip install -r requirements.txt") from None
    try:
        installed = importlib.metadata.version("mcp")
    except Exception:
        installed = "未知"
    raise SystemExit(
        "vision-mcp 依赖 mcp SDK 1.x 的 FastMCP 接口，但当前环境不兼容。\n"
        f"检测到已安装 mcp {installed}，请执行: pip install 'mcp>=1.0,<2'"
    ) from None

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None


# --------------------------------------------------------------------------- #
# 配置：优先级 config.json > 进程环境变量 > .env 文件 > 默认值
# --------------------------------------------------------------------------- #
CONFIG_PATH = Path(__file__).parent / "config.json"
DOTENV_PATH = Path(
    os.environ.get("VISION_DOTENV_PATH") or (Path(__file__).parent / ".env")
).expanduser()


def _log_warning(message: str) -> None:
    """警告统一输出到 stderr：stdio 模式下 stdout 是 MCP 协议通道。"""
    print(f"警告：{message}", file=sys.stderr)


def _load_config() -> dict:
    """从 config.json 加载配置。"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _log_warning(f"读取 config.json 失败: {e}，将使用环境变量")
    return {}


def _load_dotenv() -> dict:
    """极简 .env 解析器（避免引入额外依赖），支持注释与引号包裹的值。"""
    values: dict[str, str] = {}
    if not DOTENV_PATH.exists():
        return values
    try:
        for raw_line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                values[key] = value
    except Exception as e:
        _log_warning(f"读取 .env 失败: {e}")
    return values


_config = _load_config()
_dotenv = _load_dotenv()


def _get_config(key: str, env_key: str, default: str = "") -> str:
    """按 config.json > 进程环境变量 > .env > 默认值的优先级取配置。"""
    if key in _config and _config[key] not in (None, ""):
        return str(_config[key]).strip()
    if env_key in os.environ:
        return os.environ[env_key].strip()
    if env_key in _dotenv:
        return _dotenv[env_key].strip()
    return default.strip()


def _as_int(key: str, env_key: str, default: int) -> int:
    raw = _get_config(key, env_key, str(default))
    try:
        return int(raw)
    except ValueError:
        _log_warning(f"配置 {env_key} 不是合法整数（{raw!r}），使用默认值 {default}")
        return default


def _as_float(key: str, env_key: str, default: float) -> float:
    raw = _get_config(key, env_key, str(default))
    try:
        return float(raw)
    except ValueError:
        _log_warning(f"配置 {env_key} 不是合法数字（{raw!r}），使用默认值 {default}")
        return default


def _as_bool(key: str, env_key: str, default: bool) -> bool:
    raw = _get_config(key, env_key, str(default)).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    _log_warning(f"配置 {env_key} 不是合法布尔值（{raw!r}），使用默认值 {default}")
    return default


BASE_URL = _get_config(
    "base_url", "VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
API_KEY = _get_config("api_key", "VISION_API_KEY")
MODEL = _get_config("model", "VISION_MODEL", "qwen-vl-plus")
# 后端 API 协议：openai-completions（OpenAI Chat Completions，默认）/ openai-responses（OpenAI Responses）/ anthropic-messages（Anthropic Messages）
API = _get_config("api", "VISION_API", "openai-completions").strip().lower()
if API not in ("openai-completions", "openai-responses", "anthropic-messages"):
    _log_warning(f"配置 api={API!r} 不受支持，使用默认值 openai-completions")
    API = "openai-completions"
MAX_TOKENS = _as_int("max_tokens", "VISION_MAX_TOKENS", 4096)
TIMEOUT = _as_float("timeout", "VISION_TIMEOUT", 120.0)
MAX_RETRIES = _as_int("max_retries", "VISION_MAX_RETRIES", 2)
RETRY_BACKOFF = _as_float("retry_backoff", "VISION_RETRY_BACKOFF", 2.0)
CACHE_MAX_ENTRIES = _as_int("cache_max_entries", "VISION_CACHE_MAX_ENTRIES", 256)
CACHE_ENABLED = _as_bool("cache_enabled", "VISION_CACHE_ENABLED", True)
MAX_IMAGE_BYTES = _as_int(
    "max_image_bytes", "VISION_MAX_IMAGE_BYTES", 20 * 1024 * 1024
)
MAX_IMAGE_DIMENSION = _as_int(
    "max_image_dimension", "VISION_MAX_IMAGE_DIMENSION", 4000
)
SERVER_NAME = _get_config("server_name", "MCP_SERVER_NAME", "vision-mcp")

# 图片自动缩放开关：未安装 Pillow 时强制关闭
AUTO_RESIZE = _as_bool("auto_resize", "VISION_AUTO_RESIZE", True)
if AUTO_RESIZE and Image is None:
    _log_warning("未安装 Pillow，图片自动缩放已禁用；建议执行 pip install -r requirements.txt")
    AUTO_RESIZE = False

# 仅保留主流视觉后端(OpenAI/Anthropic)原生支持的格式；BMP 后端普遍不支持，故排除
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

mcp = FastMCP(SERVER_NAME)


class VisionError(Exception):
    """带结构化错误码的异常，便于客户端按 code 编程处理（如重试/提示用户）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# --------------------------------------------------------------------------- #
# 内容寻址缓存：相同图片 + 相同提示 + 相同模型在窗口内复用，避免重复调用视觉后端
# --------------------------------------------------------------------------- #
_cache: OrderedDict[str, str] = OrderedDict()


def _cache_key(image_value: str, prompt: str) -> str:
    """内容寻址缓存 key：图片 data URL + 提示 + 模型 + API 协议。

    图片 data URL 由原始字节 + 压缩参数确定，同一来源在相配配置下具有确定性，
    因此可作为内容指纹（无需再散列原始字节）。
    """
    raw = f"{MODEL}\x00{API}\x00{image_value}\x00{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    if not CACHE_ENABLED:
        return None
    val = _cache.get(key)
    if val is not None:
        _cache.move_to_end(key)
        return val
    return None


def _cache_set(key: str, value: str) -> None:
    if not CACHE_ENABLED:
        return
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


# --------------------------------------------------------------------------- #
# 图片解析
# --------------------------------------------------------------------------- #
def _resolve_image(image: str) -> str:
    """把本地路径 / http(s) URL / data URL 统一解析为可放入 OpenAI image_url 的字符串。"""
    image = image.strip()
    if not image:
        raise VisionError("empty_image", "image 不能为空")

    # 已是 data URL -> 直接透传
    if image.startswith("data:"):
        return image

    # http(s) URL -> 原样透传（交给视觉后端拉取）
    if image.startswith(("http://", "https://")):
        return image

    # 其余按本地文件路径处理
    p = Path(image).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.exists():
        raise VisionError("not_found", f"图片不存在: {p}")

    ext = p.suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise VisionError(
            "unsupported_format",
            f"不支持的格式 {ext or '(无扩展名)'}; 仅支持 {sorted(_ALLOWED_EXT)}",
        )
    mime = _MIME_BY_EXT.get(ext) or mimetypes.guess_type(image)[0] or "application/octet-stream"

    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise VisionError(
            "too_large",
            f"图片过大: {len(raw) // 1024}KB > {MAX_IMAGE_BYTES // 1024}KB，请压缩后再试",
        )

    if AUTO_RESIZE:
        raw, mime = _maybe_resize(raw, mime)

    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


def _maybe_resize(raw: bytes, mime: str) -> tuple[bytes, str]:
    """超长边图片等比缩小并转 JPEG；存在 EXIF 旋转时修正方向。失败则原样返回。"""
    if Image is None:
        return raw, mime
    try:
        buf = io.BytesIO(raw)
        img = Image.open(buf)
        exif = img.getexif()
        orientation = exif.get(274, 1)  # EXIF Orientation tag
        needs_transpose = orientation not in (1, None)
        if needs_transpose and ImageOps is not None:
            img = ImageOps.exif_transpose(img)
            # 像素已修正，删除 orientation tag 防止下游二次旋转
            exif = img.getexif()
            if 274 in exif:
                del exif[274]
        w, h = img.size
        if max(w, h) <= MAX_IMAGE_DIMENSION and not needs_transpose:
            return raw, mime
        if max(w, h) > MAX_IMAGE_DIMENSION:
            ratio = MAX_IMAGE_DIMENSION / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        # 统一转 RGB 后存 JPEG，兼容 RGBA / 调色板 / LA 等模式
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        # exif 必须是 Exif 对象而非 None（空对象也算），否则 Pillow 报错
        img.save(out, format="JPEG", quality=85, exif=exif)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return raw, mime  # 处理失败则原样发送


def _sniff_image_mime(raw: bytes) -> str:
    """用 Pillow 探测裸 base64 图片的真实格式；探测失败时回退 PNG。"""
    if Image is not None:
        try:
            img = Image.open(io.BytesIO(raw))
            return _FORMAT_TO_MIME.get(img.format or "", "image/png")
        except Exception:
            pass
    return "image/png"


# --------------------------------------------------------------------------- #
# 调用 OpenAI 兼容视觉后端
# --------------------------------------------------------------------------- #
def _retry_delay(attempt: int) -> float:
    return RETRY_BACKOFF * (2 ** attempt)


def _log_retry(reason: str, attempt: int) -> None:
    _log_warning(f"{reason}，{_retry_delay(attempt):.1f}s 后重试（第 {attempt + 1}/{MAX_RETRIES} 次）")


def _call_vision(content: list) -> str:
    """按 api 协议调用视觉后端，返回纯文本；对瞬时故障自动重试。"""
    if API == "anthropic-messages":
        url = f"{BASE_URL}/v1/messages"
        payload = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        # 无鉴权后端留空 key 时不发送鉴权头
        if API_KEY:
            headers["x-api-key"] = API_KEY
    elif API == "openai-responses":
        url = f"{BASE_URL}/responses"
        payload = {
            "model": MODEL,
            "max_output_tokens": MAX_TOKENS,
            "input": [{"role": "user", "content": content}],
        }
        headers = {"Content-Type": "application/json"}
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"
    else:  # openai-completions（默认）
        url = f"{BASE_URL}/chat/completions"
        payload = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"Content-Type": "application/json"}
        if API_KEY:
            headers["Authorization"] = f"Bearer {API_KEY}"

    data = None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"视觉 API 返回非 JSON 响应（前 300 字符）: {raw_body[:300]}"
                ) from e
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (408, 429) or e.code >= 500
            if retryable and attempt < MAX_RETRIES:
                _log_retry(f"HTTP {e.code}", attempt)
                time.sleep(_retry_delay(attempt))
                continue
            raise RuntimeError(f"视觉 API 返回 {e.code}: {body}") from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            if attempt < MAX_RETRIES:
                reason = getattr(e, "reason", e)
                _log_retry(f"网络错误: {reason}", attempt)
                time.sleep(_retry_delay(attempt))
                continue
            reason = getattr(e, "reason", e)
            raise RuntimeError(f"网络错误: {reason}") from e

    if API == "openai-responses":
        parts = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for block in item.get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "output_text"
                        and block.get("text")
                    ):
                        parts.append(block["text"])
        if parts:
            return "".join(parts)
        raise RuntimeError(f"视觉 API 返回空文本: {data}")

    if API == "anthropic-messages":
        blocks = data.get("content") or []
        texts = [
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        if texts:
            return "".join(texts)
        raise RuntimeError(f"视觉 API 返回空文本: {data}")

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
    """把 data URL 转成当前 api 协议要求的图片 content 块。"""
    if API == "openai-responses":
        return {"type": "input_image", "image_url": data_url}
    if API == "anthropic-messages":
        header, _, payload = data_url.partition(",")
        mime = header.split(";")[0].split(":", 1)[-1] or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": payload},
        }
    return {"type": "image_url", "image_url": {"url": data_url}}


# --------------------------------------------------------------------------- #
# MCP Prompt：教主导航模型何时使用视觉工具
# --------------------------------------------------------------------------- #
@mcp.prompt(
    name="vision_instructions",
    title="图片处理指引",
    description="教主导航模型识别图片场景并正确调用 vision-mcp 的视觉工具。",
)
def vision_instructions() -> str:
    """当对话中遇到以下情况，直接调用对应的视觉工具，不要猜测图片内容：

1. 用户粘贴了图片、截图路径、图片 URL，或要求"查看/分析/识别/描述这张图"：
   调用 vision_analyze（通用理解）或 vision_ocr（只提取文字）。

2. 图片是日志、终端、代码、报错弹窗或文档截图，需要逐字读取文字：
   调用 vision_ocr。

3. 需要同时分析多张图片（如多张截图对比、多页文档）：
   调用 vision_analyze_batch，把每张图放进 items 列表。

工具参数约定：
- 本地文件路径用 image_path 参数；
- http(s) URL 或 data: base64 用 image 参数；
- image / image_path / image_url / image_base64 四选一，不要同时传多个。

重要：
- 不要用 Read 工具读取图片文件——你无法理解像素，应把图片交给上面的视觉工具。
- 视觉工具返回的是文字，拿到后直接基于它回答用户即可。
"""


# --------------------------------------------------------------------------- #
# MCP 工具
# --------------------------------------------------------------------------- #
@mcp.tool()
def vision_analyze(
    prompt: str = "请详细描述这张图片的全部内容。",
    image: str = "",
    image_path: str = "",
    image_url: str = "",
    image_base64: str = "",
) -> str:
    """通用图片理解：把图片交给配置的多模态模型分析，返回文本描述。

    当主模型无法理解图片内容（截图、UI 图、流程图、报错截图、照片等）时使用。
    当对话中出现图片路径、图片 URL、截图文件，或用户要求查看/分析/识别图片时，优先调用本工具。
    不要用 Read 工具读取图片文件。

    Args:
        prompt: 具体分析要求，例如 "提取图中的报错信息和堆栈"（可省略）。
        image: 图片，可为本地路径、http(s) URL 或 data: 前缀的 base64 URL。
        image_path: 本地图片路径（与 image 互斥，二选一）。
        image_url: 图片 URL（与 image 互斥，二选一）。
        image_base64: 裸 base64 图片内容（与 image 互斥，二选一）。

    Returns:
        视觉模型返回的文本分析。
    """
    data_url = _pick_image(image, image_path, image_url, image_base64)
    key = _cache_key(data_url, prompt)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    content = [
        _image_content(data_url),
        {"type": "text", "text": prompt},
    ]
    text = _call_vision(content)
    _cache_set(key, text)
    return text


@mcp.tool()
def vision_ocr(
    image: str = "",
    image_path: str = "",
    image_url: str = "",
    image_base64: str = "",
) -> str:
    """纯 OCR：从图片中逐字提取可见文本，不做解释。

    适用于日志截图、终端截图、代码截图、错误弹窗、文档截图等。
    当对话中出现图片路径、图片 URL 或用户要求提取图片文字时，优先调用本工具。

    Args:
        image: 图片，可为本地路径、http(s) URL 或 data: 前缀的 base64 URL。
        image_path: 本地图片路径（与 image 互斥，二选一）。
        image_url: 图片 URL（与 image 互斥，二选一）。
        image_base64: 裸 base64 图片内容（与 image 互斥，二选一）。

    Returns:
        保留换行缩进的原始文本。
    """
    data_url = _pick_image(image, image_path, image_url, image_base64)
    prompt = (
        "请逐字转录图中所有可见文本，保留换行和缩进结构。"
        "只输出文字内容，不要解释，不要添加任何评注。"
        "无法识别的字符用 [?] 代替。"
    )
    key = _cache_key(data_url, prompt)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    content = [_image_content(data_url), {"type": "text", "text": prompt}]
    text = _call_vision(content)
    _cache_set(key, text)
    return text


@mcp.tool()
def vision_analyze_batch(
    items: list[dict],
    prompt: str = "请详细描述这张图片的全部内容。",
    concurrency: int = 3,
) -> str:
    """批量分析多张图片：一次调用处理多张，单张失败不影响其他。

    适用于对比多张截图（如 before/after UI 测试）、一次查看多张图表、多页文档等场景。

    Args:
        items: 图片列表，每项为 {"image"/"image_path"/"image_url"/"image_base64": 图片}，
            可选的 "prompt" 覆盖该项的单独分析要求。
        prompt: 未单独指定 prompt 的图片使用的默认分析要求。
        concurrency: 并发请求数（1-8，默认 3）。

    Returns:
        按输入顺序编号的每张图片分析结果，失败项带错误原因。
    """
    if not items:
        raise VisionError("empty_items", "items 不能为空")
    if len(items) > 50:
        raise VisionError("too_many_items", "一次最多分析 50 张图片")
    concurrency = max(1, min(8, int(concurrency)))

    def _analyze_one(idx: int, item: dict) -> str:
        try:
            data_url = _pick_image(
                str(item.get("image") or ""),
                str(item.get("image_path") or ""),
                str(item.get("image_url") or ""),
                str(item.get("image_base64") or ""),
            )
            item_prompt = str(item.get("prompt") or "").strip() or prompt
            key = _cache_key(data_url, item_prompt)
            cached = _cache_get(key)
            if cached is not None:
                return f"[{idx}] 成功\n{cached}"
            text = _call_vision(
                [_image_content(data_url), {"type": "text", "text": item_prompt}]
            )
            _cache_set(key, text)
            return f"[{idx}] 成功\n{text}"
        except Exception as e:
            return f"[{idx}] 失败: {e}"

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(_analyze_one, range(len(items)), items))
    return "\n\n".join(results)


def _pick_image(image: str, image_path: str, image_url: str, image_base64: str) -> str:
    """从多个可选参数中确定唯一图片来源。"""
    provided = [v for v in (image, image_path, image_url, image_base64) if v and v.strip()]
    if len(provided) > 1:
        raise VisionError("multiple_sources", "image / image_path / image_url / image_base64 只能填写一个")
    if not provided:
        raise VisionError("no_source", "请提供图片：image / image_path / image_url / image_base64 任选其一")

    if image_base64:
        value = image_base64.strip()
        header, payload = "", value
        if value.startswith("data:"):
            header, _, payload = value.partition(",")
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception as e:
            raise VisionError("invalid_base64", "image_base64 不是合法的 base64 内容") from e
        if len(raw) > MAX_IMAGE_BYTES:
            raise VisionError(
                "too_large",
                f"图片过大: {len(raw) // 1024}KB > {MAX_IMAGE_BYTES // 1024}KB，请压缩后再试",
            )
        mime = header.split(";")[0].split(":", 1)[-1] or _sniff_image_mime(raw)
        if AUTO_RESIZE:
            raw, mime = _maybe_resize(raw, mime)
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    if image_url:
        value = image_url.strip()
        if value.startswith(("http://", "https://", "data:")):
            return value
        raise VisionError("invalid_url", "image_url 必须以 http://、https:// 或 data: 开头")
    return _resolve_image(provided[0])


# --------------------------------------------------------------------------- #
# 诊断与入口
# --------------------------------------------------------------------------- #
def _print_effective_config() -> None:
    """打印生效配置（API key 脱敏），用于排查接入问题。"""
    def mask(key: str) -> str:
        if not key:
            return "(未设置)"
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}****{key[-4:]}"

    print(f"server_name         : {SERVER_NAME}")
    print(f"api                 : {API}")
    print(f"base_url            : {BASE_URL}")
    print(f"model               : {MODEL}")
    print(f"api_key             : {mask(API_KEY)}")
    print(f"max_tokens          : {MAX_TOKENS}")
    print(f"timeout             : {TIMEOUT}s")
    print(f"max_retries         : {MAX_RETRIES}")
    print(f"retry_backoff       : {RETRY_BACKOFF}s")
    print(f"max_image_bytes     : {MAX_IMAGE_BYTES} ({MAX_IMAGE_BYTES // 1024}KB)")
    print(f"max_image_dimension : {MAX_IMAGE_DIMENSION}px")
    print(f"auto_resize         : {AUTO_RESIZE}")
    print(f"cache_enabled       : {CACHE_ENABLED} (max {CACHE_MAX_ENTRIES} entries)")
    print(f"pillow_available    : {Image is not None}")
    print(f"config_file         : {CONFIG_PATH}")
    print(f"dotenv_file         : {DOTENV_PATH}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _print_effective_config()
    else:
        mcp.run()
