"""LIVE 冒烟测试：需要真 TOKEN，默认跳过。

    QWPAD_LIVE_TOKEN=wxa_xxx QWPAD_LIVE_TARGET=<自己 userId> \
      PYTHONPATH="$HERMES_HOME/hermes-agent:$HSP" .venv/bin/python -m pytest tests/test_live_smoke.py -q

验证三件事：TOKEN 有效 / 事件游标能前进 / 能真的发出一条文本。
"""

from __future__ import annotations

import asyncio
import os

import pytest

from qwpad.client import QWPadClient

TOKEN = os.environ.get("QWPAD_LIVE_TOKEN", "").strip()
TARGET = os.environ.get("QWPAD_LIVE_TARGET", "").strip()

pytestmark = pytest.mark.skipif(not TOKEN, reason="需要 QWPAD_LIVE_TOKEN")


def test_live_login_status():
    async def run():
        client = QWPadClient(TOKEN)
        try:
            return await client.get_login_status()
        finally:
            await client.close()

    status = asyncio.run(run())
    assert status.get("userId"), f"TOKEN 未绑定账号: {status}"
    assert int(status.get("errorCode") or 0) == 0, f"账号不在线: {status}"


def test_live_events_cursor():
    async def run():
        client = QWPadClient(TOKEN)
        try:
            return await client.fetch_events(0, limit=5, wait_seconds=0)
        finally:
            await client.close()

    page = asyncio.run(run())
    assert page.next_id >= 0
    for item in page.items:
        assert "id" in item


@pytest.mark.skipif(not TARGET, reason="需要 QWPAD_LIVE_TARGET")
def test_live_send_text():
    async def run():
        client = QWPadClient(TOKEN)
        try:
            return await client.send_text(TARGET, "QWPad adapter smoke test（忽略）")
        finally:
            await client.close()

    payload = asyncio.run(run())
    assert int(payload.get("isSendSuccess") or 0) == 1, payload
