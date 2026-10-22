"""出站：文本分片、markdown 清洗、媒体上传，以及 cron 用的独立发送。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Optional

from gateway.platforms.base import SendResult
from gateway.platforms._shared import get_scoped_secret as _get_scoped_secret, send_error

from .client import (
    DEFAULT_BASE_URL, FILE_TYPE_FILE, FILE_TYPE_IMAGE, QWPadAuthError, QWPadClient, QWPadError,
)
from .media import encode_voice_to_silk

logger = logging.getLogger(__name__)

# 企业微信文本消息上限（字符数）。超出会被服务端截断，所以自己分片。
MAX_MESSAGE_LENGTH = 2000

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_MARKDOWN_RULES = (
    (r"\*\*(.+?)\*\*", r"\1"),                 # 粗体
    (r"__(.+?)__", r"\1"),
    (r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1"),  # 斜体
    (r"`([^`\n]+)`", r"\1"),                   # 行内代码
    (r"^```\w*$", ""),                          # 代码围栏行
    (r"!\[([^\]]*)\]\(([^)\s]+)\)", r"\2"),     # 图片 → url（先于链接）
    (r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)"),  # 链接 → 文本 (url)
    (r"^#{1,6}\s+", ""),                        # 标题
    (r"^&gt;\s?", ""),
    (r"^>\s?", ""),                             # 引用
)


def strip_markdown(text: str) -> str:
    """企业微信不渲染 markdown —— 转成可读纯文本。"""
    out = text or ""
    for pattern, repl in _MARKDOWN_RULES:
        out = re.sub(pattern, repl, out, flags=re.MULTILINE)
    return out


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """按字符数分片：先按空行/换行，再硬切超长段落。"""
    limit = max(200, int(limit or MAX_MESSAGE_LENGTH))
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buffer = ""
    for paragraph in text.split("\n"):
        candidate = f"{buffer}\n{paragraph}" if buffer else paragraph
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return [c for c in (chunk.strip() for chunk in chunks) if c]


def _fail(message: str) -> SendResult:
    return SendResult(success=False, error=message)


def _ok(payload: dict[str, Any]) -> SendResult:
    msg_id = payload.get("msgServerId") or payload.get("msgUniqueIdentifier") or ""
    return SendResult(success=True, message_id=str(msg_id) or None)


async def send_text(client: QWPadClient, chat_id: str, content: str, *,
                    reply: Optional[dict[str, Any]] = None,
                    limit: int = MAX_MESSAGE_LENGTH) -> SendResult:
    """发一条（自动分片的）文本消息。"""
    pieces = split_message(strip_markdown(content), limit)
    if not pieces:
        return _fail("empty message")
    last: Optional[dict[str, Any]] = None
    for index, piece in enumerate(pieces):
        try:
            last = await client.send_text(chat_id, piece, reply=reply if index == 0 else None)
        except QWPadError as exc:
            return _fail(str(exc))
    return _ok(last or {})


async def send_quoted_text(client: QWPadClient, chat_id: str, content: str, *,
                           reply: dict[str, Any],
                           limit: int = MAX_MESSAGE_LENGTH) -> SendResult:
    """发引用回复：引用气泡 + 可见正文。

    实测（2026-10-22）：QWPad 的 ``/msg/sendText|sendHyperText`` + ``reply`` 生成的
    kRichText 引用是 ``f1002.f4=1``（只引用模式），单段正文会被客户端隐藏；正文按
    真人客户端的四段结构（``"`` / 原文 / ``"\\n------\\n`` / 新内容）拼又会把引号和
    分隔线原样显示出来（API 发不出真人客户端的 kText）。

    可行解：正文用两段，首段为空串、第二段放回复内容。客户端会正常渲染正文，引用
    气泡仍由 ``reply`` 生成，且不会出现任何标记字符。
    """
    pieces = split_message(strip_markdown(content), limit)
    if not pieces:
        return _fail("empty message")
    parts = [
        {"subtype": 0, "text": ""},
        {"subtype": 0, "text": pieces[0]},
    ]
    try:
        last = await client.send_hyper_text(chat_id, parts, reply=reply)
    except QWPadError as exc:
        return _fail(str(exc))
    for piece in pieces[1:]:
        try:
            last = await client.send_text(chat_id, piece)
        except QWPadError as exc:
            return _fail(str(exc))
    return _ok(last or {})


def infer_upload_kind(path: str) -> int:
    return FILE_TYPE_IMAGE if os.path.splitext(path or "")[1].lower() in _IMAGE_EXT else FILE_TYPE_FILE


async def send_media(client: QWPadClient, chat_id: str, path: str, *,
                     caption: Optional[str] = None,
                     reply: Optional[dict[str, Any]] = None,
                     force_document: bool = False) -> SendResult:
    """上传本地文件并按图片/文件发送。"""
    if not path or not os.path.isfile(path):
        return _fail(f"file not found: {path}")
    name = os.path.basename(path) or "file"
    if caption:
        text_result = await send_text(client, chat_id, caption)
        if not text_result.success:
            return text_result
    kind = FILE_TYPE_FILE if force_document else infer_upload_kind(path)
    try:
        upload = await client.upload_file(path, kind)
        if kind == FILE_TYPE_IMAGE:
            payload = await client.send_image(chat_id, upload, name, reply=reply)
        else:
            payload = await client.send_file(chat_id, upload, name, reply=reply)
    except QWPadError as exc:
        return _fail(str(exc))
    return _ok(payload or {})


async def send_voice(client: QWPadClient, chat_id: str, audio_path: str, *,
                     caption: Optional[str] = None,
                     reply: Optional[dict[str, Any]] = None) -> SendResult:
    """发语音气泡：ffmpeg → SILK → ``/msg/sendVoice``。

    SILK 编码不可用/失败时降级为普通文件（与 wpp 的既定偏好一致），
    并在日志里写明原因，避免「日志说成功、用户听不到」。
    """
    if not audio_path or not os.path.isfile(audio_path):
        return _fail(f"file not found: {audio_path}")
    if caption:
        text_result = await send_text(client, chat_id, caption)
        if not text_result.success:
            return text_result
    encoded = await encode_voice_to_silk(audio_path)
    if not encoded:
        logger.warning("[QWPad] SILK 编码不可用，语音降级为文件: %s", audio_path)
        return await send_media(client, chat_id, audio_path, force_document=True)
    silk, voice_time = encoded
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as fh:
            fh.write(silk)
            tmp_path = fh.name
        upload = await client.upload_file(tmp_path, FILE_TYPE_FILE)
        payload = await client.send_voice(chat_id, upload, voice_time, reply=reply)
    except QWPadError as exc:
        return _fail(str(exc))
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return _ok(payload or {})


# ── cron / send_message 的进程外发送 ────────────────────────────────────────

def _token_from(pconfig: Any) -> str:
    extra = getattr(pconfig, "extra", {}) or {}
    token = _get_scoped_secret("QWPAD_TOKEN") or extra.get("token") or getattr(pconfig, "token", "") or ""
    return str(token).strip()


def _base_from(pconfig: Any) -> str:
    extra = getattr(pconfig, "extra", {}) or {}
    return str(_get_scoped_secret("QWPAD_API_BASE") or extra.get("api_base") or DEFAULT_BASE_URL).strip()


async def standalone_send(pconfig: Any, chat_id: str, message: str, *,
                          thread_id: Optional[str] = None,
                          media_files: Optional[list[str]] = None,
                          force_document: bool = False) -> dict[str, Any]:
    """cron 任务在 gateway 进程外发消息：临时建客户端，发完即关。"""
    token = _token_from(pconfig)
    if not token:
        return send_error("QWPad standalone send: QWPAD_TOKEN 未配置")
    client = QWPadClient(token, _base_from(pconfig))
    try:
        result = await send_text(client, chat_id, message,
                                 limit=int((getattr(pconfig, "extra", {}) or {}).get("max_message_length") or MAX_MESSAGE_LENGTH))
        if not result.success:
            return send_error(f"QWPad standalone send: {result.error}")
        for path in media_files or []:
            media = await send_media(client, chat_id, path, force_document=force_document)
            if not media.success:
                return send_error(f"QWPad standalone media: {media.error}")
        return {"success": True, "message_id": result.message_id}
    except QWPadAuthError as exc:
        return send_error(f"QWPad standalone send: TOKEN 失效 ({exc})")
    finally:
        await client.close()
