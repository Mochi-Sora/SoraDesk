"""Paths, persisted desktop settings, and the bridge run-state file."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18791
DEFAULT_MAX_UPLOAD_MB = 25
DEFAULT_MAX_FETCH_MB = 25


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else default


APP_ROOT = _env_path("SORA_DESKTOP_ROOT", Path.home() / ".sora_agent" / "desktop")
RUN_ROOT = _env_path("SORA_DESKTOP_RUN_ROOT", Path.home() / ".sora_agent" / "run")
CONFIG_PATH = _env_path("SORA_DESKTOP_CONFIG", APP_ROOT / "config.json")
PAIRING_PATH = _env_path("SORA_DESKTOP_PAIRING", APP_ROOT / "pairing.json")
MEDIA_ROOT = _env_path("SORA_DESKTOP_MEDIA_ROOT", APP_ROOT / "media")
LOG_PATH = _env_path("SORA_DESKTOP_LOG", APP_ROOT / "desktop.log")
STATE_PATH = _env_path("SORA_DESKTOP_STATE", RUN_ROOT / "desktop.json")


@dataclass
class DesktopConfig:
    """User-facing bridge settings stored in ``~/.sora_agent/desktop/config.json``."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    trust_localhost: bool = True
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB
    max_fetch_mb: int = DEFAULT_MAX_FETCH_MB
    streaming: bool = True
    send_progress: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DesktopConfig:
        base = cls()
        host = raw.get("host")
        port = raw.get("port")
        return cls(
            host=str(host).strip() if isinstance(host, str) and host.strip() else base.host,
            port=_as_int(port, base.port, 0, 65535),
            trust_localhost=_as_bool(raw.get("trust_localhost"), base.trust_localhost),
            max_upload_mb=_as_int(raw.get("max_upload_mb"), base.max_upload_mb, 0, 4096),
            max_fetch_mb=_as_int(raw.get("max_fetch_mb"), base.max_fetch_mb, 0, 4096),
            streaming=_as_bool(raw.get("streaming"), base.streaming),
            send_progress=_as_bool(raw.get("send_progress"), base.send_progress),
        )

    def with_overrides(self, *, host: str | None = None, port: int | None = None) -> DesktopConfig:
        return DesktopConfig(
            host=host.strip() if host and host.strip() else self.host,
            port=port if port is not None else self.port,
            trust_localhost=self.trust_localhost,
            max_upload_mb=self.max_upload_mb,
            max_fetch_mb=self.max_fetch_mb,
            streaming=self.streaming,
            send_progress=self.send_progress,
        )


def load_config(path: Path | None = None) -> DesktopConfig:
    target = path or CONFIG_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = DesktopConfig()
    except (OSError, ValueError):
        config = DesktopConfig()
    else:
        config = DesktopConfig.from_dict(raw) if isinstance(raw, dict) else DesktopConfig()

    host = os.getenv("SORA_DESKTOP_HOST", "").strip()
    port_text = os.getenv("SORA_DESKTOP_PORT", "").strip()
    trust = os.getenv("SORA_DESKTOP_TRUST_LOCALHOST", "").strip()
    if host:
        config.host = host
    if port_text:
        config.port = _as_int(port_text, config.port, 0, 65535)
    if trust:
        config.trust_localhost = _as_bool(trust, config.trust_localhost)
    return config


def write_default_config(config: DesktopConfig, path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    if target.exists():
        return
    ensure_dir(target.parent)
    text = json.dumps(asdict(config), indent=2) + "\n"
    _atomic_write(target, text, mode=0o600)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def ensure_paths() -> None:
    for path in (APP_ROOT, MEDIA_ROOT, RUN_ROOT):
        ensure_dir(path)


def write_run_state(
    *,
    port: int,
    launch_mode: str,
    log_path: Path,
    host: str = DEFAULT_HOST,
) -> None:
    ensure_paths()
    payload = {
        "version": 1,
        "port": int(port),
        "pid": os.getpid(),
        "host": host,
        "launch_mode": launch_mode,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "log_path": str(log_path),
    }
    _atomic_write(STATE_PATH, json.dumps(payload, indent=2) + "\n", mode=0o600)


def read_run_state() -> dict[str, Any] | None:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def clear_run_state(*, pid: int | None = None) -> None:
    state = read_run_state()
    if pid is not None and state is not None and state.get("pid") != pid:
        return
    try:
        STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _atomic_write(target: Path, text: str, *, mode: int = 0o600) -> None:
    ensure_dir(target.parent)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _as_int(value: object, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.strip().lower()
        if folded in {"1", "true", "yes", "on"}:
            return True
        if folded in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return default
