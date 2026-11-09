"""Device ledger and pairing codes for the SoraDesk bridge.

Everything lives in one small JSON file. The server and the ``sora-desktop
pairing`` CLI are separate processes, so every read reloads the file and every
mutation replaces it atomically: no daemon IPC is needed for pairing.
"""

from __future__ import annotations

import json
import secrets
import string
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PAIRING_PATH, ensure_dir

CODE_TTL_S = 600
_CODE_ALPHABET = string.ascii_uppercase.replace("I", "").replace("O", "") + "23456789"
_LEDGER_VERSION = 1


@dataclass(frozen=True)
class DeviceEntry:
    token: str
    name: str
    approved_at: float
    last_seen: float


@dataclass(frozen=True)
class PendingCode:
    code: str
    token: str
    created_at: float
    expires_at: float


class DeviceLedger:
    """Tolerant JSON ledger of paired devices and live pairing codes."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or PAIRING_PATH
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # -- reads -----------------------------------------------------------------

    def is_approved(self, token: str) -> bool:
        return token in self._devices()

    def is_revoked(self, token: str) -> bool:
        revoked = self._read().get("revoked")
        return isinstance(revoked, dict) and token in revoked

    def is_known(self, token: str) -> bool:
        payload = self._read()
        devices = payload.get("devices")
        revoked = payload.get("revoked")
        codes = payload.get("codes")
        if isinstance(devices, dict) and token in devices:
            return True
        if isinstance(revoked, dict) and token in revoked:
            return True
        if isinstance(codes, dict):
            for entry in codes.values():
                if isinstance(entry, dict) and entry.get("token") == token:
                    return True
        return False

    def list_devices(self) -> list[DeviceEntry]:
        payload = self._read()
        raw = payload.get("devices")
        if not isinstance(raw, dict):
            return []
        entries: list[DeviceEntry] = []
        for token, value in raw.items():
            if not isinstance(token, str) or not isinstance(value, dict):
                continue
            entries.append(
                DeviceEntry(
                    token=token,
                    name=str(value.get("name") or ""),
                    approved_at=_as_float(value.get("approved_at")),
                    last_seen=_as_float(value.get("last_seen")),
                )
            )
        entries.sort(key=lambda entry: entry.approved_at)
        return entries

    def list_pending(self) -> list[PendingCode]:
        payload = self._read()
        raw = payload.get("codes")
        if not isinstance(raw, dict):
            return []
        now = time.time()
        entries: list[PendingCode] = []
        for code, value in raw.items():
            if not isinstance(code, str) or not isinstance(value, dict):
                continue
            expires_at = _as_float(value.get("expires_at"))
            token = value.get("token")
            if not isinstance(token, str) or expires_at <= now:
                continue
            entries.append(
                PendingCode(
                    code=code,
                    token=token,
                    created_at=_as_float(value.get("created_at")),
                    expires_at=expires_at,
                )
            )
        entries.sort(key=lambda entry: entry.created_at)
        return entries

    def code_for_token(self, token: str) -> PendingCode | None:
        for entry in self.list_pending():
            if entry.token == token:
                return entry
        return None

    def touch(self, token: str) -> None:
        if not self.is_approved(token):
            return

        def mutate(payload: dict[str, Any]) -> None:
            devices = payload.get("devices")
            if isinstance(devices, dict) and isinstance(devices.get(token), dict):
                devices[token]["last_seen"] = time.time()

        self._mutate(mutate)

    # -- pairing ---------------------------------------------------------------

    def issue_code(self, token: str, ttl: int = CODE_TTL_S) -> tuple[str, float]:
        """Return ``(code, expires_at)``, reusing a live code for the same token."""
        now = time.time()
        existing = self.code_for_token(token)
        if existing is not None:
            return existing.code, existing.expires_at

        with self._lock:
            payload = self._read()
            codes = payload.setdefault("codes", {})
            if not isinstance(codes, dict):
                codes = {}
                payload["codes"] = codes
            code = _unique_code(codes)
            expires_at = now + max(30, int(ttl))
            codes[code] = {
                "token": token,
                "created_at": now,
                "expires_at": expires_at,
            }
            self._prune_codes(payload, now)
            self._write(payload)
        return code, expires_at

    def approve_code(self, code: str) -> str | None:
        normalized = code.strip().upper()
        with self._lock:
            payload = self._read()
            codes = payload.get("codes")
            if not isinstance(codes, dict):
                return None
            entry = codes.get(normalized)
            if not isinstance(entry, dict):
                return None
            token = entry.get("token")
            if not isinstance(token, str):
                return None
            now = time.time()
            if _as_float(entry.get("expires_at")) <= now:
                codes.pop(normalized, None)
                self._write(payload)
                return None
            self._approve(payload, token, now)
            codes.pop(normalized, None)
            self._prune_codes(payload, now)
            self._write(payload)
            return token

    def deny_code(self, code: str) -> str | None:
        normalized = code.strip().upper()
        with self._lock:
            payload = self._read()
            codes = payload.get("codes")
            if not isinstance(codes, dict):
                return None
            entry = codes.pop(normalized, None)
            if not isinstance(entry, dict):
                return None
            token = entry.get("token")
            if not isinstance(token, str):
                return None
            self._write(payload)
            return token

    def approve_token(self, token: str, name: str = "loopback") -> None:
        with self._lock:
            payload = self._read()
            self._approve(payload, token, time.time(), name=name)
            self._write(payload)

    def revoke(self, token_or_prefix: str) -> str | None:
        needle = token_or_prefix.strip()
        if not needle:
            return None
        with self._lock:
            payload = self._read()
            devices = payload.get("devices")
            if not isinstance(devices, dict):
                return None
            matches = [
                token
                for token in devices
                if isinstance(token, str)
                and (token == needle or token.startswith(needle))
            ]
            if len(matches) != 1:
                return None
            token = matches[0]
            existing = devices.get(token)
            name = str(existing.get("name") or "") if isinstance(existing, dict) else ""
            devices.pop(token, None)
            revoked = payload.setdefault("revoked", {})
            if isinstance(revoked, dict):
                revoked[token] = {
                    "revoked_at": time.time(),
                    "name": name,
                }
            codes = payload.get("codes")
            if isinstance(codes, dict):
                for code in [c for c, e in codes.items() if isinstance(e, dict) and e.get("token") == token]:
                    codes.pop(code, None)
            self._write(payload)
            return token

    # -- storage ---------------------------------------------------------------

    def _devices(self) -> dict[str, Any]:
        payload = self._read()
        devices = payload.get("devices")
        return devices if isinstance(devices, dict) else {}

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": _LEDGER_VERSION, "devices": {}, "revoked": {}, "codes": {}}
        if not isinstance(raw, dict):
            return {"version": _LEDGER_VERSION, "devices": {}, "revoked": {}, "codes": {}}
        raw.setdefault("devices", {})
        raw.setdefault("revoked", {})
        raw.setdefault("codes", {})
        return raw

    def _write(self, payload: dict[str, Any]) -> None:
        ensure_dir(self._path.parent)
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(self._path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _mutate(self, mutate: Any) -> None:
        with self._lock:
            payload = self._read()
            mutate(payload)
            self._write(payload)

    def _approve(
        self, payload: dict[str, Any], token: str, now: float, *, name: str | None = None
    ) -> None:
        devices = payload.setdefault("devices", {})
        if not isinstance(devices, dict):
            devices = {}
            payload["devices"] = devices
        existing = devices.get(token)
        approved_at = (
            _as_float(existing.get("approved_at"))
            if isinstance(existing, dict) and existing.get("approved_at")
            else now
        )
        devices[token] = {
            "approved_at": approved_at,
            "last_seen": now,
            "name": (name or (existing or {}).get("name") or "")[:120],
        }
        revoked = payload.get("revoked")
        if isinstance(revoked, dict):
            revoked.pop(token, None)

    @staticmethod
    def _prune_codes(payload: dict[str, Any], now: float) -> None:
        codes = payload.get("codes")
        if not isinstance(codes, dict):
            return
        for code in [
            code
            for code, entry in codes.items()
            if not isinstance(entry, dict) or _as_float(entry.get("expires_at")) <= now
        ]:
            codes.pop(code, None)


def _unique_code(codes: dict[str, Any]) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if code not in codes:
            return code


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def format_timestamp(value: float) -> str:
    if value <= 0:
        return "unknown"
    return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
