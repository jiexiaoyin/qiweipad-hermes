"""非聊天事件的旁路留存。

Hermes 的对话链路只接受群聊/私聊（见 :func:`inbound.parse_event` 的 category 硬闸）。
好友申请、审批、抄送、账号状态这类事件不适合进对话，但值得落盘，用于后续 CRM 同步、
审批提醒和运维观测。这里只写本地 JSONL，**绝不调用** ``handle_message``，因此不会回复、
不会污染上下文，也不受 Hermes 入站 metadata 契约影响。

保留策略由 ``QWPAD_RETAIN_EVENTS`` 控制：

- 未设置 → 默认集（见 :data:`DEFAULT_RETAIN`）
- ``off`` / 空 / ``0`` → 关闭留存（非聊天事件直接丢弃）
- ``all`` → 除明确噪音外全部留存
- ``friend_apply,approval,...`` → 只留指定类
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .client import MSG_APP_TEXT_CARD
from .inbound import CHAT_CATEGORY, EVENT_TYPE_CHAT, strip_html
from .state import state_dir, token_fingerprint

logger = logging.getLogger(__name__)

# 默认保留的业务类：好友申请/变化、审批、抄送、关系时间线、应用卡片、账号元数据。
DEFAULT_RETAIN = frozenset({
    "friend_apply",
    "friend_change",
    "approval",
    "cc",
    "relationship",
    "app_card",
    "account_meta",
})

KNOWN_KINDS = DEFAULT_RETAIN | {"other"}

# msgType → 留存类。2357 与 2132 都属于「好友申请」，前者带申请人资料，后者只是信号。
RETAIN_MSG_TYPES = {
    2357: "friend_apply",
    2132: "friend_apply",
    2131: "friend_change",
    145: "approval",
    561: "cc",
    1011: "relationship",
}

# 明确噪音：已读回执、未知空通知。即使 QWPAD_RETAIN_EVENTS=all 也不留。
DROP_MSG_TYPES = {2001}
DROP_EVENT_TYPES = {2180}

# 账号级事件：只有事件元数据，没有可解析正文。
META_ONLY_EVENT_TYPES = {11004}

# 这些 msgType 即使 display.category=chat 也不是人类对话，必须留存而不回复。
NON_CHAT_MSG_TYPES = {MSG_APP_TEXT_CARD}


@dataclass(frozen=True)
class EventClass:
    """一条事件的最终归属。"""

    action: str          # chat | retain | drop
    kind: str = ""       # retention 类；chat/drop 时为空
    reason: str = ""     # drop 原因 / retain 类说明


@dataclass(frozen=True)
class RetentionPolicy:
    """``QWPAD_RETAIN_EVENTS`` 的解析结果。"""

    kinds: frozenset[str] = DEFAULT_RETAIN
    retain_all: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "RetentionPolicy":
        if value is None:
            return cls()
        text = str(value).strip().lower()
        if text in ("", "off", "0", "false", "no", "none"):
            return cls(frozenset())
        if text == "all":
            return cls(frozenset(), retain_all=True)
        kinds = frozenset(part.strip() for part in text.split(",") if part.strip())
        unknown = kinds - KNOWN_KINDS
        if unknown:
            logger.warning("QWPad: 未知的留存类 %s（已知：%s）",
                           ",".join(sorted(unknown)), ",".join(sorted(KNOWN_KINDS)))
        return cls(kinds & KNOWN_KINDS)

    def allows(self, kind: str) -> bool:
        if kind in self.kinds:
            return True
        return self.retain_all and kind in KNOWN_KINDS


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _retained_or_drop(kind: str, policy: RetentionPolicy, reason: str = "") -> EventClass:
    if policy.allows(kind):
        return EventClass("retain", kind, reason)
    return EventClass("drop", kind, f"{kind}_disabled")


def classify_event(item: Any, policy: RetentionPolicy | None = None) -> EventClass:
    """决定一条 ``/events`` 条目是进对话、进旁路日志，还是丢弃。

    这个函数不修改 ``item``，也不发网络请求；适配器在 ``parse_event`` 之前调用它，
    所以非聊天事件不会先被 category 硬闸丢掉。
    """
    policy = policy or RetentionPolicy()
    if not isinstance(item, dict):
        return EventClass("drop", reason="not_dict")

    message = _as_dict(item.get("message"))
    structured = _as_dict(message.get("structured"))
    display = _as_dict(item.get("display"))
    outer_type = _int(item.get("type"))
    msg_type = _int(message.get("msgType") or structured.get("msgType"))
    category = _str(display.get("category")).lower()

    # 噪音优先：即使策略是 all 也不留。
    if msg_type in DROP_MSG_TYPES:
        return EventClass("drop", reason="read_receipt")
    if outer_type in DROP_EVENT_TYPES:
        return EventClass("drop", reason="unknown_empty")

    # 应用卡片不是人类对话。无论 category 是什么，都只留存、不回复。
    if msg_type in NON_CHAT_MSG_TYPES:
        return _retained_or_drop("app_card", policy, "app_card")

    kind = RETAIN_MSG_TYPES.get(msg_type)
    if kind:
        return _retained_or_drop(kind, policy, kind)

    if outer_type in META_ONLY_EVENT_TYPES:
        return _retained_or_drop("account_meta", policy, "account_meta")

    if category == CHAT_CATEGORY and (not outer_type or outer_type == EVENT_TYPE_CHAT):
        return EventClass("chat")

    if category != CHAT_CATEGORY:
        return _retained_or_drop("other", policy, category or "missing_category")

    return EventClass("drop", reason="unclassified")


def _decode_raw(msg_data: dict[str, Any], message: dict[str, Any]) -> str:
    decoded = _as_dict(msg_data.get("decodedRawData")).get("text")
    if isinstance(decoded, str) and decoded:
        return decoded
    raw = msg_data.get("base64RawData") or message.get("rawBodyBase64") or ""
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        return base64.b64decode(raw).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _printable(text: str) -> str:
    """去掉 protobuf 长度前缀等不可打印字节，保留可读标题。"""
    cleaned = "".join(ch for ch in text if ch == "\n" or 32 <= ord(ch) != 127)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def extract_card(text: str) -> Optional[dict[str, Any]]:
    """从 FunView 原始正文里抠出内嵌的卡片 JSON。"""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        card, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return card if isinstance(card, dict) else None


def build_record(item: dict[str, Any], kind: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """把一条事件整理成旁路日志记录；``account_meta`` 只留元数据。"""
    message = _as_dict(item.get("message"))
    structured = _as_dict(message.get("structured"))
    display = _as_dict(item.get("display"))
    msg_type = _int(message.get("msgType") or structured.get("msgType"))
    message_type = _as_dict(display.get("messageType"))
    msg_data = _as_dict(structured.get("msgData")) or _as_dict(message.get("msgData"))

    timestamp = _int(structured.get("timestamp")) or int(now or time.time())
    record: dict[str, Any] = {
        "eventId": _int(item.get("id")),
        "ts": timestamp,
        "createdAt": _str(item.get("createdAt")),
        "type": _int(item.get("type")),
        "category": _str(display.get("category")),
        "msgType": msg_type,
        "nativeName": _str(message_type.get("nativeName")),
        "kind": kind,
        "senderId": _str(display.get("senderId")) or _str(structured.get("senderId")),
        "senderName": _str(display.get("senderName")) or _str(structured.get("senderName")),
        "roomId": _str(display.get("roomId")) or _str(structured.get("fromRoomId")),
        "title": _str(display.get("title")),
    }

    if kind == "account_meta":
        # 账号级通知的正文是不透明的 protobuf，只留事件元数据。
        record["metaOnly"] = True
        record["text"] = ""
        return record

    decoded = _decode_raw(msg_data, message)
    card = extract_card(decoded)
    text = _str(display.get("text"))
    if card:
        # FunView 卡片的人类可读标题在 main_title.title；正文 JSON 单独放 card 字段。
        text = text or _str(_as_dict(card.get("main_title")).get("title"))
    if not text:
        text = _printable(decoded.split("{", 1)[0])
    record["text"] = strip_html(text)
    record["card"] = card
    record["data"] = msg_data or None
    record["raw"] = item
    return record


class RetentionStore:
    """按 TOKEN 指纹落盘的 JSONL 旁路日志（自动轮转 + 过期清理）。"""

    def __init__(
        self,
        token: str,
        *,
        directory: Path | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        keep: int = 5,
        retention_days: int = 30,
    ):
        self.path = (directory or state_dir()) / f"retained-{token_fingerprint(token)}.jsonl"
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.keep = max(1, int(keep))
        self.retention_days = max(0, int(retention_days))
        self._last_prune = 0.0

    # ── 写 ──────────────────────────────────────────────────────────────────
    def append(self, item: dict[str, Any], kind: str) -> dict[str, Any]:
        record = build_record(item, kind)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._append_line(line)
        self._maybe_prune()
        return record

    def _append_line(self, line: str) -> None:
        payload = line.encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.path.exists() and self.path.stat().st_size + len(payload) > self.max_bytes:
                self._rotate()
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as fh:
                fh.write(payload)
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)
        except OSError as exc:
            logger.warning("QWPad: 写留存事件失败 %s: %s", self.path, exc)

    def _rotate(self) -> None:
        for index in range(self.keep - 1, 0, -1):
            src = self._rotated(index)
            if src.exists():
                os.replace(src, self._rotated(index + 1))
        if self.path.exists():
            os.replace(self.path, self._rotated(1))

    def _rotated(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")

    # ── 过期清理 ────────────────────────────────────────────────────────────
    def _maybe_prune(self) -> None:
        if self.retention_days <= 0:
            return
        now = time.time()
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        cutoff = now - self.retention_days * 86400
        self._prune_file(self.path, cutoff)
        for index in range(1, self.keep + 1):
            rotated = self._rotated(index)
            with contextlib.suppress(OSError):
                if rotated.exists() and rotated.stat().st_mtime < cutoff:
                    rotated.unlink()

    def _prune_file(self, path: Path, cutoff: float) -> None:
        if not path.exists():
            return
        kept: list[str] = []
        changed = False
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    changed = True
                    continue
                if _int(record.get("ts")) >= cutoff:
                    kept.append(line)
                else:
                    changed = True
            if not changed:
                return
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".retained-", suffix=".jsonl")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + ("\n" if kept else ""))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("QWPad: 清理留存日志失败 %s: %s", path, exc)

    # ── 读（排查/未来工具用） ───────────────────────────────────────────────
    def read(self, *, kind: str = "", since: float = 0, limit: int = 50) -> list[dict[str, Any]]:
        files = [self._rotated(i) for i in range(self.keep, 0, -1)] + [self.path]
        records: list[dict[str, Any]] = []
        for path in files:
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if kind and record.get("kind") != kind:
                        continue
                    if since and _int(record.get("ts")) < since:
                        continue
                    records.append(record)
            except OSError as exc:
                logger.warning("QWPad: 读取留存日志失败 %s: %s", path, exc)
        records.sort(key=lambda rec: _int(rec.get("ts")))
        return records[-max(1, int(limit)):]
