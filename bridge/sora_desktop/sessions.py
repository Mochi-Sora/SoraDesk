"""Read SoraAgent's on-disk conversation store as a desktop session catalog.

SoraAgent persists one JSON file per ``session_key`` and deliberately stores no
per-message timestamps or titles.  The bridge therefore derives presentation
metadata: the title comes from the first user message, timestamps from the
file's modification time, and attachment records from the media sidecar index.
Nothing here writes to SoraAgent's store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sora_agent.channels.sessions import SessionStore
from sora_agent.providers import Message

from .media import UploadStore
from .media import public as public_attachment

CHANNEL = "desktop"
SESSION_PREFIX = f"{CHANNEL}:"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_TITLE_CHARS = 80


@dataclass(frozen=True)
class SessionRecord:
    """One persisted desktop conversation."""

    id: str
    key: str
    path: Path
    created_at: int
    updated_at: int
    messages: list[Message]


class SessionCatalog:
    """List and read the ``desktop:`` conversations in a ``SessionStore``."""

    def __init__(self, store: SessionStore, uploads: UploadStore | None = None) -> None:
        self.store = store
        self.uploads = uploads

    @property
    def root(self) -> Path:
        return self.store.root

    def session_key(self, session_id: str) -> str | None:
        clean = session_id.strip()
        if not _SAFE_ID.match(clean):
            return None
        return SESSION_PREFIX + clean

    def get(self, session_id: str) -> SessionRecord | None:
        key = self.session_key(session_id)
        if key is None:
            return None
        path = self.store.path(key)
        return self._read(key, path)

    def meta(self, session_id: str) -> dict[str, Any] | None:
        record = self.get(session_id)
        return self._meta(record) if record is not None else None

    def list(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        capped = max(1, min(200, int(limit)))
        offset = _decode_cursor(cursor)
        records = list(self._scan())
        window = records[offset : offset + capped]
        next_offset = offset + len(window)
        result: dict[str, Any] = {
            "sessions": [self._meta(record) for record in window],
        }
        if next_offset < len(records):
            result["next_cursor"] = _encode_cursor(next_offset)
        else:
            result["next_cursor"] = None
        return result

    def history(
        self,
        session_id: str,
        *,
        limit: int = 100,
        before: str | None = None,
    ) -> dict[str, Any] | None:
        record = self.get(session_id)
        if record is None:
            return None
        visible = [
            message
            for message in record.messages
            if message.role == "user"
            or (message.role == "assistant" and message.content.strip())
        ]
        capped = max(1, min(500, int(limit)))
        if before:
            # The cursor is an offset into the visible transcript; the bridge
            # only returns a trailing window, so pagination walks backwards.
            end = max(0, len(visible) - _decode_cursor(before))
        else:
            end = len(visible)
        start = max(0, end - capped)
        window = visible[start:end]
        attachments = self._attachment_map(record, visible, window, start)
        messages = []
        for index, message in enumerate(window, start=start):
            entry: dict[str, Any] = {
                "message_id": _message_id(record.key, index),
                "role": message.role,
                "text": message.content,
                "ts": _timestamp(record, index, len(visible)),
            }
            records = attachments.get(index)
            if records:
                entry["attachments"] = records
            messages.append(entry)
        result: dict[str, Any] = {
            "session_id": record.id,
            "messages": messages,
            "next_cursor": (
                _encode_cursor(len(visible) - start)
                if start > 0
                else None
            ),
        }
        return result

    # -- scanning --------------------------------------------------------------

    def _scan(self) -> Iterator[SessionRecord]:
        root = self.root
        if not root.is_dir():
            return
        records: list[SessionRecord] = []
        for path in root.glob("*.json"):
            if path.name.startswith("."):
                continue
            record = self._read_path(path)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        yield from records

    def _read(self, key: str, path: Path) -> SessionRecord | None:
        return self._read_path(path, expected_key=key)

    def _read_path(self, path: Path, expected_key: str | None = None) -> SessionRecord | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        key = payload.get("session_key")
        if not isinstance(key, str) or not key.startswith(SESSION_PREFIX):
            return None
        if expected_key is not None and key != expected_key:
            return None
        session_id = key[len(SESSION_PREFIX) :]
        if not session_id or not _SAFE_ID.match(session_id):
            return None
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return SessionRecord(
            id=session_id,
            key=key,
            path=path,
            created_at=_created_ms(stat),
            updated_at=int(stat.st_mtime * 1000),
            messages=self.store.load(key),
        )

    # -- presentation ----------------------------------------------------------

    @staticmethod
    def _meta(record: SessionRecord) -> dict[str, Any]:
        visible = [
            message
            for message in record.messages
            if message.role == "user"
            or (message.role == "assistant" and message.content.strip())
        ]
        return {
            "id": record.id,
            "title": _title(visible),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "message_count": len(visible),
            "source": CHANNEL,
        }

    def _attachment_map(
        self,
        record: SessionRecord,
        visible: list[Message],
        window: list[Message],
        start: int,
    ) -> dict[int, list[dict[str, Any]]]:
        if self.uploads is None:
            return {}
        entries = self.uploads.attachments_for_session(record.key)
        if not entries:
            return {}
        # Everything uploaded for one chat.send shares a batch id, so history
        # can attach every file to that user message instead of one per entry.
        groups: list[list[dict[str, Any]]] = []
        by_batch: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            batch = entry.get("batch_id")
            if isinstance(batch, str) and batch:
                group = by_batch.get(batch)
                if group is None:
                    group = []
                    by_batch[batch] = group
                    groups.append(group)
                group.append(entry)
            else:
                groups.append([entry])
        mapped: dict[int, list[dict[str, Any]]] = {}
        used: set[int] = set()
        for offset, message in enumerate(window):
            if message.role != "user":
                continue
            index = start + offset
            match = next(
                (
                    position
                    for position, group in enumerate(groups)
                    if position not in used and _group_matches(group, message.content)
                ),
                None,
            )
            if match is None:
                continue
            used.add(match)
            mapped[index] = [public_attachment(entry) for entry in groups[match]]
        return mapped


def _title(messages: list[Message]) -> str:
    for message in messages:
        if message.role != "user":
            continue
        text = " ".join(message.content.split())
        if text:
            if len(text) > _TITLE_CHARS:
                return text[: _TITLE_CHARS - 1].rstrip() + "…"
            return text
    return "New conversation"


def _message_id(key: str, index: int) -> str:
    digest = hashlib.sha256(f"{key}:{index}".encode()).hexdigest()[:16]
    return f"h-{digest}"


def _timestamp(record: SessionRecord, index: int, count: int) -> int:
    if count <= 1:
        return record.updated_at
    spaced = record.updated_at - (count - 1 - index) * 1000
    return max(record.created_at, spaced)


def _created_ms(stat: Any) -> int:
    birth = getattr(stat, "st_birthtime", None)
    seconds = birth if isinstance(birth, (int, float)) and birth > 0 else stat.st_mtime
    return int(seconds * 1000)


def _encode_cursor(offset: int) -> str:
    raw = str(max(0, int(offset))).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.isdigit():
        return int(cursor)
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        return max(0, int(raw.decode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return 0


def _looks_synthetic(text: str) -> bool:
    folded = text.strip().lower()
    return folded.startswith(("[attached file", "please review the attached"))


def _group_matches(group: list[dict[str, Any]], text: str) -> bool:
    for entry in group:
        recorded = entry.get("text")
        if isinstance(recorded, str) and recorded.strip():
            if recorded == text:
                return True
        elif not text.strip() or _looks_synthetic(text):
            return True
    return False
