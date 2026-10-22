"""文本回复自动引用：Hermes 不传 reply_to 时引用本轮入站消息。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from gateway.config import Platform

import qwpad.adapter as adapter_module
from qwpad.adapter import QWPadAdapter

Platform._add_pseudo_member("qwpad")

SELF_ID = "1688850000000001"
CHAT_ID = "7881300000000001"


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.hyper_calls: list[tuple[str, list[dict], dict | None]] = []

    async def send_text(self, chat_id, content, reply=None):
        self.calls.append((str(chat_id), content, reply))
        return {"msgServerId": "1"}

    async def send_hyper_text(self, chat_id, content, reply=None):
        self.hyper_calls.append((str(chat_id), content, reply))
        return {"msgServerId": "2"}


def _event(msg_type=2, *, event_id=1001, data=None, sender=CHAT_ID):
    data = data if data is not None else {
        "atList": [],
        "content": "没啥事，我就是做测试",
        "extras": None,
        "moreDetail": [{"subtype": 0, "text": "没啥事，我就是做测试"}],
    }
    return {
        "id": event_id,
        "createdAt": "2026-10-22 09:03:03",
        "type": 15000,
        "display": {
            "category": "chat",
            "text": "",
            "senderId": sender,
            "senderName": "",
            "roomId": "0",
            "messageId": f"MSG-{event_id}",
            "messageType": {"msgType": msg_type, "known": True, "nativeName": "kRichText"},
        },
        "message": {
            "msgType": msg_type,
            "msgData": data,
            "structured": {
                "senderId": sender,
                "fromRoomId": "0",
                "receiverId": SELF_ID,
                "msgType": msg_type,
                "msgData": data,
                "msgUniqueIdentifier": f"UNIQ-{event_id}",
                "isRevoked": False,
                "timestamp": 1700000000,
            },
        },
    }


def _adapter(*, quote=True):
    adapter = QWPadAdapter(SimpleNamespace(extra={}))
    adapter._self_id = SELF_ID
    adapter._running = True
    adapter.quote_replies = quote
    adapter._client = _FakeClient()
    return adapter


def _inbox(adapter, **kwargs):
    from qwpad.inbound import parse_event
    inbox = parse_event(_event(**kwargs), self_id=SELF_ID)
    assert inbox is not None
    return inbox


def test_anchor_uses_cached_contact_name():
    from qwpad.adapter import _contact_display_name
    assert _contact_display_name({"nickname": "测试用户"}) == "测试用户"
    assert _contact_display_name({"fullUserInfo": {"name": "陈老板"}}) == "陈老板"

    adapter = _adapter()
    inbox = _inbox(adapter)
    adapter._contact_cache[CHAT_ID] = ("测试用户", time.monotonic())
    adapter._remember_reply_anchor(inbox)
    assert adapter._replies[inbox.message_id]["showName"] == "测试用户"


def test_anchor_keeps_original_msgdata():
    adapter = _adapter()
    inbox = _inbox(adapter)

    adapter._remember_reply_anchor(inbox)

    anchor = adapter._replies[inbox.message_id]
    assert anchor["msgUniqueIdentifier"] == inbox.message_id
    assert anchor["type"] == 2
    assert anchor["timeStamp"] == 1700000000
    assert anchor["userId"] == CHAT_ID
    assert anchor["msgData"]["moreDetail"][0]["text"] == "没啥事，我就是做测试"


def test_anchor_strips_html_from_rich_text():
    adapter = _adapter()
    inbox = _inbox(adapter, data={
        "content": '<div class="msg">第一行</div><br>第二行', "extras": None})

    adapter._remember_reply_anchor(inbox)

    assert adapter._replies[inbox.message_id]["text"] == "第一行\n第二行"


def test_send_auto_quotes_latest_inbound():
    adapter = _adapter()
    inbox = _inbox(adapter)
    adapter._remember_reply_anchor(inbox)

    asyncio.run(adapter.send(CHAT_ID, "好的"))

    assert adapter._client.calls == []
    _, parts, reply = adapter._client.hyper_calls[0]
    assert reply and reply["msgUniqueIdentifier"] == inbox.message_id
    # 首段为空、第二段正文：引用气泡由 reply 生成，正文可见且不带标记字符。
    assert [part["text"] for part in parts] == ["", "好的"]
    assert CHAT_ID not in adapter._pending_reply


def test_interim_send_keeps_anchor_for_final():
    adapter = _adapter()
    inbox = _inbox(adapter)
    adapter._remember_reply_anchor(inbox)

    asyncio.run(adapter.send(CHAT_ID, "处理中", metadata={"_interim_send": True}))
    asyncio.run(adapter.send(CHAT_ID, "完成"))

    assert len(adapter._client.hyper_calls) == 2
    assert adapter._client.hyper_calls[0][2] is not None
    assert adapter._client.hyper_calls[1][2] is not None
    assert CHAT_ID not in adapter._pending_reply


def test_expired_anchor_is_not_quoted():
    adapter = _adapter()
    inbox = _inbox(adapter)
    adapter._remember_reply_anchor(inbox)
    anchor = adapter._pending_reply[CHAT_ID][1]
    adapter._pending_reply[CHAT_ID] = (
        time.monotonic() - adapter_module.QUOTE_WINDOW_S - 1, anchor)

    asyncio.run(adapter.send(CHAT_ID, "普通通知"))

    assert adapter._client.hyper_calls == []
    assert adapter._client.calls[0][2] is None


def test_quote_disabled_sends_plain_text():
    adapter = _adapter(quote=False)
    inbox = _inbox(adapter)
    adapter._remember_reply_anchor(inbox)

    asyncio.run(adapter.send(CHAT_ID, "好的"))

    assert adapter._client.hyper_calls == []
    assert adapter._client.calls[0][2] is None


def test_non_text_anchor_falls_back_to_plain_text():
    adapter = _adapter()
    inbox = _inbox(adapter)
    adapter._remember_reply_anchor(inbox)
    anchor = adapter._pending_reply[CHAT_ID][1]
    anchor["type"] = 1          # 原消息是图片：引用渲染未验证，先退化为普通文本
    anchor["text"] = ""

    asyncio.run(adapter.send(CHAT_ID, "收到图片"))

    assert adapter._client.hyper_calls == []
    assert adapter._client.calls[0][2] is None
