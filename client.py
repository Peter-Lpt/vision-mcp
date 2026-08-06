#!/usr/bin/env python3
"""vision-mcp 命令行客户端（通过 MCP stdio 协议调用服务器工具）。

不依赖 Codex 应用是否把 MCP 工具注入到会话，任何环境都能直接用：

    python client.py tools
    python client.py analyze <图片路径或URL> [提示词]
    python client.py ocr <图片路径或URL>
    python client.py batch <图片1> <图片2> ... [提示词]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "server.py"


def _image_args(image: str) -> dict:
    """本地路径走 image_path，http(s)/data: 走 image。"""
    if image.startswith(("http://", "https://", "data:")):
        return {"image": image}
    return {"image_path": image}


def _result_text(result) -> str:
    parts = []
    for item in result.content:
        if getattr(item, "type", "") == "text":
            parts.append(item.text)
    return "\n".join(parts)


async def _call(tool: str, arguments: dict) -> str:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    return _result_text(result)


async def _list_tools() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for tool in tools.tools:
                desc = tool.description.strip().splitlines()[0] if tool.description else ""
                print(f"{tool.name}: {desc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="vision-mcp CLI 客户端")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("tools", help="列出服务器提供的 MCP 工具")

    p = sub.add_parser("analyze", help="通用图片理解")
    p.add_argument("image", help="本地图片路径 / http(s) URL / data URL")
    p.add_argument("prompt", nargs="?", default="请详细描述这张图片的全部内容。")

    p = sub.add_parser("ocr", help="逐字提取图片中的文字")
    p.add_argument("image", help="本地图片路径 / http(s) URL / data URL")

    p = sub.add_parser("batch", help="批量分析多张图片")
    p.add_argument("images", nargs="+", help="多个图片路径 / http(s) URL / data URL")
    p.add_argument("prompt", nargs="?", default="请详细描述这张图片的全部内容。")

    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "tools":
        asyncio.run(_list_tools())
        return 0
    if args.command == "analyze":
        text = asyncio.run(
            _call("vision_analyze", {"prompt": args.prompt, **_image_args(args.image)})
        )
    elif args.command == "batch":
        items = [_image_args(img) for img in args.images]
        text = asyncio.run(
            _call("vision_analyze_batch", {"items": items, "prompt": args.prompt})
        )
    else:
        text = asyncio.run(_call("vision_ocr", _image_args(args.image)))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
