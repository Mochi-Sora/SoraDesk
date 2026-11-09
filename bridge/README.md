# SoraDesk bridge

A local WebSocket bridge that exposes a **SoraAgent** runtime to the SoraDesk
Tauri client using the protocol in [`../docs/protocol.md`](../docs/protocol.md).

The bridge is a thin adapter, not a second agent: it builds SoraAgent's own
provider, tools, memory, identity, calendar, sessions, and `ChatHandler`, then
translates the handler's cumulative stream snapshots into protocol deltas.

## Install

The bridge imports `sora_agent` directly, so install SoraAgent and the bridge in
the same Python environment:

From this directory (`SoraDesk/bridge`), with a sibling `SoraAgent` checkout:

```sh
cd ../../SoraAgent
python -m venv .venv
.venv/bin/pip install -e .

cd ../SoraDesk/bridge
../SoraAgent/.venv/bin/pip install -e .
```

That provides the `sora-desktop` console script. To let the desktop app's Settings buttons
find it, link it into the app's search path or export `SORA_DESKTOP_BIN`:

```sh
mkdir -p ~/.sora_agent/venv/bin
ln -sf "$(cd ../../SoraAgent && pwd)/.venv/bin/sora-desktop" ~/.sora_agent/venv/bin/sora-desktop
```

## Run

```sh
sora-desktop start           # background, writes ~/.sora_agent/run/desktop.json
sora-desktop status
sora-desktop logs --tail 80 --no-follow
sora-desktop stop
```

`serve` runs in the foreground. The default endpoint is
`ws://127.0.0.1:18791`; loopback connections are trusted by default, so no
pairing prompt appears for local use. Set `trust_localhost: false` in
`~/.sora_agent/desktop/config.json` (or bind a non-loopback host) to require
pairing:

```sh
sora-desktop pairing pending
sora-desktop pairing approve <code>
sora-desktop pairing revoke <token-prefix>
```

## Files

| Path | Purpose |
|---|---|
| `~/.sora_agent/desktop/config.json` | bridge host, port, limits, streaming |
| `~/.sora_agent/desktop/pairing.json` | paired devices and live pairing codes |
| `~/.sora_agent/desktop/media/` | uploaded files and the session attachment index |
| `~/.sora_agent/desktop/desktop.log` | daemon log |
| `~/.sora_agent/run/desktop.json` | run state read by the Tauri shell |
| `~/.sora_agent/sessions/` | SoraAgent's conversation store (read/write) |

## Protocol notes

- `hello.result.features` is empty: SoraAgent has no mid-turn injection, so the
  client queues text while a turn runs.
- `calendar.list` is read-only and backed by SoraAgent's local calendar.
- `memory.stats` reports SoraAgent's `memory.doctor()` shape plus database and
  daily-store byte sizes.
- One active turn per session; `turn.interrupt` cancels the running turn and
  denies any pending approval.
