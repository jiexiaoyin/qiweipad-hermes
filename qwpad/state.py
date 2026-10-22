"""游标与去重状态：按 profile + TOKEN 指纹落盘，重启不重放旧消息。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

DEDUPE_CAPACITY = 2000


def token_fingerprint(token: str) -> str:
    """TOKEN 的短指纹——只用于文件名，绝不明文落盘。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:16]


def state_dir() -> Path:
    """当前 profile 的 QWPad 状态目录（调用点必须在 profile scope 内）。"""
    return Path(get_hermes_home()) / "state" / "qwpad"


class CursorStore:
    """``{lastEventId, updatedAt}`` 的小状态文件，原子写。"""

    def __init__(self, token: str, directory: Path | None = None):
        self.path = (directory or state_dir()) / f"events-{token_fingerprint(token)}.json"
        self._last: int = 0

    def load(self) -> int:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as exc:
            logger.warning("QWPad: 读取游标失败 %s: %s", self.path, exc)
            return 0
        try:
            self._last = int(raw.get("lastEventId") or 0)
        except (TypeError, ValueError):
            self._last = 0
        return self._last

    def save(self, event_id: int) -> None:
        event_id = int(event_id or 0)
        if event_id <= 0 or event_id == self._last:
            return
        self._last = event_id
        payload = {"lastEventId": event_id, "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".cursor-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("QWPad: 写游标失败 %s: %s", self.path, exc)


class DedupeSet:
    """有界的「已处理消息 id」集合（LRU 淘汰）。"""

    def __init__(self, capacity: int = DEDUPE_CAPACITY):
        self._items: OrderedDict[str, None] = OrderedDict()
        self.capacity = capacity

    def __contains__(self, key: Any) -> bool:
        return key in self._items

    def add(self, key: Any) -> None:
        if not key:
            return
        self._items[key] = None
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def seed(self, keys: Iterable[Any]) -> None:
        for key in keys:
            self.add(key)
