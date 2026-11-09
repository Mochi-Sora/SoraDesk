"""Upload storage and transcript-scoped media fetch for the bridge.

Uploads are owned by the device token that sent them and expire if a turn never
consumes them. Consumed files are indexed by session so ``media.fetch`` can
rebuild previews after a reload without exposing arbitrary filesystem paths.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import mimetypes
import re
import secrets
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MEDIA_ROOT, ensure_dir
from .protocol import ERROR_MEDIA_GONE, ERROR_NO_ATTACHMENT, ERROR_TOO_LARGE

UPLOAD_TTL_S = 15 * 60.0
UPLOAD_KINDS = frozenset({"image", "audio", "video", "file"})
_SAFE_EXTENSION = re.compile(r"^\.[a-z0-9]{1,10}$")


class UploadError(RuntimeError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass
class UploadRecord:
    attachment_id: str
    path: Path
    name: str
    mime: str
    kind: str
    size: int
    owner_token: str
    created_at: float
    duration_ms: int | None = None


class UploadStore:
    """Pending uploads plus the durable per-session attachment index."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        index_path: Path | None = None,
        max_upload_bytes: int = 25 * 1024 * 1024,
        max_fetch_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.root = (root or MEDIA_ROOT).expanduser()
        self.index_path = index_path or (self.root / "index.json")
        self.max_upload_bytes = max(0, int(max_upload_bytes))
        self.max_fetch_bytes = max(0, int(max_fetch_bytes))
        self._lock = threading.Lock()
        self._pending: dict[str, UploadRecord] = {}
        ensure_dir(self.root)

    # -- uploads ---------------------------------------------------------------

    def store(
        self,
        owner_token: str,
        *,
        kind: str,
        data: str,
        name: str | None,
        mime: str | None,
        duration_ms: int | None,
    ) -> UploadRecord:
        if kind not in UPLOAD_KINDS:
            raise UploadError(
                "bad_request", "kind must be one of image, audio, video, file"
            )
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UploadError("bad_request", "data is not valid base64") from exc
        if len(raw) > self.max_upload_bytes:
            raise UploadError(
                ERROR_TOO_LARGE,
                f"upload exceeds the {self.max_upload_bytes // (1024 * 1024)} MB limit",
                {"limit_bytes": self.max_upload_bytes},
            )
        display_name = _safe_name(name)
        content_type = (mime or "").strip() or "application/octet-stream"
        path = self.root / f"{uuid.uuid4().hex}{_safe_extension(display_name, content_type)}"
        try:
            with path.open("xb") as handle:
                handle.write(raw)
            with contextlib.suppress(OSError):
                path.chmod(0o600)
        except OSError as exc:
            raise UploadError("internal", "failed to store the upload") from exc

        record = UploadRecord(
            attachment_id=f"att-{uuid.uuid4().hex[:12]}",
            path=path,
            name=display_name,
            mime=content_type,
            kind=kind,
            size=len(raw),
            owner_token=owner_token,
            created_at=time.monotonic(),
            duration_ms=duration_ms if duration_ms is not None and duration_ms >= 0 else None,
        )
        with self._lock:
            self._pending[record.attachment_id] = record
        return record

    def pending(self, attachment_id: str, owner_token: str) -> UploadRecord | None:
        with self._lock:
            record = self._pending.get(attachment_id)
        if record is None or record.owner_token != owner_token:
            return None
        return record

    def consume(self, attachment_id: str, owner_token: str) -> UploadRecord | None:
        with self._lock:
            record = self._pending.get(attachment_id)
            if record is None or record.owner_token != owner_token:
                return None
            self._pending.pop(attachment_id, None)
        return record

    def prune(self) -> None:
        cutoff = time.monotonic() - UPLOAD_TTL_S
        with self._lock:
            expired = [
                record for record in self._pending.values() if record.created_at <= cutoff
            ]
            for record in expired:
                self._pending.pop(record.attachment_id, None)
        for record in expired:
            _unlink(record.path)

    def release(self, owner_token: str) -> None:
        with self._lock:
            released = [
                record
                for record in self._pending.values()
                if record.owner_token == owner_token
            ]
            for record in released:
                self._pending.pop(record.attachment_id, None)
        for record in released:
            _unlink(record.path)

    def release_all(self) -> None:
        with self._lock:
            released = list(self._pending.values())
            self._pending.clear()
        for record in released:
            _unlink(record.path)

    # -- durable index ---------------------------------------------------------

    def remember(
        self,
        record: UploadRecord,
        session_id: str,
        text: str,
        batch_id: str | None = None,
    ) -> None:
        """Persist a consumed upload so history and ``media.fetch`` can find it."""
        entry = {
            "attachment_id": record.attachment_id,
            "session_id": session_id,
            "text": text,
            "batch_id": batch_id or record.attachment_id,
            "path": str(record.path),
            "name": record.name,
            "mime": record.mime,
            "kind": record.kind,
            "size": record.size,
            "duration_ms": record.duration_ms,
            "created_at": time.time(),
        }
        with self._lock:
            index = self._read_index()
            entries = index.setdefault("attachments", [])
            if not isinstance(entries, list):
                entries = []
                index["attachments"] = entries
            entries.append(entry)
            self._write_index(index)

    def attachments_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._entries()
        return [
            entry
            for entry in entries
            if entry.get("session_id") == session_id and _entry_path(entry, self.root) is not None
        ]

    def fetch(self, session_id: str, attachment_id: str) -> tuple[dict[str, Any], bytes]:
        with self._lock:
            entries = self._entries()
        match = next(
            (
                entry
                for entry in reversed(entries)
                if entry.get("session_id") == session_id
                and entry.get("attachment_id") == attachment_id
            ),
            None,
        )
        if match is None:
            raise UploadError(
                ERROR_NO_ATTACHMENT,
                f"attachment {attachment_id} is not referenced by this session",
            )
        path = _entry_path(match, self.root)
        if path is None or not path.is_file():
            raise UploadError(ERROR_MEDIA_GONE, "the stored media file is no longer available")
        try:
            size = path.stat().st_size
            if size > self.max_fetch_bytes:
                raise UploadError(
                    ERROR_TOO_LARGE,
                    "stored file exceeds the media.fetch limit",
                    {"limit_bytes": self.max_fetch_bytes},
                )
            data = path.read_bytes()
        except OSError as exc:
            raise UploadError(
                ERROR_MEDIA_GONE, "the stored media file is no longer available"
            ) from exc
        return match, data

    def _entries(self) -> list[dict[str, Any]]:
        index = self._read_index()
        raw = index.get("attachments")
        if not isinstance(raw, list):
            return []
        return [entry for entry in raw if isinstance(entry, dict)]

    def _read_index(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "attachments": []}
        if not isinstance(raw, dict):
            return {"version": 1, "attachments": []}
        raw.setdefault("attachments", [])
        return raw

    def _write_index(self, index: dict[str, Any]) -> None:
        ensure_dir(self.index_path.parent)
        text = json.dumps(index, indent=2, sort_keys=True) + "\n"
        temporary = self.index_path.with_name(f".{self.index_path.name}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            with contextlib.suppress(OSError):
                temporary.chmod(0o600)
            temporary.replace(self.index_path)
        finally:
            if temporary.exists():
                with contextlib.suppress(OSError):
                    temporary.unlink()


def public(record: UploadRecord | Mapping[str, Any]) -> dict[str, Any]:
    """The wire projection shared by upload results, echoes, and history."""
    source: Mapping[str, Any] = (
        {
            "attachment_id": record.attachment_id,
            "name": record.name,
            "mime": record.mime,
            "kind": record.kind,
            "size": record.size,
            "duration_ms": record.duration_ms,
        }
        if isinstance(record, UploadRecord)
        else record
    )
    payload: dict[str, Any] = {
        "attachment_id": str(source.get("attachment_id") or ""),
        "name": str(source.get("name") or "upload"),
        "mime": str(source.get("mime") or "application/octet-stream"),
        "kind": str(source.get("kind") or "file"),
        "size": int(source.get("size") or 0),
    }
    duration = source.get("duration_ms")
    if isinstance(duration, int) and duration >= 0:
        payload["duration_ms"] = duration
    return payload


def _entry_path(entry: Mapping[str, Any], root: Path) -> Path | None:
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        path = Path(raw).resolve()
    except OSError:
        return None
    if not path.is_relative_to(root.expanduser().resolve()):
        return None
    return path


def _safe_name(value: object) -> str:
    text = str(value or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        char for char in text if char.isprintable() and char not in {"\x00", "\n", "\r", "\t"}
    ).strip()
    return (cleaned or "upload")[:255]


def _safe_extension(name: str, mime: str) -> str:
    suffix = Path(name).suffix.lower()
    if _SAFE_EXTENSION.match(suffix):
        return suffix
    guessed = (mimetypes.guess_extension(mime, strict=False) or "").lower()
    return guessed if _SAFE_EXTENSION.match(guessed) else ""


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
