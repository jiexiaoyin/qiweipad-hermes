#!/usr/bin/env python3
"""LIVE 探针：用真实 TOKEN 跑一遍「连接 → 收事件 → 发消息」的真实路径。

用法::

    QWPAD_TOKEN=wxa_xxx .venv/bin/python scripts/live_probe.py --target 1688850000000001
    QWPAD_TOKEN=wxa_xxx .venv/bin/python scripts/live_probe.py --listen 120

``--target`` 发一条自测文本（建议填自己的 userId）；``--listen`` 保持轮询 N 秒打印
收到的每条消息（解码结果）。两者可同时用。

必须在 Hermes 的 Python 环境里跑（要 import gateway.platforms.base）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_TREE = Path(os.environ.get("HERMES_TREE", HERMES_HOME / "hermes-agent"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERMES_TREE))

from gateway.config import PlatformConfig  # noqa: E402
from gateway.platform_registry import PlatformEntry, platform_registry  # noqa: E402
from gateway.platforms.event import MessageEvent  # noqa: E402

from qwpad.adapter import PLATFORM_NAME, QWPadAdapter  # noqa: E402

# 探针不经过插件加载器，手工把平台登记进 registry（否则 Platform("qwpad") 不认）
if not platform_registry.is_registered(PLATFORM_NAME):
    platform_registry.register(PlatformEntry(
        name=PLATFORM_NAME, label="QWPad", adapter_factory=QWPadAdapter,
        check_fn=lambda: True, max_message_length=2000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("probe")


class Probe:
    def __init__(self) -> None:
        self.events: list[MessageEvent] = []
        self.received = asyncio.Event()

    async def on_message(self, event: MessageEvent) -> None:
        self.events.append(event)
        src = event.source
        media = ",".join(event.media_urls) if event.media_urls else "-"
        print("\n=== 收到消息 ===")
        print(f"  chat: {src.chat_type} {src.chat_name or src.chat_id} ({src.chat_id})")
        print(f"  user: {src.user_name} ({src.user_id})")
        print(f"  type: {event.message_type}  id={event.message_id}")
        print(f"  text: {event.text!r}")
        print(f"  media: {media}")
        if event.reply_to_text:
            print(f"  reply_to: {event.reply_to_text!r}")
        print("================\n", flush=True)
        self.received.set()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="", help="发一条自测文本的目标（userId 或 roomId）")
    parser.add_argument("--text", default="QWPad adapter live probe（忽略这条）")
    parser.add_argument("--listen", type=int, default=0, help="保持轮询 N 秒")
    parser.add_argument("--image", default="", help="发一张本地图片到 --target（验证上传+sendImage）")
    parser.add_argument("--file", default="", help="发一个本地文件到 --target（验证 sendFile）")
    parser.add_argument("--api-base", default=os.environ.get("QWPAD_API_BASE", ""))
    args = parser.parse_args()

    token = os.environ.get("QWPAD_TOKEN", "").strip()
    if not token:
        print("缺少 QWPAD_TOKEN", file=sys.stderr)
        return 2

    config = PlatformConfig(enabled=True, extra={"token": token})
    if args.api_base:
        config.extra["api_base"] = args.api_base
    adapter = QWPadAdapter(config)
    probe = Probe()
    adapter.set_message_handler(probe.on_message)

    ok = await adapter.connect()
    print(f"connect() -> {ok}")
    if not ok:
        print(f"fatal: {adapter.fatal_error}")
        return 1
    print(f"self: id={adapter._self_id} nick={adapter._self_nick} cursor={adapter._cursor}")
    try:
        if args.target:
            result = await adapter.send(args.target, args.text)
            print(f"send() -> success={result.success} id={result.message_id} err={result.error}")
        if args.image and args.target:
            media = await adapter.send_image_file(args.target, args.image, caption=args.text)
            print(f"send_image_file() -> success={media.success} id={media.message_id} err={media.error}")
        if args.file and args.target:
            doc = await adapter.send_document(args.target, args.file, caption=args.text)
            print(f"send_document() -> success={doc.success} id={doc.message_id} err={doc.error}")
        if args.listen:
            print(f"轮询 {args.listen}s，等待入站消息…（给对方发条消息试试）")
            try:
                await asyncio.wait_for(probe.received.wait(), timeout=args.listen)
            except TimeoutError:
                print("监听超时，没有收到消息")
        info = await adapter.get_chat_info(adapter._self_id)
        print(f"get_chat_info(self) -> {info}")
    finally:
        await adapter.disconnect()
        print(f"disconnect() 完成，收到 {len(probe.events)} 条消息")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
