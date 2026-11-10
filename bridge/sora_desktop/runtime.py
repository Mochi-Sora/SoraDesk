"""SoraAgent runtime adapter for the SoraDesk bridge.

The bridge deliberately does not reimplement the agent.  It builds the same
provider, tool registry, memory, identity, calendar, session store, and chat
handler that SoraAgent's own channels use, then translates the chat handler's
reply into the SoraDesk protocol's complete assistant message.  The current
SoraAgent runtime does not stream token deltas, so a turn delivers one
authoritative ``message`` event; clients that support streaming still work
unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sora_agent import __version__ as SORA_VERSION
from sora_agent.approval import AutoApprovalGate
from sora_agent.calendar import CalendarItem
from sora_agent.channels.chat import CHAT_SYSTEM_PROMPT, ChatHandler
from sora_agent.channels.sessions import SessionStore
from sora_agent.channels.sessions import default_root as default_sessions_root
from sora_agent.channels.types import InboundMessage
from sora_agent.files import Attachment
from sora_agent.persona import apply_persona
from sora_agent.runner import RunEvent
from sora_agent.runtime_setup import (
    approval_mode,
    build_calendar,
    build_file_understander,
    build_identity,
    build_memory,
    build_provider,
    build_registry,
    build_sessions,
    context_window,
    load_runtime,
    quality_enabled,
)

from .config import DesktopConfig
from .media import UploadStore
from .protocol import PREVIEW_MAX_CHARS, truncate_text
from .sessions import SessionCatalog

log = logging.getLogger("sora_desktop.runtime")

Emit = Callable[[str, dict[str, Any]], None]

_TURN = threading.local()


@dataclass
class TurnContext:
    """State shared by one worker thread for the duration of a turn."""

    session_id: str
    turn_id: str
    emit: Emit
    tool_seq: int = 0


def _context() -> TurnContext | None:
    return getattr(_TURN, "context", None)


@dataclass
class TurnOutcome:
    status: str
    error: str = ""


class DesktopApprovals:
    """Blocking approval gate wired to the WebSocket event stream.

    Tool execution calls ``request`` on the worker thread.  The prompt is
    published immediately, and the worker waits for ``resolve`` (or timeout,
    cancellation, and shutdown, all of which fail closed).
    """

    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = max(1.0, float(timeout))
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def request(self, action: str, detail: str) -> bool:
        context = _context()
        if context is None:
            log.warning("approval requested outside a desktop turn: %s", action)
            return False
        approval_id = f"apr-{uuid.uuid4().hex[:12]}"
        deadline = time.time() + self.timeout
        pending = PendingApproval(
            approval_id=approval_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool=str(action),
            detail=str(detail),
            deadline=deadline,
        )
        with self._lock:
            busy = any(
                item.session_id == context.session_id
                for item in self._pending.values()
            )
            if busy:
                log.warning("approval already pending for %s", context.session_id)
                return False
            self._pending[approval_id] = pending
        try:
            context.emit(
                "approval",
                {
                    "session_id": context.session_id,
                    "turn_id": context.turn_id,
                    "approval_id": approval_id,
                    "tool": pending.tool,
                    "summary": _summary(detail),
                    "args_preview": truncate_text(detail, PREVIEW_MAX_CHARS),
                    "deadline": int(deadline * 1000),
                },
            )
            decided = pending.event.wait(self.timeout)
        finally:
            with self._lock:
                if self._pending.get(approval_id) is pending:
                    del self._pending[approval_id]
        if not decided:
            log.warning("approval timed out for %s", action)
            return False
        return bool(pending.decision)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is None:
            return False
        pending.decision = bool(approved)
        pending.event.set()
        return True

    def cancel(self, session_id: str | None = None) -> bool:
        with self._lock:
            matches = [
                item
                for item in self._pending.values()
                if session_id is None or item.session_id == session_id
            ]
            for item in matches:
                self._pending.pop(item.approval_id, None)
        for item in matches:
            item.decision = False
            item.event.set()
        return bool(matches)

    def cancel_all(self) -> None:
        self.cancel(None)

    def log(self, action: str, decision: str, detail: str, actor: str = "agent") -> None:
        """Approval audit is best-effort in the desktop bridge (logged only)."""
        log.debug("approval audit %s %s %s", action, decision, actor)


@dataclass
class PendingApproval:
    approval_id: str
    session_id: str
    turn_id: str
    tool: str
    detail: str
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    decision: bool | None = None


class DesktopRuntime:
    """The assembled SoraAgent runtime used by the bridge server."""

    def __init__(self, config: DesktopConfig) -> None:
        self.config = config
        self.settings, self.secrets, self.configured = load_runtime()
        self.started_at = _now_ms()
        self.version = SORA_VERSION

        self.uploads = UploadStore(
            max_upload_bytes=config.max_upload_mb * 1024 * 1024,
            max_fetch_bytes=config.max_fetch_mb * 1024 * 1024,
        )

        self.provider = build_provider(self.settings, self.secrets)
        self.registry = build_registry(self.settings)
        try:
            self.identity = build_identity(self.settings)
        except Exception:
            log.exception("identity initialization failed; continuing without it")
            self.identity = None
        try:
            self.memory = build_memory(self.settings, self.identity, self.secrets)
        except Exception:
            log.exception("memory initialization failed; continuing without it")
            self.memory = None

        sessions = build_sessions(self.settings)
        if sessions is None:
            # The desktop app always needs a transcript to render.  If the
            # generic sessions feature was disabled, still keep desktop
            # conversations on disk so the Sessions view works.
            sessions = SessionStore(default_sessions_root(), enabled=True)
        self.sessions = sessions
        self.catalog = SessionCatalog(sessions, self.uploads)

        calendar = build_calendar(self.settings)
        self.calendar: Any = False if calendar is False else calendar
        try:
            self.files = build_file_understander(self.settings, self.secrets)
        except Exception:
            log.exception("file understander initialization failed")
            self.files = None

        mode = approval_mode(self.settings)
        if mode == "off":
            self.approvals: Any = AutoApprovalGate()
        else:
            self.approvals = DesktopApprovals(
                timeout=self.settings.limits.approval_timeout
            )
        self.approval_mode = mode

        prompt = apply_persona(
            CHAT_SYSTEM_PROMPT,
            self.settings.agent.name,
            self.settings.agent.personality.base,
            self.settings.agent.personality.warmth,
        )
        self.handler = ChatHandler(
            self.provider,
            self.registry,
            memory=self.memory,
            identity=self.identity,
            approval_gate=self.approvals,
            sessions=self.sessions,
            quality=None if quality_enabled(self.settings) else False,
            max_steps=self.settings.limits.max_steps,
            max_turns=self.settings.limits.max_turns,
            max_tool_result_chars=self.settings.limits.max_tool_result_chars,
            tool_timeout=self.settings.limits.tool_timeout,
            provider_retries=self.settings.limits.provider_retries,
            on_event=self._on_run_event,
            system_prompt=prompt,
            calendar=None if self.calendar is False else self.calendar,
            context_window=context_window(self.settings),
            file_understander=self.files,
        )
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # -- identity --------------------------------------------------------------

    @property
    def features(self) -> list[str]:
        # SoraAgent has no mid-turn steering; clients queue while busy.
        return []

    def limits(self) -> dict[str, int]:
        return {
            "max_upload_bytes": self.config.max_upload_mb * 1024 * 1024,
            "max_fetch_bytes": self.config.max_fetch_mb * 1024 * 1024,
        }

    def new_session_id(self) -> str:
        return f"s-{uuid.uuid4().hex[:12]}"

    def session_key(self, session_id: str) -> str | None:
        return self.catalog.session_key(session_id)

    # -- turns -----------------------------------------------------------------

    def handle_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        text: str,
        attachments: list[Attachment],
        emit: Emit,
    ) -> TurnOutcome:
        context = TurnContext(
            session_id=session_id,
            turn_id=turn_id,
            emit=emit,
        )
        _TURN.context = context
        try:
            inbound = InboundMessage(
                channel="desktop",
                sender_id="desktop",
                chat_id=session_id,
                content=text,
                attachments=attachments,
            )
            result = self.handler.handle(inbound)
            # Current SoraAgent returns plain text; older builds may return a
            # TurnReply-like object with a text field.  Accept both while the
            # streaming API is being refactored.
            final_text = str(getattr(result, "text", result)).strip()
            if final_text:
                emit(
                    "message",
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "message_id": f"{turn_id}-m1",
                        "role": "assistant",
                        "text": final_text,
                        "ts": _now_ms(),
                    },
                )
            with self._lock:
                interrupted = session_id in self._cancelled
                self._cancelled.discard(session_id)
            return TurnOutcome("interrupted" if interrupted else "completed")
        except Exception as exc:
            log.exception("desktop turn failed")
            with self._lock:
                self._cancelled.discard(session_id)
            return TurnOutcome("error", f"{type(exc).__name__}: {exc}")
        finally:
            _TURN.context = None

    def cancel(self, session_id: str) -> bool:
        key = self.catalog.session_key(session_id)
        if key is None:
            return False
        with self._lock:
            self._cancelled.add(session_id)
        stopped = self.handler.cancel(key)
        cancel = getattr(self.approvals, "cancel", None)
        if callable(cancel):
            stopped = cancel(session_id) or stopped
        return bool(stopped)

    def close(self) -> None:
        try:
            self.handler.cancel_all()
        except Exception:
            log.exception("cancelling turns during shutdown failed")
        cancel_all = getattr(self.approvals, "cancel_all", None)
        if callable(cancel_all):
            cancel_all()
        if self.memory is not None:
            try:
                self.memory.close()
            except Exception:
                log.exception("closing memory failed")

    # -- chat handler callbacks ------------------------------------------------

    def _on_run_event(self, event: RunEvent) -> None:
        context = _context()
        if context is None:
            return
        if event.kind == "tool_finished":
            context.tool_seq += 1
            ok = bool(event.ok)
            summary = event.detail.strip() if event.detail else ("done" if ok else "failed")
            context.emit(
                "tool",
                {
                    "session_id": context.session_id,
                    "turn_id": context.turn_id,
                    "tool_call_id": (
                        f"{context.turn_id}-tool-{event.step}-{context.tool_seq}"
                    ),
                    "name": event.name or "tool",
                    "phase": "end" if ok else "error",
                    "summary": truncate_text(summary, 240),
                    "ok": ok,
                },
            )
        elif event.kind == "provider_retry" and self.config.send_progress:
            context.emit(
                "progress",
                {
                    "session_id": context.session_id,
                    "turn_id": context.turn_id,
                    "kind": "provider",
                    "text": "Retrying the model…",
                },
            )

    # -- read-only views -------------------------------------------------------

    def memory_stats(self) -> dict[str, Any]:
        if self.memory is None:
            root = Path.home() / ".sora_agent"
            return {
                "enabled": False,
                "root": str(root),
                "db_path": str(root / "memory" / "cortexdb.db"),
                "db_bytes": 0,
                "daily": {
                    "days": 0,
                    "messages": 0,
                    "oldest": "",
                    "newest": "",
                    "bytes": 0,
                },
                "backup": {
                    "count": 0,
                    "active": 0,
                    "daily": 0,
                    "bytes": 0,
                    "latest": "",
                },
                "sidecar": {
                    "enabled": False,
                    "binary_present": False,
                    "running": False,
                    "healthy": False,
                    "http_addr": "",
                    "grpc_addr": "",
                    "db_path": "",
                    "backup_dir": "",
                    "version": "",
                    "log": "",
                },
                "config": {},
            }
        try:
            doctor = self.memory.doctor()
        except Exception as exc:
            log.exception("memory doctor failed")
            return {
                "enabled": True,
                "root": "",
                "db_path": "",
                "db_bytes": 0,
                "daily": {"days": 0, "messages": 0, "oldest": "", "newest": "", "bytes": 0},
                "backup": {"count": 0, "active": 0, "daily": 0, "bytes": 0, "latest": ""},
                "sidecar": {},
                "config": {},
                "error": str(exc),
            }
        config = doctor.get("config") if isinstance(doctor.get("config"), dict) else {}
        db_path = str(config.get("db_path") or "")
        daily_dir = str(config.get("daily_dir") or "")
        daily = doctor.get("daily") if isinstance(doctor.get("daily"), dict) else {}
        return {
            "enabled": True,
            "root": str(config.get("root") or ""),
            "db_path": db_path,
            "db_bytes": _file_size(db_path),
            "daily": {
                "days": int(daily.get("days") or 0),
                "messages": int(daily.get("messages") or 0),
                "oldest": str(daily.get("oldest") or ""),
                "newest": str(daily.get("newest") or ""),
                "bytes": _tree_size(daily_dir),
            },
            "backup": doctor.get("backup") if isinstance(doctor.get("backup"), dict) else {},
            "sidecar": doctor.get("sidecar") if isinstance(doctor.get("sidecar"), dict) else {},
            "config": {
                "recall_mode": config.get("recall_mode"),
                "auto_dream": config.get("auto_dream"),
                "scheduler_running": config.get("scheduler_running"),
                "dream_hour_utc": config.get("dream_hour_utc"),
            },
        }

    def calendar_list(self, limit: int = 200) -> dict[str, Any]:
        if self.calendar is False or self.calendar is None:
            return {"items": [], "enabled": False, "error": None}
        try:
            items = self.calendar.list_items(
                include_completed=True,
                limit=max(1, min(500, int(limit))),
            )
        except Exception as exc:
            log.exception("calendar listing failed")
            return {"items": [], "enabled": True, "error": str(exc)}
        return {
            "items": [_calendar_item(item) for item in items],
            "enabled": True,
            "error": None,
        }


def _calendar_item(item: CalendarItem) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "kind": item.kind,
        "title": item.title,
        "starts_at": item.starts_at,
        "ends_at": item.ends_at,
        "due_at": item.due_at,
        "notes": item.notes,
        "importance": item.importance,
        "importance_rank": int(item.importance_rank),
        "done": bool(item.done),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "completed_at": item.completed_at,
    }


def _summary(detail: str) -> str:
    text = detail.strip()
    if text.upper().startswith("ACTION:"):
        text = text[len("ACTION:") :].strip()
    return truncate_text(text, 240)


def _file_size(path: str) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _tree_size(path: str) -> int:
    if not path:
        return 0
    root = Path(path)
    total = 0
    try:
        for item in root.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _now_ms() -> int:
    return int(time.time() * 1000)
