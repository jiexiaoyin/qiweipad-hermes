"""QWPad（企业微信 pad 协议）Hermes 平台适配器。

一个 TOKEN = 一个企业微信账号；收靠 ``GET /events`` long-poll，发靠 ``/msg/sendText`` 等。
不走 bot / 自建应用回调，消息是以「真人客户端」身份收发的。

配置（env 优先于 config.yaml ``gateway.platforms.qwpad.extra``）：

| env | extra | 说明 |
|---|---|---|
| ``QWPAD_TOKEN`` | ``token`` | 必填，``wxa_`` 前缀业务 TOKEN |
| ``QWPAD_API_BASE`` | ``api_base`` | 默认 ``https://api.qwpad.com`` |
| ``QWPAD_ALLOWED_USERS`` | ``allowed_users`` | 允许对话的成员 ID，逗号分隔 |
| ``QWPAD_ALLOW_ALL_USERS`` | ``allow_all_users`` | 放开所有人（仅调试） |
| ``QWPAD_HOME_CHANNEL`` | — | cron ``deliver=qwpad`` 的默认目标 |
| ``QWPAD_GROUP_REQUIRE_MENTION`` | ``require_mention`` | 群里是否必须 @ 才回（默认 true） |
| ``QWPAD_POLL_WAIT_SECONDS`` | ``poll_wait_seconds`` | long-poll 等待秒数（1-30，默认 25） |
| ``QWPAD_MAX_MESSAGE_LENGTH`` | ``max_message_length`` | 单条文本上限（默认 2000） |
| ``QWPAD_QUOTE_REPLIES`` | ``quote_replies`` | 回复时引用原消息（默认 true） |
| ``QWPAD_SILK_DECODER`` | — | silk 解码器路径（语音转写用） |
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx

from gateway.config import Platform
from gateway.platforms._shared import get_scoped_secret as _get_scoped_secret
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms.helpers import cancel_task

from .client import DEFAULT_BASE_URL, QWPadAuthError, QWPadClient, QWPadError
from .inbound import InboundMessage, parse_event, reply_context, strip_html
from .media import cache_media
from .outbound import MAX_MESSAGE_LENGTH, send_media, send_quoted_text, send_text, send_voice
from .retained import RetentionPolicy, RetentionStore, classify_event
from .state import CursorStore, DedupeSet, token_fingerprint

logger = logging.getLogger(__name__)

PLATFORM_NAME = "qwpad"
MAX_URL_DOWNLOAD_BYTES = 20 * 1024 * 1024
STATUS_CHECK_INTERVAL_S = 300.0
ROOM_NAME_TTL_S = 900.0
# 自动引用窗口：入站消息后这么久内的首条发送引用它；超时视为普通通知，不引用。
QUOTE_WINDOW_S = 600.0

_MESSAGE_TYPES = {
    "text": MessageType.TEXT,
    "image": MessageType.PHOTO,
    "voice": MessageType.VOICE,
    "video": MessageType.VIDEO,
    "file": MessageType.DOCUMENT,
}


def _contact_display_name(entry: dict[str, Any]) -> str:
    """从 ``batchGetUserinfo`` 条目里取可读名字（不同联系人字段不同）。"""
    full = entry.get("fullUserInfo")
    full = full if isinstance(full, dict) else {}
    for value in (entry.get("nickname"), entry.get("realName"), entry.get("name"),
                  full.get("name"), entry.get("acctid")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = _get_scoped_secret(name)
    return _truthy(raw, default) if raw is not None else default


def _env_int(name: str, default: int) -> int:
    raw = _get_scoped_secret(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        logger.warning("QWPad: %s=%r 不是数字，使用默认值 %s", name, raw, default)
        return default


class QWPadAdapter(BasePlatformAdapter):
    """企业微信 pad 协议适配器。"""

    def __init__(self, config, **kwargs: Any):
        super().__init__(config=config, platform=Platform(PLATFORM_NAME))
        extra = getattr(config, "extra", {}) or {}
        self.token = str(_get_scoped_secret("QWPAD_TOKEN") or extra.get("token")
                         or getattr(config, "token", "") or "").strip()
        self.api_base = str(_get_scoped_secret("QWPAD_API_BASE") or extra.get("api_base")
                            or DEFAULT_BASE_URL).strip().rstrip("/")
        self.require_mention = _env_flag("QWPAD_GROUP_REQUIRE_MENTION",
                                         _truthy(extra.get("require_mention"), True))
        self.quote_replies = _env_flag("QWPAD_QUOTE_REPLIES",
                                       _truthy(extra.get("quote_replies"), True))
        poll_wait = _get_scoped_secret("QWPAD_POLL_WAIT_SECONDS") or extra.get("poll_wait_seconds") or 25
        self.poll_wait_seconds = max(1, min(int(float(poll_wait)), 30))
        self.poll_limit = max(1, min(int(extra.get("poll_limit") or 50), 500))
        raw_limit = _get_scoped_secret("QWPAD_MAX_MESSAGE_LENGTH") or extra.get("max_message_length")
        self.max_message_length = int(raw_limit or MAX_MESSAGE_LENGTH)
        retain = _get_scoped_secret("QWPAD_RETAIN_EVENTS")
        if retain is None:
            retain = extra.get("retain_events")
        self.retention_policy = RetentionPolicy.from_value(retain)

        self._client: Optional[QWPadClient] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._cursor_store: Optional[CursorStore] = None
        self._retention: Optional[RetentionStore] = None
        self._cursor = 0
        self._dedupe = DedupeSet()
        self._self_id = ""
        self._self_nick = ""
        self._room_cache: dict[str, tuple[str, float]] = {}
        self._contact_cache: dict[str, tuple[str, float]] = {}
        self._replies: dict[str, dict[str, Any]] = {}
        # chat_id -> (monotonic 时间, 锚点)；Hermes 的普通回复不传 reply_to，
        # 靠它把本轮入站消息自动引用出去。
        self._pending_reply: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_status_check = 0.0

    @property
    def name(self) -> str:
        return "QWPad"

    # ── 生命周期 ────────────────────────────────────────────────────────────
    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.token:
            return self._fail("config_missing", "QWPAD_TOKEN 未配置", retryable=False)
        if not self._acquire_platform_lock(
                PLATFORM_NAME, token_fingerprint(self.token), "QWPad 企业微信账号"):
            return False

        self._client = QWPadClient(self.token, self.api_base)
        try:
            status = await self._client.get_login_status()
        except QWPadAuthError as exc:
            await self._close_client()
            return self._fail("auth_failed", f"TOKEN 无效或已失效：{exc}", retryable=False)
        except QWPadError as exc:
            await self._close_client()
            return self._fail("connect_failed", str(exc), retryable=True)

        self._self_id = str(status.get("userId") or "").strip()
        self._self_nick = str(status.get("nickname") or "").strip()
        error_code = int(status.get("errorCode") or 0)
        if error_code != 0:
            await self._close_client()
            return self._fail(
                "account_offline",
                f"账号不在线（errorCode={error_code} {status.get('errorMsg') or ''}），"
                "请先在 QWPad 侧完成扫码登录",
                retryable=True)

        self._cursor_store = CursorStore(self.token)
        self._cursor = self._cursor_store.load()
        self._retention = RetentionStore(
            self.token,
            max_bytes=_env_int("QWPAD_RETAIN_MAX_BYTES", 10 * 1024 * 1024),
            keep=_env_int("QWPAD_RETAIN_KEEP", 5),
            retention_days=_env_int("QWPAD_RETAIN_DAYS", 30),
        )
        self._running = True
        self._last_status_check = time.monotonic()
        self._poll_task = asyncio.create_task(self._poll_loop(), name=f"{PLATFORM_NAME}-poll")
        self._mark_connected()
        self._wire_plugin_handlers(None)
        logger.info("[QWPad] 已连接 %s（%s / %s），游标 %s",
                    self._self_nick or self._self_id, status.get("corpName") or "", self._self_id,
                    self._cursor)
        return True

    async def disconnect(self) -> None:
        self._running = False
        self._mark_disconnected()
        await cancel_task(self._poll_task)
        self._poll_task = None
        await self._close_client()
        with contextlib.suppress(Exception):
            self._release_platform_lock()

    async def _close_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    def _fail(self, code: str, message: str, *, retryable: bool) -> bool:
        self._set_fatal_error(code, message, retryable=retryable)
        logger.error("[QWPad] %s: %s", code, message)
        return False

    # ── 收消息 ──────────────────────────────────────────────────────────────
    async def _poll_loop(self) -> None:
        backoff = 1.0
        while self._running:
            client = self._client
            if client is None:
                return
            try:
                # 不在服务端按 type 过滤：15500 系统通知（好友申请、审批、抄送等）
                # 也要进来，由 classify_event 决定是进对话、进旁路日志还是丢弃。
                page = await client.fetch_events(
                    self._cursor, limit=self.poll_limit,
                    wait_seconds=self.poll_wait_seconds)
                for item in page.items:
                    await self._handle_event_item(item)
                    self._cursor = max(self._cursor, int(item.get("id") or 0))
                    self._save_cursor()
                if page.next_id > self._cursor:
                    self._cursor = page.next_id
                    self._save_cursor()
                backoff = 1.0
                if await self._status_check_due():
                    await self._check_account_status()
            except asyncio.CancelledError:
                raise
            except QWPadAuthError as exc:
                self._running = False
                self._set_fatal_error("auth_failed", f"TOKEN 失效：{exc}", retryable=False)
                await self._notify_fatal_error()
                return
            except QWPadError as exc:
                if not exc.retryable:
                    logger.error("[QWPad] 拉取事件失败（不可重试）：%s", exc)
                else:
                    logger.warning("[QWPad] 拉取事件失败，%.0fs 后重试：%s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except Exception as exc:  # noqa: BLE001 - 轮询循环不能死
                logger.warning("[QWPad] 轮询异常，%.0fs 后重试：%s", backoff, exc, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _save_cursor(self) -> None:
        if self._cursor_store is not None:
            self._cursor_store.save(self._cursor)

    async def _status_check_due(self) -> bool:
        return (time.monotonic() - self._last_status_check) >= STATUS_CHECK_INTERVAL_S

    async def _check_account_status(self) -> None:
        self._last_status_check = time.monotonic()
        client = self._client
        if client is None:
            return
        with contextlib.suppress(QWPadError):
            status = await client.get_login_status()
            if int(status.get("errorCode") or 0) != 0:
                logger.warning("[QWPad] 账号掉线：%s", status.get("errorMsg") or status.get("errorCode"))

    async def _handle_event_item(self, item: dict[str, Any]) -> None:
        decision = classify_event(item, self.retention_policy)
        if decision.action == "drop":
            logger.debug("[QWPad] 丢弃事件 id=%s kind=%s reason=%s",
                         item.get("id"), decision.kind, decision.reason)
            return
        if decision.action == "retain":
            self._retain_event(item, decision.kind)
            return

        inbox = parse_event(item, self_id=self._self_id, self_nick=self._self_nick)
        if inbox is None or inbox.is_self:
            return
        if inbox.message_id in self._dedupe:
            return
        self._dedupe.add(inbox.message_id)
        if inbox.chat_type == "group" and self.require_mention and not inbox.mentions_me:
            logger.debug("[QWPad] 群消息未 @ 我，跳过 room=%s sender=%s", inbox.chat_id, inbox.user_id)
            return
        await self._dispatch(inbox)

    def _retain_event(self, item: dict[str, Any], kind: str) -> None:
        """非聊天事件只进旁路日志，不调用 handle_message，因此不会回复。"""
        store = self._retention
        if store is None:
            return
        try:
            store.append(item, kind)
        except Exception as exc:  # noqa: BLE001 - 旁路留存不能影响对话链路
            logger.warning("[QWPad] 留存事件失败 id=%s kind=%s: %s", item.get("id"), kind, exc)

    async def _dispatch(self, inbox: InboundMessage) -> None:
        media_paths: list[str] = []
        media_types: list[str] = []
        refs = list(inbox.media_refs)
        if inbox.media:
            refs.insert(0, inbox.media)
        if self._client is not None:
            for ref in refs:
                if not ref.downloadable:
                    continue
                cached = await cache_media(self._client, ref)
                if cached:
                    path, media_type = cached
                    media_paths.append(path)
                    media_types.append(media_type)

        chat_name = await self._chat_name(inbox)
        source = self.build_source(
            chat_id=inbox.chat_id,
            chat_name=chat_name,
            chat_type=inbox.chat_type,
            user_id=inbox.user_id,
            user_name=inbox.user_name,
            message_id=inbox.message_id,
        )
        reply_text = reply_context(inbox)
        event = MessageEvent(
            text=inbox.text,
            message_type=_MESSAGE_TYPES.get(inbox.media.kind if inbox.media else "text", MessageType.TEXT),
            user_id=inbox.user_id,
            user_name=inbox.user_name,
            source=source,
            raw_message=inbox.raw,
            message_id=inbox.message_id,
            media_urls=media_paths,
            media_types=media_types,
            reply_to_message_id=None,
            reply_to_text=reply_text,
            reply_to_author_name=(inbox.reply or {}).get("showName") if inbox.reply else None,
            timestamp=(datetime.datetime.fromtimestamp(inbox.timestamp)
                       if inbox.timestamp else datetime.datetime.now()),
            # 不要往 metadata 塞值：Hermes 原生准入的 metadata 契约目前只放行 WhatsApp 的专用键
            # （gateway/session_envelope.py:_native_metadata），其它平台带任何 metadata 都会被
            # 判 invalid_params(metadata)，消息不落库、只回 120 字符拒绝提示。
        )
        if self.quote_replies and inbox.chat_type:
            self._remember_reply_anchor(inbox)
        logger.info("[QWPad] 收到%s消息 room=%s user=%s type=%s len=%s",
                    "群" if inbox.chat_type == "group" else "私",
                    inbox.chat_id, inbox.user_name or inbox.user_id, inbox.msg_name, len(inbox.text or ""))
        await self.handle_message(event)

    def _remember_reply_anchor(self, inbox: InboundMessage) -> None:
        """存下引用一条消息所需的字段（企业微信 reply 需要原文结构）。

        ``msgData`` 必须原样复制原消息正文（图片/文件引用也靠它），不能只塞一个
        ``content``；``msgUniqueIdentifier`` 非空是 QWPad 判定引用生效的条件。
        """
        structured = ((inbox.raw or {}).get("message") or {}).get("structured") or {}
        data = structured.get("msgData")
        cached = self._contact_cache.get(inbox.user_id or inbox.chat_id)
        show_name = (cached[0] if cached else "") or inbox.user_name or inbox.user_id
        original_text = data.get("content") if isinstance(data, dict) else ""
        if isinstance(original_text, str):
            # kRichText 的 content 可能带 HTML 片段；引用气泡的预览要纯文本。
            original_text = strip_html(original_text)
        if not isinstance(original_text, str) or not original_text.strip():
            original_text = inbox.text or ""
        anchor = {
            "msgUniqueIdentifier": inbox.message_id,
            "showName": show_name,
            "timeStamp": inbox.timestamp,
            "type": inbox.msg_type,
            "userId": inbox.user_id,
            "msgData": data if isinstance(data, dict) else {"content": inbox.text or ""},
            # 只用于判断原消息是否有可引用的文本；正文不再拼接原文。
            "text": original_text,
        }
        while len(self._replies) > 500:
            self._replies.pop(next(iter(self._replies)), None)
        self._replies[inbox.message_id] = anchor
        self._pending_reply[inbox.chat_id] = (time.monotonic(), anchor)
        while len(self._pending_reply) > 200:
            self._pending_reply.pop(next(iter(self._pending_reply)), None)

    def _quote_anchor(self, chat_id: str, reply_to: Optional[str],
                      metadata: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """取本次发送要引用的原消息。

        Hermes 的普通回复不会传 ``reply_to``；此时用本 chat 最近一条入站消息的锚点。
        中间状态消息（``_interim_send``）不消耗锚点，最终回复仍能引用；超过
        ``QUOTE_WINDOW_S`` 的锚点视为过期（避免 cron/通知误引用旧消息）。
        """
        if not self.quote_replies:
            return None
        chat_key = str(chat_id)
        anchor = self._replies.get(reply_to) if reply_to else None
        if anchor is None:
            pending = self._pending_reply.get(chat_key)
            if pending:
                created, stored = pending
                if time.monotonic() - created <= QUOTE_WINDOW_S:
                    anchor = stored
                else:
                    self._pending_reply.pop(chat_key, None)
        if anchor is not None and not (metadata or {}).get("_interim_send"):
            self._pending_reply.pop(chat_key, None)
        return anchor

    async def _chat_name(self, inbox: InboundMessage) -> str:
        if inbox.chat_type != "group":
            if inbox.user_name and inbox.user_name != inbox.user_id:
                return inbox.user_name
            cached = self._contact_cache.get(inbox.chat_id)
            if cached and time.monotonic() - cached[1] < ROOM_NAME_TTL_S:
                return cached[0]
            client = self._client
            if client is not None:
                try:
                    data = await asyncio.wait_for(
                        client.batch_get_userinfo([inbox.chat_id]), timeout=5.0)
                    for entry in data.get("contactList") or []:
                        name = _contact_display_name(entry)
                        if name:
                            self._contact_cache[inbox.chat_id] = (name, time.monotonic())
                            return name
                except Exception:  # noqa: BLE001 - 昵称只是装饰
                    logger.debug("[QWPad] 私聊昵称查询失败 user=%s", inbox.chat_id, exc_info=True)
            return inbox.user_name or inbox.chat_id
        cached = self._room_cache.get(inbox.chat_id)
        if cached and time.monotonic() - cached[1] < ROOM_NAME_TTL_S:
            return cached[0]
        client = self._client
        if client is None:
            return inbox.chat_id
        try:
            detail = await asyncio.wait_for(client.get_room_detail(inbox.chat_id), timeout=10.0)
        except Exception:  # noqa: BLE001 - 群名只是装饰
            return cached[0] if cached else inbox.chat_id
        name = str(detail.get("roomName") or detail.get("name") or "").strip() or inbox.chat_id
        self._room_cache[inbox.chat_id] = (name, time.monotonic())
        return name

    # ── 发消息 ──────────────────────────────────────────────────────────────
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[dict[str, Any]] = None) -> SendResult:
        if self._client is None or not self.is_connected:
            return SendResult(success=False, error="Not connected")
        anchor = self._quote_anchor(str(chat_id), reply_to, metadata)
        if anchor and anchor.get("type") in (0, 2) and (anchor.get("text") or "").strip():
            result = await send_quoted_text(
                self._client, str(chat_id), content, reply=anchor,
                limit=self.max_message_length)
        else:
            # 非文本原消息的引用渲染尚未验证；先退化成普通文本，避免发出异常气泡。
            result = await send_text(self._client, str(chat_id), content,
                                     limit=self.max_message_length)
        if not result.success and anchor:
            logger.warning("[QWPad] 带引用发送失败，改为不引用重试：%s", result.error)
            result = await send_text(self._client, str(chat_id), content, limit=self.max_message_length)
        return result

    async def send_typing(self, chat_id: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """企业微信 pad 协议没有「正在输入」状态 —— 空实现。"""

    async def send_image(self, chat_id: str, image_url: str, caption: Optional[str] = None,
                         reply_to: Optional[str] = None,
                         metadata: Optional[dict[str, Any]] = None) -> SendResult:
        local = await self._localize_url(image_url)
        if not local:
            return SendResult(success=False, error="image download failed")
        try:
            return await send_media(self._client, str(chat_id), local, caption=caption,
                                    reply=self._quote_anchor(str(chat_id), reply_to, metadata))
        finally:
            with contextlib.suppress(OSError):
                if local.startswith(tempfile.gettempdir()):
                    os.unlink(local)

    async def send_image_file(self, chat_id: str, image_path: str, caption: Optional[str] = None,
                              reply_to: Optional[str] = None,
                              metadata: Optional[dict[str, Any]] = None, **kwargs: Any) -> SendResult:
        return await send_media(self._client, str(chat_id), image_path, caption=caption,
                                reply=self._quote_anchor(str(chat_id), reply_to, metadata))

    async def send_voice(self, chat_id: str, audio_path: str, caption: Optional[str] = None,
                         reply_to: Optional[str] = None,
                         metadata: Optional[dict[str, Any]] = None, **kwargs: Any) -> SendResult:
        return await send_voice(self._client, str(chat_id), audio_path, caption=caption,
                                reply=self._quote_anchor(str(chat_id), reply_to, metadata))

    async def send_document(self, chat_id: str, file_path: str, caption: Optional[str] = None,
                            file_name: Optional[str] = None, reply_to: Optional[str] = None,
                            metadata: Optional[dict[str, Any]] = None, **kwargs: Any) -> SendResult:
        return await send_media(self._client, str(chat_id), file_path, caption=caption,
                                reply=self._quote_anchor(str(chat_id), reply_to, metadata),
                                force_document=True)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """群返回群名/人数；私聊返回联系人昵称。"""
        chat_id = str(chat_id)
        client = self._client
        if client is None:
            return {"name": chat_id, "type": "dm", "chat_id": chat_id}
        cached = self._room_cache.get(chat_id)
        if cached and time.monotonic() - cached[1] < ROOM_NAME_TTL_S:
            return {"name": cached[0], "type": "group", "chat_id": chat_id}
        try:
            detail = await asyncio.wait_for(client.get_room_detail(chat_id), timeout=10.0)
        except Exception:  # noqa: BLE001
            detail = {}
        if detail.get("roomName") or detail.get("name"):
            name = str(detail.get("roomName") or detail.get("name"))
            self._room_cache[chat_id] = (name, time.monotonic())
            return {"name": name, "type": "group", "chat_id": chat_id,
                    "member_count": len(detail.get("memberList") or [])}
        contact = self._contact_cache.get(chat_id)
        if contact and time.monotonic() - contact[1] < ROOM_NAME_TTL_S:
            return {"name": contact[0], "type": "dm", "chat_id": chat_id}
        with contextlib.suppress(Exception):
            data = await asyncio.wait_for(client.batch_get_userinfo([chat_id]), timeout=10.0)
            for entry in data.get("contactList") or []:
                name = _contact_display_name(entry)
                if name:
                    self._contact_cache[chat_id] = (name, time.monotonic())
                    return {"name": name, "type": "dm", "chat_id": chat_id}
        return {"name": chat_id, "type": "dm", "chat_id": chat_id}

    async def _localize_url(self, url: str) -> Optional[str]:
        """``file://`` 直接返回路径；http(s) 下载到临时文件。"""
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return url2pathname(parsed.path) if Path(url2pathname(parsed.path)).is_file() else None
        if parsed.scheme not in ("http", "https"):
            return None
        suffix = Path(parsed.path).suffix or ".jpg"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                async with http.stream("GET", url) as response:
                    response.raise_for_status()
                    total = 0
                    fd, path = tempfile.mkstemp(prefix="qwpad-img-", suffix=suffix)
                    with os.fdopen(fd, "wb") as fh:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_URL_DOWNLOAD_BYTES:
                                raise ValueError("image too large")
                            fh.write(chunk)
                    return path
        except Exception as exc:  # noqa: BLE001
            logger.warning("[QWPad] 下载图片失败 %s: %s", url.split("?")[0], exc)
            return None
