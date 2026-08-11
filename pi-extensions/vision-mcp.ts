/**
 * vision-mcp (pi extension)
 *
 * 给纯文本主模型补齐视觉能力的 pi 扩展。由 vision-mcp 的 server.py 增量改造而来。
 *
 * 原理：pi 主模型（可能不支持图片）遇到图片（截图、UI 图、流程图、报错截图、照片等）
 * 时，通过自定义工具把图片交给一个 OpenAI 兼容的多模态模型（Qwen-VL、GPT-4o、Gemini、
 * 本地 vLLM 等）分析，再把返回的文本结果交还给主模型。
 *
 *   pi 主模型(纯文本) ──调用工具──▶ 本扩展 ──Chat Completions──▶ 多模态模型
 *   Claude/DeepSeek ◀──────文本结果─────◀──────────────────────   Qwen-VL / GPT-4o / ...
 *
 * 与 MCP 版相比，pi 原生额外提供：
 * - 当附加了真实图片且当前主模型不支持图片时，自动转录图片为文本并注入对话
 *   （这是 MCP 版做不到的，pi 能在发往模型前看到附加图片）。
 *
 * 配置优先级：config 文件 > 进程环境变量 > 默认值。
 * config 文件路径：$VISION_CONFIG_PATH 或 ~/.pi/vision-mcp/config.json（键与 MCP 版 config.json 一致）。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

// --------------------------------------------------------------------------- #
// 配置
// --------------------------------------------------------------------------- #
interface VisionConfig {
  api: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  maxTokens: number;
  timeout: number;
  maxRetries: number;
  retryBackoff: number;
  maxImageBytes: number;
  maxImageDimension: number;
  autoResize: boolean;
  autoTranscribe: boolean;
  cacheEnabled: boolean;
  cacheMaxEntries: number;
}

const DEFAULTS = {
  api: "openai-completions",
  baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  apiKey: "",
  model: "qwen-vl-plus",
  maxTokens: 4096,
  timeout: 120,
  maxRetries: 2,
  retryBackoff: 2,
  maxImageBytes: 20 * 1024 * 1024,
  maxImageDimension: 4000,
  autoResize: true,
  autoTranscribe: true,
  cacheEnabled: true,
  cacheMaxEntries: 256,
};

const CONFIG_PATH = process.env.VISION_CONFIG_PATH
  ? resolvePath(process.env.VISION_CONFIG_PATH)
  : join(homedir(), ".pi", "vision-mcp", "config.json");

function loadConfig(): VisionConfig {
  const cfg: Record<string, unknown> = { ...DEFAULTS };
  if (existsSync(CONFIG_PATH)) {
    try {
      const file = JSON.parse(readFileSync(CONFIG_PATH, "utf-8"));
      Object.assign(cfg, file);
    } catch (e) {
      console.error(`vision-mcp: 读取 config 失败(${CONFIG_PATH}): ${(e as Error).message}`);
    }
  }
  const env = (k: string) => process.env[k];
  const num = (fileKey: string, envKey: string, def: number) => {
    const fileVal = cfg[fileKey];
    if (typeof fileVal === "number" && !Number.isNaN(fileVal)) return fileVal;
    const raw = env(envKey);
    if (raw && raw.trim() !== "" && !Number.isNaN(Number(raw))) return Number(raw);
    return def;
  };
  const bool = (fileKey: string, envKey: string, def: boolean) => {
    const fileVal = cfg[fileKey];
    if (typeof fileVal === "boolean") return fileVal;
    const raw = (env(envKey) ?? "").toLowerCase();
    if (["1", "true", "yes", "on"].includes(raw)) return true;
    if (["0", "false", "no", "off"].includes(raw)) return false;
    return def;
  };
  const str = (fileKey: string, envKey: string, def: string) => {
    const fileVal = cfg[fileKey];
    if (typeof fileVal === "string" && fileVal.trim() !== "") return fileVal.trim();
    const raw = env(envKey);
    if (raw && raw.trim() !== "") return raw.trim();
    return def;
  };

  const api = str("api", "VISION_API", DEFAULTS.api).toLowerCase();
  if (!["openai-completions", "openai-responses", "anthropic-messages"].includes(api)) {
    console.error(`vision-mcp: 配置 api=${api} 不受支持，使用默认值 openai-completions`);
  }

  return {
    api: ["openai-responses", "anthropic-messages"].includes(api) ? api : "openai-completions",
    baseUrl: str("base_url", "VISION_BASE_URL", DEFAULTS.baseUrl).replace(/\/+$/, ""),
    apiKey: str("api_key", "VISION_API_KEY", DEFAULTS.apiKey),
    model: str("model", "VISION_MODEL", DEFAULTS.model),
    maxTokens: num("max_tokens", "VISION_MAX_TOKENS", DEFAULTS.maxTokens),
    timeout: num("timeout", "VISION_TIMEOUT", DEFAULTS.timeout),
    maxRetries: num("max_retries", "VISION_MAX_RETRIES", DEFAULTS.maxRetries),
    retryBackoff: num("retry_backoff", "VISION_RETRY_BACKOFF", DEFAULTS.retryBackoff),
    maxImageBytes: num("max_image_bytes", "VISION_MAX_IMAGE_BYTES", DEFAULTS.maxImageBytes),
    maxImageDimension: num(
      "max_image_dimension",
      "VISION_MAX_IMAGE_DIMENSION",
      DEFAULTS.maxImageDimension,
    ),
    autoResize: bool("auto_resize", "VISION_AUTO_RESIZE", DEFAULTS.autoResize),
    autoTranscribe: bool("auto_transcribe", "VISION_AUTO_TRANSCRIBE", DEFAULTS.autoTranscribe),
    cacheEnabled: bool("cache_enabled", "VISION_CACHE_ENABLED", DEFAULTS.cacheEnabled),
    cacheMaxEntries: num("cache_max_entries", "VISION_CACHE_MAX_ENTRIES", DEFAULTS.cacheMaxEntries),
  };
}

const config = loadConfig();

/** 带结构化错误码的异常，便于上层按 code 编程处理。 */
export class VisionError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "VisionError";
    this.code = code;
  }
}

// --------------------------------------------------------------------------- #
// 内容寻址缓存：相同图片(data URL) + 相同提示 + 相同模型在窗口内复用
// --------------------------------------------------------------------------- #
const _cache = new Map<string, string>();

function cacheKey(imageValue: string, prompt: string): string {
  return `${config.api}\u0000${config.model}\u0000${imageValue}\u0000${prompt}`;
}

function cacheGet(key: string): string | undefined {
  if (!config.cacheEnabled) return undefined;
  const val = _cache.get(key);
  if (val !== undefined) {
    _cache.delete(key);
    _cache.set(key, val); // 置为最近使用
  }
  return val;
}

function cacheSet(key: string, value: string): void {
  if (!config.cacheEnabled) return;
  _cache.set(key, value);
  while (_cache.size > config.cacheMaxEntries) {
    const oldest = _cache.keys().next().value;
    if (oldest === undefined) break;
    _cache.delete(oldest);
  }
}

// --------------------------------------------------------------------------- #
// 图片解析
// --------------------------------------------------------------------------- #
// 仅保留主流视觉后端(OpenAI/Anthropic)原生支持的格式；BMP 后端普遍不支持，故排除
const ALLOWED_EXT = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
const MIME_BY_EXT: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
};

function imageContent(dataUrl: string): Record<string, unknown> {
  if (config.api === "openai-responses") {
    return { type: "input_image", image_url: dataUrl };
  }
  if (config.api === "anthropic-messages") {
    const comma = dataUrl.indexOf(",");
    const header = dataUrl.slice(0, comma);
    const payload = dataUrl.slice(comma + 1);
    const mime = header.split(";")[0].split(":")[1] || "image/png";
    return {
      type: "image",
      source: { type: "base64", media_type: mime, data: payload },
    };
  }
  return { type: "image_url", image_url: { url: dataUrl } };
}

/** 超长边图片等比缩小并转 JPEG；修正 EXIF 旋转。用 sharp（可选），缺失则原样返回。 */
async function maybeResize(
  raw: Uint8Array,
  mime: string,
): Promise<{ raw: Uint8Array; mime: string }> {
  if (!config.autoResize) return { raw, mime };
  try {
    // sharp 为可选依赖：未安装或加载失败时优雅降级为原样发送
    const sharp = (await import("sharp")).default;
    const meta = await sharp(raw).metadata();
    const w = meta.width ?? 0;
    const h = meta.height ?? 0;
    const orientation = meta.orientation ?? 1;
    const needsTranspose = orientation > 1;
    if (Math.max(w, h) <= config.maxImageDimension && !needsTranspose) {
      return { raw, mime };
    }
    let img = sharp(raw).rotate(); // 按 EXIF 自动旋转
    if (Math.max(w, h) > config.maxImageDimension) {
      const ratio = config.maxImageDimension / Math.max(w, h);
      img = img.resize(Math.max(1, Math.round(w * ratio)), Math.max(1, Math.round(h * ratio)));
    }
    const out = await img.jpeg({ quality: 85 }).toBuffer();
    return { raw: out, mime: "image/jpeg" };
  } catch {
    return { raw, mime };
  }
}

function mimeFromExt(ext: string): string {
  return MIME_BY_EXT[ext.toLowerCase()] || "application/octet-stream";
}

/** 把本地路径 / http(s) URL / data URL 统一解析为可放入 image_url 的字符串。 */
async function resolveImage(
  image: string,
  imagePath: string,
  imageUrl: string,
  imageBase64: string,
  cwd: string,
  signal?: AbortSignal,
): Promise<string> {
  const provided = [image, imagePath, imageUrl, imageBase64].filter(
    (v) => v && v.trim(),
  );
  if (provided.length > 1) {
    throw new VisionError("multiple_sources", "image / image_path / image_url / image_base64 只能填写一个");
  }
  if (provided.length === 0) {
    throw new VisionError("no_source", "请提供图片：image / image_path / image_url / image_base64 任选其一");
  }

  if (imageBase64) {
    const value = imageBase64.trim();
    let header = "";
    let payload = value;
    if (value.startsWith("data:")) {
      const comma = value.indexOf(",");
      header = value.slice(0, comma);
      payload = value.slice(comma + 1);
    }
    let raw: Buffer;
    try {
      raw = Buffer.from(payload, "base64");
      if (raw.length === 0) throw new Error("empty");
    } catch {
      throw new VisionError("invalid_base64", "image_base64 不是合法的 base64 内容");
    }
    if (raw.length > config.maxImageBytes) {
      throw new VisionError(
        "too_large",
        `图片过大: ${Math.floor(raw.length / 1024)}KB > ${Math.floor(config.maxImageBytes / 1024)}KB，请压缩后再试`,
      );
    }
    const mime =
      header.split(";")[0].split(":")[1] || sniffImageMime(raw);
    const resized = await maybeResize(raw, mime);
    return `data:${resized.mime};base64,${Buffer.from(resized.raw).toString("base64")}`;
  }

  if (imageUrl) {
    const url = imageUrl.trim();
    if (!/^(https?:\/\/|data:)/.test(url)) {
      throw new VisionError("invalid_url", "image_url 必须以 http://、https:// 或 data: 开头");
    }
    return url;
  }

  // image 参数可能是 http(s) URL 或 data: URL，直接透传
  if (image && /^(https?:\/\/|data:)/.test(image.trim())) {
    return image.trim();
  }

  // 本地文件路径（image 或 image_path）
  let p = (image || imagePath).trim();
  if (!p.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(p)) {
    p = resolvePath(cwd, p);
  }
  if (!existsSync(p)) {
    throw new VisionError("not_found", `图片不存在: ${p}`);
  }
  const ext = p.slice(p.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_EXT.includes(ext)) {
    throw new VisionError("unsupported_format", `不支持的格式 ${ext || "(无扩展名)"}; 仅支持 ${ALLOWED_EXT.join(", ")}`);
  }
  const raw = readFileSync(p);
  if (raw.length > config.maxImageBytes) {
    throw new VisionError(
      "too_large",
      `图片过大: ${Math.floor(raw.length / 1024)}KB > ${Math.floor(config.maxImageBytes / 1024)}KB，请压缩后再试`,
    );
  }
  const resized = await maybeResize(raw, mimeFromExt(ext));
  return `data:${resized.mime};base64,${Buffer.from(resized.raw).toString("base64")}`;
}

function sniffImageMime(raw: Uint8Array): string {
  // 简易魔数探测
  const head = Buffer.from(raw.slice(0, 12));
  if (raw.length >= 8 && head.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return "image/png";
  }
  if (raw.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) {
    return "image/jpeg";
  }
  if (raw.length >= 12 && head.toString("ascii", 0, 4) === "RIFF" && head.toString("ascii", 8, 12) === "WEBP") {
    return "image/webp";
  }
  if (raw.length >= 6 && ["GIF87a", "GIF89a"].includes(head.toString("ascii", 0, 6))) {
    return "image/gif";
  }
  return "image/png";
}

// --------------------------------------------------------------------------- #
// 调用 OpenAI 兼容视觉后端
// --------------------------------------------------------------------------- #
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function callVision(content: unknown[], signal?: AbortSignal): Promise<string> {
  const anthropic = config.api === "anthropic-messages";
  const responses = config.api === "openai-responses";
  const payload = responses
    ? {
        model: config.model,
        max_output_tokens: config.maxTokens,
        input: [{ role: "user", content }],
      }
    : {
        model: config.model,
        max_tokens: config.maxTokens,
        messages: [{ role: "user", content }],
      };
  const url = anthropic
    ? `${config.baseUrl}/v1/messages`
    : responses
      ? `${config.baseUrl}/responses`
      : `${config.baseUrl}/chat/completions`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (anthropic) {
    headers["x-api-key"] = config.apiKey;
    headers["anthropic-version"] = "2023-06-01";
  } else if (config.apiKey) {
    headers["Authorization"] = `Bearer ${config.apiKey}`;
  }

  let lastError: Error | undefined;
  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal,
      });
      const text = await resp.text();
      if (!resp.ok) {
        const retryable = resp.status === 408 || resp.status === 429 || resp.status >= 500;
        if (retryable && attempt < config.maxRetries) {
          await sleep(config.retryBackoff * 2 ** attempt);
          continue;
        }
        throw new Error(`视觉 API 返回 ${resp.status}: ${text.slice(0, 300)}`);
      }
      let data: any;
      try {
        data = JSON.parse(text);
      } catch {
        throw new Error(`视觉 API 返回非 JSON 响应（前 300 字符）: ${text.slice(0, 300)}`);
      }
      if (responses) {
        const output = data?.output;
        if (Array.isArray(output)) {
          const texts = output
            .filter((o: any) => o && o.type === "message")
            .flatMap((o: any) => o.content ?? [])
            .filter((b: any) => b && b.type === "output_text" && b.text)
            .map((b: any) => b.text)
            .join("");
          if (texts) return texts;
        }
        throw new Error(`视觉 API 返回空文本: ${JSON.stringify(data)}`);
      }
      if (anthropic) {
        const blocks = data?.content;
        if (Array.isArray(blocks)) {
          const texts = blocks
            .filter((b: any) => b && b.type === "text" && b.text)
            .map((b: any) => b.text)
            .join("");
          if (texts) return texts;
        }
        throw new Error(`视觉 API 返回空文本: ${JSON.stringify(data)}`);
      }
      const msg = data?.choices?.[0]?.message;
      if (!msg) {
        throw new Error(`视觉 API 响应格式异常: ${JSON.stringify(data)}`);
      }
      if (typeof msg.content === "string") return msg.content;
      if (Array.isArray(msg.content)) {
        const parts = msg.content
          .filter((p: any) => p && p.type === "text")
          .map((p: any) => p.text ?? "")
          .join("");
        if (parts) return parts;
      }
      for (const key of ["reasoning", "reasoning_content"]) {
        if (msg[key]) return String(msg[key]);
      }
      throw new Error(`视觉 API 返回空文本: ${JSON.stringify(data)}`);
    } catch (e) {
      const err = e as Error;
      // 已格式化的 HTTP/解析错误不重试；仅网络抖动(fetch 抛 TypeError)可重试
      const isFormatted = err instanceof Error && err.message.startsWith("视觉 API");
      if (err.name !== "AbortError" && !isFormatted && attempt < config.maxRetries) {
        lastError = err;
        await sleep(config.retryBackoff * 2 ** attempt);
        continue;
      }
      throw err;
    }
  }
  throw lastError ?? new Error("视觉 API 调用失败");
}

// --------------------------------------------------------------------------- #
// 工具参数约定
// --------------------------------------------------------------------------- #
const IMAGE_ARGS = {
  image: Type.Optional(Type.String({ description: "图片：本地路径、http(s) URL 或 data: base64 URL" })),
  image_path: Type.Optional(Type.String({ description: "本地图片路径（与 image 互斥）" })),
  image_url: Type.Optional(Type.String({ description: "图片 URL（与 image 互斥）" })),
  image_base64: Type.Optional(Type.String({ description: "裸 base64 图片内容（与 image 互斥）" })),
};

const VISION_GUIDELINES = [
  "当对话中出现图片路径、图片 URL、截图文件，或用户要求查看/分析/识别/描述图片时，调用 vision_analyze 或 vision_ocr，不要用 read 工具读取图片文件（你无法理解像素）。",
  "图片是日志、终端、代码、报错弹窗或文档截图需要逐字提取文字时，调用 vision_ocr。",
  "需要同时分析多张图片时，调用 vision_analyze_batch，把每张图放进 items 列表。",
];

const VISION_TOOLS = ["vision_analyze", "vision_ocr", "vision_analyze_batch"];

/** 主模型是否原生支持图片（input 能力含 image）。缺失时视为不支持（安全默认：多模态才隐藏工具）。 */
function isMultimodal(model: unknown): boolean {
  const m = model as { input?: string[] } | undefined;
  return !!m?.input?.includes("image");
}

/**
 * 按主模型能力同步视觉工具可见性。
 * 多模态主模型 → 隐藏三个视觉工具（原生看图片，避免多余委派）；
 * 纯文本主模型 → 显示（委派给视觉后端）。可安全地在 session_start / model_select 反复调用。
 */
function syncVisionTools(pi: ExtensionAPI, model: unknown): void {
  const active = pi.getActiveTools();
  const hasAny = VISION_TOOLS.some((t) => active.includes(t));
  const shouldShow = !isMultimodal(model);
  if (shouldShow && !hasAny) {
    pi.setActiveTools([...new Set([...active, ...VISION_TOOLS])]);
  } else if (!shouldShow && hasAny) {
    pi.setActiveTools(active.filter((t) => !VISION_TOOLS.includes(t)));
  }
}

export default function visionExtension(pi: ExtensionAPI) {
  // 按主模型能力门控：多模态模型隐藏视觉工具，纯文本模型显示。可在 session_start / model_select 时同步。
  pi.on("session_start", (_event, ctx) => syncVisionTools(pi, ctx.model));
  pi.on("model_select", (event) => syncVisionTools(pi, event.model));
  // ------------------------------------------------------------------------- #
  // 工具：vision_analyze
  // ------------------------------------------------------------------------- #
  pi.registerTool({
    name: "vision_analyze",
    label: "Vision Analyze",
    description:
      "通用图片理解：把图片交给配置的多模态模型分析，返回文本描述。当主模型无法理解图片内容（截图、UI 图、流程图、报错截图、照片等）时使用。",
    promptSnippet: "分析图片内容并返回文本描述",
    promptGuidelines: VISION_GUIDELINES,
    parameters: Type.Object({
      prompt: Type.Optional(
        Type.String({ description: "具体分析要求，例如 '提取图中的报错信息和堆栈'" }),
      ),
      ...IMAGE_ARGS,
    }),
    async execute(toolCallId, params: any, signal, _onUpdate, ctx) {
      const dataUrl = await resolveImage(
        params.image ?? "",
        params.image_path ?? "",
        params.image_url ?? "",
        params.image_base64 ?? "",
        ctx.cwd,
        signal,
      );
      const prompt = (params.prompt ?? "请详细描述这张图片的全部内容。").trim() || "请详细描述这张图片的全部内容。";
      const key = cacheKey(dataUrl, prompt);
      const cached = cacheGet(key);
      if (cached !== undefined) {
        return { content: [{ type: "text", text: cached }], details: {} };
      }
      const text = await callVision([imageContent(dataUrl), { type: "text", text: prompt }], signal);
      cacheSet(key, text);
      return { content: [{ type: "text", text }], details: {} };
    },
  });

  // ------------------------------------------------------------------------- #
  // 工具：vision_ocr
  // ------------------------------------------------------------------------- #
  pi.registerTool({
    name: "vision_ocr",
    label: "Vision OCR",
    description:
      "纯 OCR：从图片中逐字提取可见文本，不做解释。适用于日志截图、终端截图、代码截图、错误弹窗、文档截图等。",
    promptSnippet: "逐字提取图片中的可见文本（OCR）",
    promptGuidelines: VISION_GUIDELINES,
    parameters: Type.Object({
      ...IMAGE_ARGS,
    }),
    async execute(toolCallId, params: any, signal, _onUpdate, ctx) {
      const dataUrl = await resolveImage(
        params.image ?? "",
        params.image_path ?? "",
        params.image_url ?? "",
        params.image_base64 ?? "",
        ctx.cwd,
        signal,
      );
      const prompt =
        "请逐字转录图中所有可见文本，保留换行和缩进结构。" +
        "只输出文字内容，不要解释，不要添加任何评注。无法识别的字符用 [?] 代替。";
      const key = cacheKey(dataUrl, prompt);
      const cached = cacheGet(key);
      if (cached !== undefined) {
        return { content: [{ type: "text", text: cached }], details: {} };
      }
      const text = await callVision([imageContent(dataUrl), { type: "text", text: prompt }], signal);
      cacheSet(key, text);
      return { content: [{ type: "text", text }], details: {} };
    },
  });

  // ------------------------------------------------------------------------- #
  // 工具：vision_analyze_batch
  // ------------------------------------------------------------------------- #
  pi.registerTool({
    name: "vision_analyze_batch",
    label: "Vision Analyze Batch",
    description:
      "批量分析多张图片：一次调用处理多张，单张失败不影响其他。适用于对比截图、一次查看多张图表、多页文档等。",
    promptSnippet: "批量分析多张图片",
    promptGuidelines: VISION_GUIDELINES,
    parameters: Type.Object({
      items: Type.Array(
        Type.Object({
          image: Type.Optional(Type.String()),
          image_path: Type.Optional(Type.String()),
          image_url: Type.Optional(Type.String()),
          image_base64: Type.Optional(Type.String()),
          prompt: Type.Optional(Type.String({ description: "该项单独的分析要求" })),
        }),
      ),
      prompt: Type.Optional(Type.String({ description: "未单独指定 prompt 的图片使用的默认分析要求" })),
      concurrency: Type.Optional(Type.Number({ description: "并发请求数（1-8，默认 3）" })),
    }),
    async execute(toolCallId, params: any, signal, _onUpdate, ctx) {
      const items: any[] = params.items ?? [];
      if (items.length === 0) throw new VisionError("empty_items", "items 不能为空");
      if (items.length > 50) throw new VisionError("too_many_items", "一次最多分析 50 张图片");
      const concurrency = Math.max(1, Math.min(8, Number(params.concurrency ?? 3) || 3));
      const defaultPrompt =
        (params.prompt ?? "请详细描述这张图片的全部内容。").trim() ||
        "请详细描述这张图片的全部内容。";

      const results: string[] = new Array(items.length);
      let next = 0;
      const worker = async () => {
        while (next < items.length) {
          const idx = next++;
          const item = items[idx] ?? {};
          try {
            const dataUrl = await resolveImage(
              String(item.image ?? ""),
              String(item.image_path ?? ""),
              String(item.image_url ?? ""),
              String(item.image_base64 ?? ""),
              ctx.cwd,
              signal,
            );
            const itemPrompt = (String(item.prompt ?? "").trim() || defaultPrompt);
            const key = cacheKey(dataUrl, itemPrompt);
            const cached = cacheGet(key);
            if (cached !== undefined) {
              results[idx] = `[${idx}] 成功\n${cached}`;
              continue;
            }
            const text = await callVision(
              [imageContent(dataUrl), { type: "text", text: itemPrompt }],
              signal,
            );
            cacheSet(key, text);
            results[idx] = `[${idx}] 成功\n${text}`;
          } catch (e) {
            results[idx] = `[${idx}] 失败: ${(e as Error).message}`;
          }
        }
      };
      await Promise.all(Array.from({ length: concurrency }, () => worker()));
      return { content: [{ type: "text", text: results.join("\n\n") }], details: {} };
    },
  });

  // ------------------------------------------------------------------------- #
  // 自动转录附加图片：当主模型不支持图片时，把附加图片交给视觉模型转成文本
  // ------------------------------------------------------------------------- #
  pi.on("before_agent_start", async (event, ctx) => {
    if (!config.autoTranscribe) return;
    const images = event.images ?? [];
    if (images.length === 0) return;

    const modelInput = ctx.model?.input;
    const supportsImages = Array.isArray(modelInput) && modelInput.includes("image");
    if (supportsImages) return; // 主模型看得见图片，无需转录

    const parts: string[] = [];
    for (let i = 0; i < images.length; i++) {
      const img = images[i] as { source?: { mediaType?: string; data?: string } } | undefined;
      const mediaType = img?.source?.mediaType ?? "image/png";
      const data = img?.source?.data;
      if (!data) continue;
      const dataUrl = `data:${mediaType};base64,${data}`;
      try {
        const text = await callVision(
          [
            imageContent(dataUrl),
            {
              type: "text",
              text:
                "这是用户附带的第" + (i + 1) + "张图片。请详细描述图片的全部内容。" +
                "如果是截图，请逐字转录其中文字并保留结构。",
            },
          ],
          ctx.signal,
        );
        parts.push(`[图片 ${i + 1}]\n${text}`);
      } catch (e) {
        parts.push(`[图片 ${i + 1}]\n（转录失败: ${(e as Error).message}，可要求用 vision_analyze 重试）`);
      }
    }
    if (parts.length === 0) return;

    return {
      message: {
        customType: "vision-transcription",
        content: `用户附带了 ${parts.length} 张图片，当前主模型无法直接查看，现将视觉模型转录结果附上：\n\n${parts.join("\n\n")}`,
        display: true,
      },
    };
  });
}