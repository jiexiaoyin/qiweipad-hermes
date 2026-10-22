"""出站文本处理 + 客户端协议层单测（httpx.MockTransport，不联网）。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from qwpad.client import (
    DEFAULT_BASE_URL, QWPadApiError, QWPadAuthError, QWPadClient, QWPadError,
)
from qwpad.outbound import MAX_MESSAGE_LENGTH, send_quoted_text, split_message, strip_markdown


# ── 文本 ────────────────────────────────────────────────────────────────────
def test_strip_markdown():
    text = "**重点**：看 [报表](https://x.com/a) 和 `code`\n```python\nprint(1)\n```"
    out = strip_markdown(text)
    assert "**" not in out
    assert "[报表]" not in out
    assert "报表 (https://x.com/a)" in out
    assert "```" not in out
    assert "print(1)" in out


def test_split_short_is_single_chunk():
    assert split_message("你好") == ["你好"]


def test_split_long_respects_limit():
    text = "\n".join("第%d行内容" % i for i in range(2000))
    chunks = split_message(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_hard_cut_without_newlines():
    chunks = split_message("字" * 4500, MAX_MESSAGE_LENGTH)
    assert [len(c) for c in chunks] == [2000, 2000, 500]


# ── 客户端 ──────────────────────────────────────────────────────────────────
def _client(handler) -> QWPadClient:
    return QWPadClient("wxa_test", DEFAULT_BASE_URL, transport=httpx.MockTransport(handler))


def test_send_text_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "成功",
                                         "data": {"msgServerId": 123, "isSendSuccess": 1}})

    async def run():
        client = _client(handler)
        try:
            data = await client.send_text("1688851111111111", "你好")
        finally:
            await client.close()
        return data

    data = asyncio.run(run())
    assert seen["path"] == "/msg/sendText"
    assert seen["auth"] == "Bearer wxa_test"
    assert seen["body"] == {"toId": "1688851111111111", "content": "你好"}
    assert data["msgServerId"] == 123


def test_send_hyper_text_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "成功",
                                         "data": {"msgServerId": 123, "isSendSuccess": 1}})

    parts = [{"subtype": 0, "text": "你好"}]

    async def run():
        client = _client(handler)
        try:
            return await client.send_hyper_text("1688851111111111", parts,
                                                reply={"msgUniqueIdentifier": "X"})
        finally:
            await client.close()

    data = asyncio.run(run())
    assert seen["path"] == "/msg/sendHyperText"
    assert seen["body"] == {
        "toId": "1688851111111111", "content": parts,
        "reply": {"msgUniqueIdentifier": "X"},
    }
    assert data["msgServerId"] == 123


class _RecordingClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.hyper_calls: list[tuple] = []

    async def send_text(self, chat_id, content, reply=None):
        self.calls.append((chat_id, content, reply))
        return {"msgServerId": "2"}

    async def send_hyper_text(self, chat_id, content, reply=None):
        self.hyper_calls.append((chat_id, content, reply))
        return {"msgServerId": "1"}


def test_send_quoted_text_uses_empty_lead_segment():
    client = _RecordingClient()
    reply = {"msgUniqueIdentifier": "X"}

    result = asyncio.run(send_quoted_text(
        client, "chat", "**好的**", reply=reply))

    assert result.success
    # 首段为空、第二段正文：客户端才会在引用气泡下渲染正文；
    # 正文里不能再拼引号/分隔线，否则会原样显示。
    assert client.hyper_calls[0][1] == [
        {"subtype": 0, "text": ""},
        {"subtype": 0, "text": "好的"},
    ]
    assert client.hyper_calls[0][2] is reply
    assert client.calls == []


def test_send_quoted_text_only_first_chunk_quotes():
    client = _RecordingClient()
    content = "字" * 2500

    asyncio.run(send_quoted_text(
        client, "chat", content, reply={"msgUniqueIdentifier": "X"}))

    assert client.hyper_calls[0][1][1]["text"] == content[:MAX_MESSAGE_LENGTH]
    assert client.calls == [("chat", content[MAX_MESSAGE_LENGTH:], None)]


def test_fetch_events_params_and_page():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0, "msg": "成功", "data": {
            "items": [{"id": 7, "message": {"msgType": 0}}], "nextId": 7, "hasMore": False}})

    async def run():
        client = _client(handler)
        try:
            return await client.fetch_events(5, limit=50, wait_seconds=3, event_type=15000)
        finally:
            await client.close()

    page = asyncio.run(run())
    assert seen["params"]["afterId"] == "5"
    assert seen["params"]["waitSeconds"] == "3"
    assert seen["params"]["format"] == "unified"
    assert seen["params"]["type"] == "15000"
    assert page.next_id == 7 and len(page.items) == 1


def test_fetch_events_omits_type_by_default():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0, "msg": "成功", "data": {
            "items": [], "nextId": 0, "hasMore": False}})

    async def run():
        client = _client(handler)
        try:
            return await client.fetch_events(0, limit=5, wait_seconds=0)
        finally:
            await client.close()

    asyncio.run(run())
    assert "type" not in seen["params"]


def test_auth_error_is_not_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 401400, "msg": "认证失败", "data": {}})

    async def run():
        client = _client(handler)
        try:
            await client.get_login_status()
        finally:
            await client.close()

    with pytest.raises(QWPadAuthError) as info:
        asyncio.run(run())
    assert info.value.retryable is False


def test_business_error_has_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 422100, "msg": "请检查请求json格式", "data": {}})

    async def run():
        client = _client(handler)
        try:
            await client.send_text("1", "x")
        finally:
            await client.close()

    with pytest.raises(QWPadApiError) as info:
        asyncio.run(run())
    assert info.value.code == 422100


def test_server_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async def run():
        client = _client(handler)
        try:
            await client.fetch_events(0, wait_seconds=1)
        finally:
            await client.close()

    with pytest.raises(QWPadError) as info:
        asyncio.run(run())
    assert info.value.retryable is True


def test_upload_multipart():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"code": 0, "msg": "成功", "data": {
            "fileId": "F1", "fileAesKey": "K1", "fileSize": 3, "fileMd5": "M1"}})

    async def run(tmp_path):
        path = tmp_path / "a.png"
        path.write_bytes(b"PNG")
        client = _client(handler)
        try:
            return await client.upload_file(str(path), 1)
        finally:
            await client.close()

    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    data = asyncio.run(run(tmp))
    assert seen["path"] == "/cloud/cdnBigUpload"
    assert "multipart/form-data" in seen["content_type"]
    assert b'name="fileType"' in seen["body"]
    assert data["fileId"] == "F1"
