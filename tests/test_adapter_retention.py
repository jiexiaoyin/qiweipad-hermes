"""适配器分流单测：留存事件绝不能走到 handle_message。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from gateway.config import Platform

from qwpad.adapter import QWPadAdapter
from qwpad.retained import RetentionStore

Platform._add_pseudo_member("qwpad")

SELF_ID = "1688850000000001"
SELF_NICK = "测试用户"


def _event(msg_type, *, category, event_type=15000, data=None, sender="u", room="0",
           title="", native_name=None, event_id=1001):
    return {
        "id": event_id,
        "createdAt": "2026-10-22 00:25:57",
        "type": event_type,
        "display": {
            "category": category,
            "text": "",
            "senderId": sender,
            "senderName": "",
            "roomId": room,
            "messageId": f"MSG-{event_id}",
            "messageType": {"msgType": msg_type, "known": True, "nativeName": native_name},
            "title": title,
            "content": None,
        },
        "message": {
            "msgType": msg_type,
            "msgData": data,
            "structured": {
                "senderId": sender,
                "fromRoomId": room,
                "receiverId": SELF_ID,
                "msgType": msg_type,
                "msgData": data,
                "msgUniqueIdentifier": f"MSG-{event_id}",
                "isRevoked": False,
                "timestamp": 1792628756,
            },
        },
    }


def _adapter(tmp_path) -> tuple[QWPadAdapter, list, list]:
    adapter = QWPadAdapter(SimpleNamespace(extra={}))
    adapter._self_id = SELF_ID
    adapter._self_nick = SELF_NICK
    adapter._retention = RetentionStore("token", directory=tmp_path, retention_days=0)
    dispatched: list = []
    retained: list = []

    async def fake_dispatch(inbox):
        dispatched.append(inbox)

    def fake_retain(item, kind):
        retained.append((item, kind))

    adapter._dispatch = fake_dispatch
    adapter._retain_event = fake_retain
    return adapter, dispatched, retained


def test_friend_apply_is_retained_not_dispatched(tmp_path):
    adapter, dispatched, retained = _adapter(tmp_path)
    item = _event(2357, category="system", event_type=15500, data={
        "contactId": "7881300000000001", "contactNickname": "测试用户"})
    asyncio.run(adapter._handle_event_item(item))
    assert dispatched == []
    assert retained and retained[0][1] == "friend_apply"


def test_read_receipt_is_dropped(tmp_path):
    adapter, dispatched, retained = _adapter(tmp_path)
    asyncio.run(adapter._handle_event_item(
        _event(2001, category="unknown", data={"extras": None})))
    assert dispatched == [] and retained == []


def test_chat_message_still_dispatches(tmp_path):
    adapter, dispatched, retained = _adapter(tmp_path)
    item = _event(2, category="chat", data={"content": "你好"}, sender="1688851111111111")
    asyncio.run(adapter._handle_event_item(item))
    assert len(dispatched) == 1
    assert dispatched[0].text == "你好"
    assert retained == []


def test_app_card_in_chat_is_retained_not_dispatched(tmp_path):
    adapter, dispatched, retained = _adapter(tmp_path)
    item = _event(38, category="chat", data={
        "title": "欢迎语提醒", "content": "<div>客户名：测试用户</div>"},
        sender="5629500000000001", native_name="kAppTextCard")
    asyncio.run(adapter._handle_event_item(item))
    assert dispatched == []
    assert retained and retained[0][1] == "app_card"
