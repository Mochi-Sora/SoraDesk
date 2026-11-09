"""WebSocket server for the SoraDesk ↔ SoraAgent bridge (protocol v1)."""

from __future__ import annotations

import asyncio
import base64
import functools
import ipaddress
import json
import logging
import os
import signal
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from sora_agent.files import Attachment
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .config import (
    LOG_PATH,
    DesktopConfig,
    clear_run_state,
    ensure_paths,
    write_run_state,
)
from .devices import DeviceLedger
from .media import UploadError
from .media import public as public_attachment
from .protocol import (
    CLOSE_HELLO_TIMEOUT,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNAUTHORIZED,
    ERROR_BAD_REQUEST,
    ERROR_BUSY,
    ERROR_INTERNAL,
    ERROR_NO_APPROVAL,
    ERROR_NO_ATTACHMENT,
    ERROR_NO_SESSION,
    ERROR_NO_TURN,
    ERROR_NOT_PAIRED,
    ERROR_PROTOCOL_MISMATCH,
    ERROR_UNAUTHORIZED,
    ERROR_UNSUPPORTED,
    PROTOCOL_VERSION,
    RequestFrame,
    clamp_int,
    error_response,
    event_frame,
    ok_response,
    parse_request,
)
from .runtime import DesktopRuntime, TurnOutcome

log = logging.getLogger("sora_desktop.server")

HELLO_TIMEOUT_S = 10.0
PAIRING_POLL_S = 0.75
MAX_ATTACHMENTS_PER_SEND = 24


class BridgeError(Exception):
    """A protocol-level failure that maps to an error response."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class RequestOutcome:
    result: dict[str, Any]
    after: Callable[[], None] | None = None


class Client:
    """One WebSocket connection, including its ordered outbound queue."""

    def __init__(self, websocket: ServerConnection, remote: str) -> None:
        self.websocket = websocket
        self.remote = remote
        self.loop = asyncio.get_running_loop()
        self.outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.connected = True
        self.hello_ok = False
        self.first_frame = True
        self.paired = False
        self.trusted = False
        self.device_token: str | None = None
        self.pending_token: str | None = None
        self.approval_sent = False
        self.denial_sent = False
        self.client_name = ""

    @property
    def writer(self) -> asyncio.Task[None]:
        return self._writer_task

    def start(self) -> None:
        self._writer_task = self.loop.create_task(self._write_loop())

    def enqueue(self, frame: dict[str, Any]) -> None:
        if self.connected:
            self.outbox.put_nowait(frame)

    def thread_emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.connected:
            return
        frame = event_frame(event_type, payload)
        try:
            self.loop.call_soon_threadsafe(self.enqueue, frame)
        except RuntimeError:
            # The loop closed between the check and the call: the turn is over.
            pass

    async def close(self, code: int = 1000, reason: str = "", *, flush: bool = True) -> None:
        if not self.connected:
            return
        self.connected = False
        if flush:
            try:
                await asyncio.wait_for(self.outbox.join(), timeout=1.0)
            except asyncio.TimeoutError:
                log.debug("timed out flushing the outbound queue")
        self.outbox.put_nowait(None)
        try:
            await self.websocket.close(code, reason)
        except Exception:
            log.debug("close failed", exc_info=True)

    async def wait_closed(self) -> None:
        try:
            await self._writer_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("writer task ended with an error", exc_info=True)

    async def _write_loop(self) -> None:
        while True:
            frame = await self.outbox.get()
            try:
                if frame is None:
                    return
                await self.websocket.send(
                    json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
                )
            except ConnectionClosed:
                self.connected = False
                return
            except Exception:
                self.connected = False
                log.debug("send failed", exc_info=True)
                return
            finally:
                self.outbox.task_done()


class Bridge:
    """Owns the socket server, the SoraAgent runtime, and pairing state."""

    def __init__(self, config: DesktopConfig, *, launch_mode: str = "foreground") -> None:
        self.config = config
        self.launch_mode = launch_mode
        self.ledger = DeviceLedger()
        self.runtime = DesktopRuntime(config)
        self.executor = ThreadPoolExecutor(
            max_workers=max(4, min(16, (os.cpu_count() or 4) * 2)),
            thread_name_prefix="soradsk-turn",
        )
        self._clients: set[Client] = set()
        self._active_turns: dict[str, str] = {}
        self._server: Any = None
        self._watch_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.port = config.port

    # -- lifecycle -------------------------------------------------------------

    async def run(self) -> None:
        ensure_paths()
        max_frame = max(
            1024 * 1024,
            int(self.config.max_upload_mb * 1024 * 1024 * 4 / 3) + 1024 * 1024,
        )
        self._server = await serve(
            self._handle,
            self.config.host,
            self.config.port,
            max_size=max_frame,
            max_queue=32,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )
        socket = next(iter(self._server.sockets), None)
        self.port = int(socket.getsockname()[1]) if socket is not None else self.config.port
        write_run_state(
            port=self.port,
            host=self.config.host,
            launch_mode=self.launch_mode,
            log_path=LOG_PATH,
        )
        self._install_signal_handlers()
        self._watch_task = asyncio.create_task(self._watch_pairings())
        log.info(
            "SoraDesk bridge listening on ws://%s:%s (approval=%s, streaming=%s)",
            self.config.host,
            self.port,
            self.runtime.approval_mode,
            self.runtime.streaming,
        )
        try:
            await self._stop.wait()
        finally:
            await self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    async def _shutdown(self) -> None:
        log.info("SoraDesk bridge shutting down")
        if self._watch_task is not None:
            self._watch_task.cancel()
        self.runtime.close()
        for client in list(self._clients):
            await client.close(1011, "bridge stopping")
        self._server.close()
        await self._server.wait_closed()
        clients = list(self._clients)
        if clients:
            await asyncio.gather(*(client.wait_closed() for client in clients), return_exceptions=True)
        self.executor.shutdown(wait=False, cancel_futures=True)
        clear_run_state(pid=os.getpid())

    # -- connection ------------------------------------------------------------

    async def _handle(self, websocket: ServerConnection) -> None:
        remote = _peer_ip(websocket)
        client = Client(websocket, remote)
        client.start()
        self._clients.add(client)
        log.info("connection from %s", remote)
        deadline = asyncio.get_running_loop().time() + HELLO_TIMEOUT_S
        try:
            while True:
                raw = await self._recv(websocket, client, deadline)
                if raw is None:
                    return
                frame = parse_request(raw)
                if frame is None:
                    client.enqueue(
                        error_response("", ERROR_BAD_REQUEST, "expected a JSON request frame")
                    )
                    continue
                if client.first_frame:
                    client.first_frame = False
                    if not _compatible(frame.version):
                        client.enqueue(
                            error_response(
                                frame.id,
                                ERROR_PROTOCOL_MISMATCH,
                                f"this bridge speaks protocol v{PROTOCOL_VERSION}",
                            )
                        )
                        await asyncio.sleep(0)
                        await client.close(CLOSE_PROTOCOL_ERROR, "protocol mismatch")
                        return
                await self._dispatch(client, frame)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(client)
            if client.device_token:
                self.runtime.uploads.release(client.device_token)
            await client.close()
            await client.wait_closed()
            log.info("connection from %s closed", remote)

    async def _recv(
        self, websocket: ServerConnection, client: Client, deadline: float
    ) -> str | None:
        try:
            if not client.hello_ok:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await client.close(CLOSE_HELLO_TIMEOUT, "hello timeout")
                    return None
                raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            else:
                raw = await websocket.recv()
        except asyncio.TimeoutError:
            await client.close(CLOSE_HELLO_TIMEOUT, "hello timeout")
            return None
        except ConnectionClosed:
            return None
        if isinstance(raw, bytes):
            return None
        return raw

    async def _dispatch(self, client: Client, frame: RequestFrame) -> None:
        if frame.type == "ping":
            client.enqueue(
                ok_response(
                    frame.id,
                    {
                        "ts": frame.body.get("ts", _now_ms()),
                        "server_time": _now_ms(),
                    },
                )
            )
            return
        if frame.type == "hello":
            await self._hello(client, frame)
            return
        if not client.paired:
            client.enqueue(
                error_response(frame.id, ERROR_NOT_PAIRED, "pair this device first")
            )
            return
        try:
            outcome = await self._serve_request(client, frame)
        except BridgeError as exc:
            client.enqueue(error_response(frame.id, exc.code, exc.message, exc.details))
            return
        except Exception as exc:
            log.exception("request %s failed", frame.type)
            client.enqueue(
                error_response(frame.id, ERROR_INTERNAL, f"{type(exc).__name__}: {exc}")
            )
            return
        client.enqueue(ok_response(frame.id, outcome.result))
        if outcome.after is not None:
            outcome.after()

    # -- pairing ---------------------------------------------------------------

    async def _hello(self, client: Client, frame: RequestFrame) -> None:
        client.hello_ok = True
        provided = frame.body.get("device_token")
        token = provided.strip() if isinstance(provided, str) and provided.strip() else None
        info = frame.body.get("client")
        if isinstance(info, dict):
            name = info.get("name")
            version = info.get("version")
            if isinstance(name, str):
                client.client_name = name.strip()[:120]
            if isinstance(version, str) and version.strip():
                client.client_name = (
                    f"{client.client_name} {version.strip()}"[:120]
                    if client.client_name
                    else version.strip()[:120]
                )
        client.trusted = self._trusted(client.remote)
        reason: str | None = None
        pairing: dict[str, Any] | None = None

        if client.trusted:
            if token and self.ledger.is_approved(token) and not self.ledger.is_revoked(token):
                device = token
            else:
                device = f"local-{uuid.uuid4().hex[:20]}"
                self.ledger.approve_token(device, client.client_name or "loopback")
            self.ledger.touch(device)
            client.paired = True
            client.device_token = device
            client.pending_token = None
            client.approval_sent = False
            client.denial_sent = False
        elif token and self.ledger.is_approved(token):
            self.ledger.touch(token)
            client.paired = True
            client.device_token = token
            client.pending_token = None
            client.approval_sent = False
            client.denial_sent = False
        else:
            client.paired = False
            if token and self.ledger.is_revoked(token):
                reason = "revoked"
            elif token and self.ledger.code_for_token(token) is not None:
                reason = "pending"
            elif token:
                reason = "unknown"
            else:
                reason = "missing"
            if token is None or not self.ledger.is_known(token) or reason == "revoked":
                token = f"dev-{uuid.uuid4().hex[:24]}"
            pending = self.ledger.code_for_token(token)
            if pending is None:
                code, expires = self.ledger.issue_code(token)
            else:
                code, expires = pending.code, pending.expires_at
            client.pending_token = token
            client.device_token = None
            client.approval_sent = False
            client.denial_sent = False
            pairing = {"code": code, "expires_at": int(expires * 1000)}

        client.enqueue(
            ok_response(
                frame.id,
                {
                    "protocol": PROTOCOL_VERSION,
                    "paired": client.paired,
                    "device_token": client.device_token,
                    "pairing": pairing,
                    "gateway": {
                        "version": self.runtime.version,
                        "started_at": self.runtime.started_at,
                    },
                    "reason": None if client.paired else reason,
                    "limits": self.runtime.limits(),
                    "features": self.runtime.features,
                },
            )
        )
        if client.paired:
            log.info("device %s paired from %s", _token_prefix(client.device_token), client.remote)

    def _trusted(self, remote: str) -> bool:
        if not self.config.trust_localhost:
            return False
        try:
            address = ipaddress.ip_address(remote)
        except ValueError:
            return remote in {"localhost", "ip6-localhost"}
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        return address.is_loopback

    async def _watch_pairings(self) -> None:
        while True:
            try:
                await asyncio.sleep(PAIRING_POLL_S)
                for client in list(self._clients):
                    if not client.paired and client.pending_token:
                        token = client.pending_token
                        if self.ledger.is_approved(token):
                            if not client.approval_sent:
                                client.approval_sent = True
                                client.enqueue(
                                    event_frame(
                                        "pairing.approved", {"device_token": token}
                                    )
                                )
                        elif (
                            self.ledger.code_for_token(token) is None
                            and not client.denial_sent
                        ):
                            client.denial_sent = True
                            client.enqueue(
                                event_frame(
                                    "pairing.denied", {"reason": "denied or expired"}
                                )
                            )
                    elif (
                        client.paired
                        and client.device_token
                        and self.ledger.is_revoked(client.device_token)
                    ):
                        client.enqueue(
                            error_response(
                                "", ERROR_UNAUTHORIZED, "this device token was revoked"
                            )
                        )
                        await client.close(CLOSE_UNAUTHORIZED, "revoked")
                        self._clients.discard(client)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("pairing watcher failed")

    # -- requests --------------------------------------------------------------

    async def _serve_request(self, client: Client, frame: RequestFrame) -> RequestOutcome:
        loop = asyncio.get_running_loop()
        params = frame.body
        if frame.type == "chat.send":
            return self._chat_send(client, params)
        if frame.type == "turn.interrupt":
            return self._interrupt(params)
        if frame.type == "media.upload":
            return await loop.run_in_executor(
                self.executor, functools.partial(self._upload, client, params)
            )
        if frame.type == "media.fetch":
            return await loop.run_in_executor(
                self.executor, functools.partial(self._fetch, params)
            )
        if frame.type == "sessions.list":
            limit = clamp_int(params.get("limit"), 50, 1, 200)
            cursor = params.get("cursor")
            result = await loop.run_in_executor(
                self.executor,
                functools.partial(
                    self.runtime.catalog.list,
                    limit=limit,
                    cursor=cursor if isinstance(cursor, str) else None,
                ),
            )
            return RequestOutcome(result)
        if frame.type == "sessions.history":
            session_id = _require_string(params, "session_id")
            limit = clamp_int(params.get("limit"), 100, 1, 500)
            before = params.get("before")
            result = await loop.run_in_executor(
                self.executor,
                functools.partial(
                    self.runtime.catalog.history,
                    session_id,
                    limit=limit,
                    before=before if isinstance(before, str) else None,
                ),
            )
            if result is None:
                raise BridgeError(ERROR_NO_SESSION, f"unknown session {session_id}")
            return RequestOutcome(result)
        if frame.type == "memory.stats":
            result = await loop.run_in_executor(self.executor, self.runtime.memory_stats)
            return RequestOutcome(result)
        if frame.type == "calendar.list":
            limit = clamp_int(params.get("limit"), 200, 1, 500)
            result = await loop.run_in_executor(
                self.executor, functools.partial(self.runtime.calendar_list, limit)
            )
            return RequestOutcome(result)
        if frame.type in {"approve", "deny"}:
            return self._resolve_approval(frame.type == "approve", params)
        raise BridgeError(ERROR_UNSUPPORTED, f"unknown request type: {frame.type}")

    def _chat_send(self, client: Client, params: dict[str, Any]) -> RequestOutcome:
        raw_session = params.get("session_id")
        if raw_session is None:
            session_id = self.runtime.new_session_id()
        elif isinstance(raw_session, str) and raw_session.strip():
            session_id = raw_session.strip()
            if self.runtime.session_key(session_id) is None:
                raise BridgeError(ERROR_NO_SESSION, f"unknown session {session_id}")
        else:
            raise BridgeError(ERROR_BAD_REQUEST, "session_id must be a string or null")

        text = params.get("text")
        if not isinstance(text, str):
            raise BridgeError(ERROR_BAD_REQUEST, "text must be a string")
        raw_ids = params.get("attachments", [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list) or any(
            not isinstance(item, str) for item in raw_ids
        ):
            raise BridgeError(ERROR_BAD_REQUEST, "attachments must be an array of ids")
        if len(raw_ids) > MAX_ATTACHMENTS_PER_SEND:
            raise BridgeError(
                ERROR_BAD_REQUEST,
                f"at most {MAX_ATTACHMENTS_PER_SEND} attachments per message",
            )
        if not text.strip() and not raw_ids:
            raise BridgeError(ERROR_BAD_REQUEST, "text or an attachment is required")

        token = client.device_token or ""
        uploads = self.runtime.uploads
        records = []
        for attachment_id in raw_ids:
            record = uploads.pending(attachment_id, token)
            if record is None:
                raise BridgeError(
                    ERROR_NO_ATTACHMENT,
                    f"attachment {attachment_id} is unknown, expired, or not yours",
                )
            records.append(record)
        if session_id in self._active_turns:
            raise BridgeError(ERROR_BUSY, "this session already has a running turn")

        if not text.strip():
            # SoraAgent stores the user content verbatim, and some providers
            # reject an empty user message. Name the files instead of sending "".
            names = ", ".join(record.name for record in records)
            text = f"See attached: {names}"

        attachments: list[Attachment] = []
        try:
            for record in records:
                data = record.path.read_bytes()
                attachments.append(
                    Attachment(
                        filename=record.name,
                        mime_type=record.mime,
                        kind="" if record.kind == "file" else record.kind,
                        file_id=record.attachment_id,
                        data=data,
                        metadata={
                            "attachment_id": record.attachment_id,
                            "duration_ms": record.duration_ms,
                        },
                    )
                )
        except OSError as exc:
            raise BridgeError(ERROR_INTERNAL, "failed to read an uploaded attachment") from exc

        consumed = []
        for attachment_id in raw_ids:
            record = uploads.consume(attachment_id, token)
            if record is None:
                raise BridgeError(ERROR_NO_ATTACHMENT, f"attachment {attachment_id} expired")
            consumed.append(record)

        key = self.runtime.session_key(session_id)
        assert key is not None
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        for record in consumed:
            uploads.remember(record, key, text, batch_id=message_id)
        self._active_turns[session_id] = turn_id

        def after() -> None:
            client.enqueue(
                event_frame(
                    "message",
                    {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "role": "user",
                        "text": text,
                        "ts": _now_ms(),
                        "attachments": [public_attachment(record) for record in consumed],
                    },
                )
            )
            asyncio.create_task(
                self._run_turn(
                    client,
                    session_id=session_id,
                    turn_id=turn_id,
                    text=text,
                    attachments=attachments,
                )
            )

        return RequestOutcome({"session_id": session_id, "turn_id": turn_id}, after)

    def _interrupt(self, params: dict[str, Any]) -> RequestOutcome:
        session_id = _require_string(params, "session_id")
        turn_id = _require_string(params, "turn_id")
        if self._active_turns.get(session_id) != turn_id:
            raise BridgeError(ERROR_NO_TURN, "that turn is not running")
        stopped = self.runtime.cancel(session_id)
        return RequestOutcome({"interrupted": bool(stopped)})

    def _resolve_approval(
        self, approved: bool, params: dict[str, Any]
    ) -> RequestOutcome:
        approval_id = _require_string(params, "approval_id")
        resolve = getattr(self.runtime.approvals, "resolve", None)
        if not callable(resolve) or not resolve(approval_id, approved):
            raise BridgeError(
                ERROR_NO_APPROVAL, f"approval {approval_id} is not pending"
            )
        return RequestOutcome({"approval_id": approval_id, "approved": approved})

    def _upload(self, client: Client, params: dict[str, Any]) -> RequestOutcome:
        name = params.get("name")
        mime = params.get("mime")
        kind = params.get("kind")
        data = params.get("data")
        duration = params.get("duration_ms")
        if not isinstance(name, str) or not name.strip():
            raise BridgeError(ERROR_BAD_REQUEST, "name must be a non-empty string")
        if not isinstance(mime, str):
            raise BridgeError(ERROR_BAD_REQUEST, "mime must be a string")
        if not isinstance(kind, str):
            raise BridgeError(ERROR_BAD_REQUEST, "kind must be a string")
        if not isinstance(data, str):
            raise BridgeError(ERROR_BAD_REQUEST, "data must be base64 text")
        duration_ms = None
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            duration_ms = max(0, int(duration))
        try:
            record = self.runtime.uploads.store(
                client.device_token or "",
                kind=kind,
                data=data,
                name=name,
                mime=mime,
                duration_ms=duration_ms,
            )
        except UploadError as exc:
            raise BridgeError(exc.code, exc.message, exc.details) from exc
        return RequestOutcome(public_attachment(record))

    def _fetch(self, params: dict[str, Any]) -> RequestOutcome:
        session_id = _require_string(params, "session_id")
        attachment_id = _require_string(params, "attachment_id")
        key = self.runtime.session_key(session_id)
        if key is None:
            raise BridgeError(ERROR_NO_SESSION, f"unknown session {session_id}")
        try:
            entry, data = self.runtime.uploads.fetch(key, attachment_id)
        except UploadError as exc:
            raise BridgeError(exc.code, exc.message, exc.details) from exc
        result = public_attachment(entry)
        result["data"] = base64.b64encode(data).decode("ascii")
        return RequestOutcome(result)

    # -- turn execution --------------------------------------------------------

    async def _run_turn(
        self,
        client: Client,
        *,
        session_id: str,
        turn_id: str,
        text: str,
        attachments: list[Attachment],
    ) -> None:
        loop = asyncio.get_running_loop()
        outcome: TurnOutcome
        try:
            outcome = await loop.run_in_executor(
                self.executor,
                functools.partial(
                    self.runtime.handle_turn,
                    session_id=session_id,
                    turn_id=turn_id,
                    text=text,
                    attachments=attachments,
                    emit=client.thread_emit,
                ),
            )
        except Exception as exc:
            log.exception("desktop turn crashed")
            outcome = TurnOutcome("error", f"{type(exc).__name__}: {exc}")
        finally:
            self._active_turns.pop(session_id, None)
        payload: dict[str, Any] = {
            "session_id": session_id,
            "turn_id": turn_id,
            "status": outcome.status,
        }
        if outcome.error:
            payload["error"] = outcome.error
        client.enqueue(event_frame("turn.end", payload))
        try:
            meta = await loop.run_in_executor(
                self.executor, self.runtime.catalog.meta, session_id
            )
        except Exception:  # noqa: BLE001 - metadata is cosmetic
            meta = None
        if meta is not None:
            client.enqueue(
                event_frame(
                    "session.updated",
                    {
                        "session_id": session_id,
                        "title": meta["title"],
                        "updated_at": meta["updated_at"],
                        "message_count": meta["message_count"],
                    },
                )
            )


def _require_string(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(ERROR_BAD_REQUEST, f"{name} must be a non-empty string")
    return value.strip()


def _compatible(version: object) -> bool:
    return version == PROTOCOL_VERSION


def _peer_ip(websocket: ServerConnection) -> str:
    address = getattr(websocket, "remote_address", None)
    if isinstance(address, tuple) and address:
        return str(address[0])
    return ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _token_prefix(token: str | None) -> str:
    return f"{token[:10]}…" if token else "?"
