"""``sora-desktop`` command line: run, supervise, and pair the desktop bridge."""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .config import (
    CONFIG_PATH,
    LOG_PATH,
    PAIRING_PATH,
    STATE_PATH,
    clear_run_state,
    ensure_paths,
    load_config,
    pid_alive,
    read_run_state,
    write_default_config,
)
from .devices import DeviceLedger, format_timestamp

log = logging.getLogger("sora_desktop.cli")

START_TIMEOUT_S = 12.0
STOP_TIMEOUT_S = 10.0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    command = args.command
    try:
        if command == "serve":
            return _serve(args)
        if command == "start":
            return _start(args)
        if command == "stop":
            return _stop(args)
        if command == "restart":
            _stop(args, quiet=True)
            return _start(args)
        if command == "status":
            return _status()
        if command == "logs":
            return _logs(args)
        if command == "pairing":
            return _pairing(args)
        if command == "config":
            return _config(args)
        parser.print_help()
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        log.debug("command failed", exc_info=True)
        print(f"sora-desktop: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sora-desktop",
        description="Local WebSocket bridge between SoraDesk and SoraAgent.",
    )
    parser.add_argument("--version", action="version", version="sora-desktop 0.1.0")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the bridge in the foreground")
    serve.add_argument("--host", help="bind host (default: config or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="bind port (default: config or 18791)")
    serve.add_argument(
        "--launch-mode",
        default="foreground",
        help=argparse.SUPPRESS,
    )

    start = sub.add_parser("start", help="start the bridge in the background")
    start.add_argument("--host", help="bind host (default: config or 127.0.0.1)")
    start.add_argument("--port", type=int, help="bind port (default: config or 18791)")

    sub.add_parser("stop", help="stop the background bridge")
    sub.add_parser("restart", help="restart the background bridge")
    sub.add_parser("status", help="show bridge status")

    logs = sub.add_parser("logs", help="print or follow the bridge log")
    logs.add_argument("--tail", type=int, default=80, help="lines to print (default 80)")
    logs.add_argument(
        "--no-follow",
        action="store_true",
        help="print the tail and exit instead of following",
    )

    pairing = sub.add_parser("pairing", help="manage paired devices and codes")
    pair_sub = pairing.add_subparsers(dest="pairing_command")
    pair_sub.add_parser("list", help="list paired devices (default)")
    pair_sub.add_parser("pending", help="list live pairing codes")
    approve = pair_sub.add_parser("approve", help="approve a pairing code")
    approve.add_argument("code")
    deny = pair_sub.add_parser("deny", help="deny a pairing code")
    deny.add_argument("code")
    revoke = pair_sub.add_parser("revoke", help="revoke a paired device")
    revoke.add_argument("token", help="device token or unique prefix")

    config = sub.add_parser("config", help="show paths and effective settings")
    config.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


# -- commands ------------------------------------------------------------------


def _serve(args: argparse.Namespace) -> int:
    from .server import Bridge

    config = load_config().with_overrides(host=args.host, port=args.port)
    write_default_config(config)
    ensure_paths()
    logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)
    bridge = Bridge(config, launch_mode=args.launch_mode)
    asyncio.run(bridge.run())
    return 0


def _start(args: argparse.Namespace) -> int:
    config = load_config().with_overrides(
        host=getattr(args, "host", None), port=getattr(args, "port", None)
    )
    state = read_run_state()
    if state is not None:
        pid = state.get("pid")
        if isinstance(pid, int) and pid_alive(pid):
            port = state.get("port", config.port)
            print(f"sora-desktop is already running (pid {pid}, ws://127.0.0.1:{port})")
            return 0
        clear_run_state()

    ensure_paths()
    write_default_config(config)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "sora_desktop",
        "serve",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--launch-mode",
        "daemon",
    ]
    log_handle = LOG_PATH.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()

    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(
                f"sora-desktop exited during startup (code {process.returncode}); "
                f"see {LOG_PATH}",
                file=sys.stderr,
            )
            _print_tail(LOG_PATH, 20)
            return 1
        state = read_run_state()
        if state is not None and state.get("pid") == process.pid:
            port = state.get("port")
            if isinstance(port, int):
                print(f"sora-desktop started (pid {process.pid}, ws://127.0.0.1:{port})")
                print(f"log: {LOG_PATH}")
                if not config.trust_localhost:
                    print("pair this device with: sora-desktop pairing pending")
                return 0
        time.sleep(0.1)

    print(
        f"sora-desktop did not report ready within {START_TIMEOUT_S:.0f}s; "
        f"see {LOG_PATH}",
        file=sys.stderr,
    )
    return 1


def _stop(args: argparse.Namespace | None = None, *, quiet: bool = False) -> int:
    state = read_run_state()
    if state is None:
        if not quiet:
            print("sora-desktop is not running")
        return 0
    pid = state.get("pid")
    if not isinstance(pid, int) or not pid_alive(pid):
        clear_run_state()
        if not quiet:
            print("sora-desktop is not running (removed a stale run-state file)")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_run_state(pid=pid)
        return 0
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.2)
    clear_run_state(pid=pid)
    if not quiet:
        print(f"sora-desktop stopped (pid {pid})")
    return 0


def _status() -> int:
    state = read_run_state()
    if state is None:
        print("sora-desktop: stopped")
        return 1
    pid = state.get("pid")
    running = isinstance(pid, int) and pid_alive(pid)
    port = state.get("port")
    host = state.get("host") or "127.0.0.1"
    print(f"sora-desktop: {'running' if running else 'stopped (stale run state)'}")
    if running:
        print(f"  url:         ws://{host}:{port}")
        print(f"  pid:         {pid}")
        print(f"  launch:      {state.get('launch_mode') or 'unknown'}")
        print(f"  started:     {state.get('started_at') or 'unknown'}")
        print(f"  log:         {state.get('log_path') or LOG_PATH}")
        ledger = DeviceLedger()
        devices = ledger.list_devices()
        pending = ledger.list_pending()
        print(f"  devices:     {len(devices)} paired, {len(pending)} pending")
    print(f"  state file:  {STATE_PATH}")
    return 0 if running else 1


def _logs(args: argparse.Namespace) -> int:
    if not LOG_PATH.exists():
        print(f"no log at {LOG_PATH}", file=sys.stderr)
        return 1
    _print_tail(LOG_PATH, max(0, int(args.tail)))
    if args.no_follow:
        return 0
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _pairing(args: argparse.Namespace) -> int:
    ledger = DeviceLedger()
    command = args.pairing_command or "list"
    if command == "list":
        devices = ledger.list_devices()
        if not devices:
            print("no paired devices")
        else:
            for device in devices:
                print(
                    f"{device.token[:16]}…  {device.name or 'unnamed':<24} "
                    f"approved {format_timestamp(device.approved_at)}  "
                    f"last seen {format_timestamp(device.last_seen)}"
                )
        pending = ledger.list_pending()
        if pending:
            print("\npending pairing codes:")
            for item in pending:
                print(
                    f"  {item.code}  expires {format_timestamp(item.expires_at)}  "
                    f"token {item.token[:12]}…"
                )
        return 0
    if command == "pending":
        pending = ledger.list_pending()
        if not pending:
            print("no pending pairing codes")
        for item in pending:
            print(
                f"{item.code}  expires {format_timestamp(item.expires_at)}  "
                f"token {item.token[:12]}…"
            )
        return 0
    if command in {"approve", "deny"}:
        if command == "approve":
            token = ledger.approve_code(args.code)
            if token is None:
                print("no live pairing code with that value", file=sys.stderr)
                return 1
            print(f"approved device {token[:16]}…")
            print("SoraDesk will connect on its own within a second.")
        else:
            token = ledger.deny_code(args.code)
            if token is None:
                print("no live pairing code with that value", file=sys.stderr)
                return 1
            print(f"denied pairing for device {token[:16]}…")
        return 0
    if command == "revoke":
        token = ledger.revoke(args.token)
        if token is None:
            print("no unique paired device matches that token or prefix", file=sys.stderr)
            return 1
        print(f"revoked device {token[:16]}…")
        print("A connected client is disconnected within a second.")
        return 0
    print(f"unknown pairing command: {command}", file=sys.stderr)
    return 2


def _config(args: argparse.Namespace) -> int:
    config = load_config()
    payload = {
        "host": config.host,
        "port": config.port,
        "trust_localhost": config.trust_localhost,
        "max_upload_mb": config.max_upload_mb,
        "max_fetch_mb": config.max_fetch_mb,
        "streaming": config.streaming,
        "send_progress": config.send_progress,
        "config_path": str(CONFIG_PATH),
        "pairing_path": str(PAIRING_PATH),
        "state_path": str(STATE_PATH),
        "log_path": str(LOG_PATH),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key:>18}: {value}")
    return 0


# -- helpers -------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _print_tail(path: Path, lines: int) -> None:
    if lines <= 0:
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in collections.deque(handle, maxlen=lines):
                sys.stdout.write(line)
    except OSError as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
