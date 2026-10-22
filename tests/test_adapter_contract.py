"""回归：适配器产出的 MessageEvent 必须通过 Hermes 原生准入的 metadata 契约。

Hermes 的 metadata 契约是封闭的（``gateway/session_envelope.py:_native_metadata``）：
只放行 WhatsApp 平台的专用键，其它平台只要带非空 metadata 就会被判
``invalid_params(metadata)``，消息不落库、只回 120 字符拒绝提示。
qwpad 的 ``_dispatch`` 曾经塞了 ``qwpad_msg_type`` / ``qwpad_event_id``，线上因此整条入站链路失效。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from gateway.config import Platform
from gateway.session_envelope import _native_metadata

from qwpad.adapter import QWPadAdapter
from qwpad.inbound import parse_event

# 真实网关里平台由插件注册后才可实例化；单测不拉起插件加载器，直接模拟运行时注册。
Platform._add_pseudo_member("qwpad")

SELF_ID = "1688850000000001"
SELF_NICK = "测试用户"


def _dm_event() -> dict:
    data = {"content": "你好"}
    return {
        "id": 1001,
        "createdAt": "2026-10-22 09:00:00",
        "type": 15000,
        "display": {
            "category": "chat",
            "text": "",
            "senderId": "1688851111111111",
            "senderName": "张三",
            "roomId": "0",
            "messageId": "MSG-1",
            "messageType": {"msgType": 0, "known": True, "nativeName": "kText"},
            "content": None,
        },
        "message": {
            "msgType": 0,
            "msgData": data,
            "structured": {
                "senderId": "1688851111111111",
                "senderName": "张三",
                "fromRoomId": "0",
                "receiverId": SELF_ID,
                "msgType": 0,
                "msgData": data,
                "msgUniqueIdentifier": "MSG-1",
                "isRevoked": False,
                "timestamp": 1792627702,
            },
        },
    }


def test_dispatch_event_passes_native_metadata_contract():
    adapter = QWPadAdapter(SimpleNamespace(extra={}))
    captured = []

    async def fake_handle(event):
        captured.append(event)

    adapter.handle_message = fake_handle  # 不启动真实网关，只验证事件形状
    inbox = parse_event(_dm_event(), self_id=SELF_ID, self_nick=SELF_NICK)
    assert inbox is not None

    asyncio.run(adapter._dispatch(inbox))

    assert len(captured) == 1
    # None 表示会被原生准入拒绝；{} 才表示通过。
    assert _native_metadata(captured[0]) is not None
