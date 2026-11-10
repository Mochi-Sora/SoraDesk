#!/usr/bin/env python3
"""End-to-end smoke test for the SoraDesk bridge against SoraAgent's mock provider.

Run it with SoraAgent's environment:

    ../SoraAgent/.venv/bin/python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.TemporaryDirectory(prefix="soradsk-smoke-")
TMP = Path(_TMP.name)
os.environ["SORA_DESKTOP_ROOT"] = str(TMP / "desktop")
os.environ["SORA_DESKTOP_RUN_ROOT"] = str(TMP / "run")
os.environ["SORA_SESSIONS_ROOT"] = str(TMP / "sessions")
os.environ["SORA_CALENDAR_DB"] = str(TMP / "calendar.db")
os.environ["SORA_PROVIDER"] = "mock"
os.environ["SORA_TOOLS"] = "demo"
os.environ["SORA_MEMORY"] = "0"
os.environ["SORA_APPROVAL"] = "off"
os.environ["SORA_FILES"] = "0"

from websockets.asyncio.client import connect

from sora_desktop.config import STATE_PATH, DesktopConfig
from sora_desktop.devices import DeviceLedger
from sora_desktop.server import Bridge

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
        return
    raise AssertionError(f"{name}{': ' + detail if detail else ''}")


class Client:
    def __init__(self, websocket: Any) -> None:
        self.ws = websocket
        self.events: list[dict[str, Any]] = []

    async def send(self, frame: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(frame))

    async def recv(self, timeout: float = 20.0) -> dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def request(
        self, request_type: str, timeout: float = 20.0, **params: Any
    ) -> dict[str, Any]:
        request_id = f"t-{uuid.uuid4().hex[:10]}"
        await self.send({"v": 1, "id": request_id, "type": request_type, **params})
        while True:
            frame = await self.recv(timeout)
            if frame.get("id") == request_id and "ok" in frame:
                if not frame["ok"]:
                    error = frame.get("error", {})
                    raise AssertionError(
                        f"{request_type} failed: {error.get('code')} {error.get('message')}"
                    )
                return frame.get("result", {})
            if frame.get("type"):
                self.events.append(frame)

    async def wait_event(
        self, event_type: str, timeout: float = 20.0, **match: Any
    ) -> dict[str, Any]:
        for index, frame in enumerate(self.events):
            if frame.get("type") == event_type and _matches(frame.get("event", {}), match):
                return self.events.pop(index)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = await self.recv(max(0.1, deadline - time.monotonic()))
            if frame.get("type") == event_type and _matches(frame.get("event", {}), match):
                return frame
            if frame.get("type"):
                self.events.append(frame)
        raise AssertionError(f"timed out waiting for {event_type} {match}")

    def drain(self) -> list[dict[str, Any]]:
        events = list(self.events)
        self.events.clear()
        return events

    async def collect_turn(self, turn_id: str) -> dict[str, Any]:
        deltas: list[str] = []
        messages: list[dict[str, Any]] = []
        tools: list[dict[str, Any]] = []
        end: dict[str, Any] | None = None
        deadline = time.monotonic() + 30
        while end is None:
            frame = await self.recv(max(0.1, deadline - time.monotonic()))
            if frame.get("type") == "delta" and frame["event"].get("turn_id") == turn_id:
                deltas.append(frame["event"].get("text_delta", ""))
            elif frame.get("type") == "message" and frame["event"].get("turn_id") == turn_id:
                messages.append(frame["event"])
            elif frame.get("type") == "tool" and frame["event"].get("turn_id") == turn_id:
                tools.append(frame["event"])
            elif frame.get("type") == "turn.end" and frame["event"].get("turn_id") == turn_id:
                end = frame["event"]
            elif frame.get("type"):
                self.events.append(frame)
        return {
            "deltas": deltas,
            "messages": messages,
            "tools": tools,
            "end": end,
            "text": "".join(deltas),
        }


def _matches(event: dict[str, Any], match: dict[str, Any]) -> bool:
    return all(event.get(key) == value for key, value in match.items())


async def run() -> None:
    bridge = Bridge(DesktopConfig(port=0, trust_localhost=True))
    task = asyncio.create_task(bridge.run())
    while bridge.port == 0:
        if task.done():
            await task
        await asyncio.sleep(0.02)
    url = f"ws://127.0.0.1:{bridge.port}"
    print(f"bridge: {url}")
    try:
        await asyncio.wait_for(_client_session(url), timeout=120)
        await asyncio.wait_for(_observer_session(url), timeout=120)
        await asyncio.wait_for(_pairing_session(), timeout=60)
    finally:
        bridge.stop()
        await asyncio.wait_for(task, timeout=30)
    check("run state removed on shutdown", not STATE_PATH.exists())


async def _observer_session(url: str) -> None:
    """A second loopback client observes turns without owning them."""
    async with connect(url, max_size=32 * 1024 * 1024) as source_ws, connect(
        url, max_size=32 * 1024 * 1024
    ) as observer_ws:
        source = Client(source_ws)
        observer = Client(observer_ws)

        source_hello = await source.request(
            "hello", device_token=None, client={"name": "observer-source"}
        )
        check("observer source paired", source_hello["paired"] is True)
        observer_hello = await observer.request(
            "hello",
            device_token=None,
            client={"name": "observer", "version": "0.0.1", "platform": "test"},
        )
        check("observer loopback is paired", observer_hello["paired"] is True)

        subscribed = await observer.request("events.subscribe")
        check("observer subscribed", subscribed["subscribed"] is True)
        again = await observer.request("events.subscribe")
        check("observer subscribe is idempotent", again["subscribed"] is True)

        sent = await source.request("chat.send", session_id=None, text="observer fanout")
        turn_id = sent["turn_id"]
        echo = await observer.wait_event("message", turn_id=turn_id, role="user")
        check("observer sees user echo", echo["event"]["text"] == "observer fanout")
        observed = await observer.collect_turn(turn_id)
        check("observer sees turn end", observed["end"]["status"] == "completed")
        check("observer sees streamed text", bool(observed["text"].strip()))

        source_result = await source.collect_turn(turn_id)
        check("originating client still sees turn end", source_result["end"]["status"] == "completed")

        unsubscribed = await observer.request("events.unsubscribe")
        check("observer unsubscribed", unsubscribed["subscribed"] is False)

        quiet = await source.request(
            "chat.send", session_id=None, text="quiet after unsubscribe"
        )
        quiet_turn = quiet["turn_id"]
        await source.collect_turn(quiet_turn)
        try:
            await observer.wait_event("turn.end", turn_id=quiet_turn, timeout=1.0)
        except (AssertionError, TimeoutError):
            check("observer stops receiving after unsubscribe", True)
        else:
            raise AssertionError("observer received an event after unsubscribe")


async def _pairing_session() -> None:
    bridge = Bridge(DesktopConfig(port=0, trust_localhost=False))
    task = asyncio.create_task(bridge.run())
    while bridge.port == 0:
        if task.done():
            await task
        await asyncio.sleep(0.02)
    url = f"ws://127.0.0.1:{bridge.port}"
    print(f"pairing bridge: {url}")
    ledger = DeviceLedger()
    try:
        async with connect(url, max_size=32 * 1024 * 1024) as websocket:
            client = Client(websocket)
            hello = await client.request("hello", device_token=None, client={"name": "pair-test"})
            check("untrusted loopback is unpaired", hello["paired"] is False)
            check("pairing code issued", isinstance(hello.get("pairing"), dict))
            code = hello["pairing"]["code"]
            check("pairing reason", hello["reason"] == "missing")
            try:
                await client.request("sessions.list")
            except AssertionError as exc:
                check("requests blocked before pairing", "not_paired" in str(exc), str(exc))
            else:
                raise AssertionError("unpaired request should fail")

            token = ledger.approve_code(code)
            check("CLI approved code", isinstance(token, str))
            approved = await client.wait_event("pairing.approved", timeout=5)
            check("approval event token", approved["event"]["device_token"] == token)

            hello = await client.request("hello", device_token=token, client={"name": "pair-test"})
            check("paired after approval", hello["paired"] is True)
            listed = await client.request("sessions.list")
            check("paired request works", isinstance(listed["sessions"], list))
            try:
                await client.request("events.subscribe")
            except AssertionError as exc:
                check(
                    "pairing-only client cannot observe turn events",
                    "unauthorized" in str(exc),
                    str(exc),
                )
            else:
                raise AssertionError("untrusted observer subscription should fail")

            check("revoked token", ledger.revoke(token) == token)
            deadline = time.monotonic() + 5
            unauthorized = False
            while time.monotonic() < deadline:
                frame = await client.recv(max(0.1, deadline - time.monotonic()))
                error = frame.get("error") or {}
                if error.get("code") == "unauthorized":
                    unauthorized = True
                    break
            check("revocation pushes unauthorized", unauthorized)

        async with connect(url, max_size=32 * 1024 * 1024) as websocket:
            denied_client = Client(websocket)
            hello = await denied_client.request("hello", device_token=None)
            code = hello["pairing"]["code"]
            check("second pairing code", isinstance(code, str))
            check("deny code", isinstance(ledger.deny_code(code), str))
            denied = await denied_client.wait_event("pairing.denied", timeout=5)
            check("denial event", "reason" in denied["event"])
    finally:
        bridge.stop()
        await asyncio.wait_for(task, timeout=30)


async def _client_session(url: str) -> None:
    async with connect(url, max_size=32 * 1024 * 1024) as websocket:
        client = Client(websocket)

        print("handshake")
        hello = await client.request(
            "hello",
            device_token=None,
            client={"name": "smoke", "version": "0.0.1", "platform": "test"},
        )
        check("loopback is paired", hello["paired"] is True)
        check("protocol v1", hello["protocol"] == 1)
        check("no chat.inject feature", hello.get("features") == [])
        check("limits advertise upload", hello["limits"]["max_upload_bytes"] == 25 * 1024 * 1024)
        check("limits advertise fetch", hello["limits"]["max_fetch_bytes"] == 25 * 1024 * 1024)
        check("device token returned", isinstance(hello.get("device_token"), str))

        ping = await client.request("ping", ts=123456)
        check("ping echoes ts", ping["ts"] == 123456)
        check("ping has server_time", isinstance(ping["server_time"], int))

        print("empty sessions")
        listed = await client.request("sessions.list", limit=50)
        check("no sessions yet", listed["sessions"] == [])

        print("first turn")
        sent = await client.request("chat.send", session_id=None, text="hello from smoke")
        session_id = sent["session_id"]
        turn_id = sent["turn_id"]
        check("new session id", session_id.startswith("s-"))
        user = await client.wait_event("message", turn_id=turn_id, role="user")
        check("user echo text", user["event"]["text"] == "hello from smoke")
        first = await client.collect_turn(turn_id)
        check("turn completed", first["end"]["status"] == "completed", str(first["end"]))
        check("streamed deltas", bool(first["deltas"]))
        check("streamed text non-empty", bool(first["text"].strip()))
        assistant = [item for item in first["messages"] if item["role"] == "assistant"]
        check("final assistant message", bool(assistant) and bool(assistant[-1]["text"].strip()))
        check("tool progress observed", bool(first["tools"]))
        updated = await client.wait_event("session.updated", session_id=session_id)
        check("session updated title", updated["event"]["title"] == "hello from smoke")
        check("session updated count", updated["event"]["message_count"] == 2)


        print("history and list")
        history = await client.request("sessions.history", session_id=session_id)
        check("history has user + assistant", len(history["messages"]) == 2, str(history))
        check("history user text", history["messages"][0]["text"] == "hello from smoke")
        check(
            "history assistant matches final",
            history["messages"][1]["text"] == assistant[-1]["text"],
        )
        listed = await client.request("sessions.list", limit=50)
        check("session listed", len(listed["sessions"]) == 1)
        check("title derived", listed["sessions"][0]["title"] == "hello from smoke")
        check("message count", listed["sessions"][0]["message_count"] == 2)

        print("read-only views")
        memory = await client.request("memory.stats")
        check("memory disabled shape", memory["enabled"] is False)
        check("memory daily bytes", memory["daily"]["bytes"] == 0)
        calendar = await client.request("calendar.list", limit=20)
        check("calendar enabled", calendar["enabled"] is True)
        check("calendar empty", calendar["items"] == [])

        print("attachment round trip")
        upload = await client.request(
            "media.upload",
            name="pixel.png",
            mime="image/png",
            kind="image",
            data=base64.b64encode(PNG_1X1).decode("ascii"),
        )
        note = await client.request(
            "media.upload",
            name="note.txt",
            mime="text/plain",
            kind="file",
            data=base64.b64encode(b"hello").decode("ascii"),
        )
        check("upload size", upload["size"] == len(PNG_1X1))
        check("upload kind", upload["kind"] == "image")
        sent = await client.request(
            "chat.send",
            session_id=session_id,
            text="what is this?",
            attachments=[upload["attachment_id"], note["attachment_id"]],
        )
        attachment_turn = sent["turn_id"]
        echo = await client.wait_event("message", turn_id=attachment_turn, role="user")
        check(
            "user echo carries every attachment",
            [item["attachment_id"] for item in echo["event"]["attachments"]]
            == [upload["attachment_id"], note["attachment_id"]],
        )
        second = await client.collect_turn(attachment_turn)
        check("attachment turn completed", second["end"]["status"] == "completed")
        fetched = await client.request(
            "media.fetch",
            session_id=session_id,
            attachment_id=upload["attachment_id"],
        )
        check("fetched bytes match", base64.b64decode(fetched["data"]) == PNG_1X1)

        print("history keeps attachment metadata")
        history = await client.request("sessions.history", session_id=session_id)
        check("history grew", len(history["messages"]) == 4, str(len(history["messages"])))
        with_attachment = [
            item for item in history["messages"] if item.get("attachments")
        ]
        check("attachment record in history", len(with_attachment) == 1)
        check(
            "every attachment id in history",
            [item["attachment_id"] for item in with_attachment[0]["attachments"]]
            == [upload["attachment_id"], note["attachment_id"]],
        )

        print("attachment-only turn")
        solo = await client.request(
            "media.upload",
            name="solo.txt",
            mime="text/plain",
            kind="file",
            data=base64.b64encode(b"solo").decode("ascii"),
        )
        solo_send = await client.request(
            "chat.send",
            session_id=None,
            text="",
            attachments=[solo["attachment_id"]],
        )
        solo_echo = await client.wait_event(
            "message", turn_id=solo_send["turn_id"], role="user"
        )
        check(
            "attachment-only echo gets a caption",
            solo_echo["event"]["text"] == "See attached: solo.txt",
            solo_echo["event"]["text"],
        )
        await client.collect_turn(solo_send["turn_id"])
        solo_history = await client.request(
            "sessions.history", session_id=solo_send["session_id"]
        )
        check(
            "attachment-only history is not empty",
            solo_history["messages"][0]["text"] == "See attached: solo.txt",
            solo_history["messages"][0]["text"],
        )
        solo_list = await client.request("sessions.list", limit=50)
        solo_meta = next(
            item
            for item in solo_list["sessions"]
            if item["id"] == solo_send["session_id"]
        )
        check(
            "attachment-only title names the file",
            solo_meta["title"] == "See attached: solo.txt",
            solo_meta["title"],
        )

        print("errors")
        try:
            await client.request(
                "turn.interrupt", session_id=session_id, turn_id="turn-not-running"
            )
        except AssertionError as exc:
            check("interrupt unknown turn", "no_turn" in str(exc), str(exc))
        else:
            raise AssertionError("interrupt of an unknown turn should fail")

        try:
            await client.request("cron.list")
        except AssertionError as exc:
            check("cron removed", "unsupported" in str(exc), str(exc))
        else:
            raise AssertionError("cron.list should be unsupported")

        try:
            await client.request(
                "media.fetch", session_id=session_id, attachment_id="att-missing"
            )
        except AssertionError as exc:
            check("unknown attachment", "no_attachment" in str(exc), str(exc))
        else:
            raise AssertionError("unknown attachment should fail")

    print("device ledger")
    ledger = DeviceLedger(TMP / "ledger.json")
    token = f"dev-{uuid.uuid4().hex}"
    code, _expires = ledger.issue_code(token)
    check("code issued", len(code) == 6)
    check("pending before approval", not ledger.is_approved(token))
    check("approve returns token", ledger.approve_code(code) == token)
    check("approved after code", ledger.is_approved(token))
    check("revoke", ledger.revoke(token) == token)
    check("revoked", ledger.is_revoked(token))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    finally:
        _TMP.cleanup()
    print("smoke: all checks passed")
