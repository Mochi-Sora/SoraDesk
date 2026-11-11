# SoraDesk bridge

A local WebSocket bridge that exposes a **SoraAgent** runtime to the SoraDesk
Tauri client using the protocol in [`../docs/protocol.md`](../docs/protocol.md).

The bridge is a thin adapter, not a second agent: it builds SoraAgent's own
provider, tools, memory, identity, calendar, sessions, and `ChatHandler`, then
translates the handler's cumulative stream snapshots into protocol `delta`
events and one authoritative `message` per segment. Streaming is enabled by
default; when it is disabled or the provider cannot stream, the bridge sends a
single complete `message` event instead.

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

## First run without a SoraAgent config

If `~/.sora_agent/config.json` does not exist, the bridge does not fall back to
the bare legacy gateway defaults. It starts with desktop defaults instead:
memory, identity, files, sessions, clock/calendar, and streaming are enabled,
the provider stays `mock`, and the demo order tools stay off. This keeps the
Memories and identity views meaningful before setup, without pretending a real
model is configured. Run `sora setup` to write a config and choose a provider.

The bridge log calls this out explicitly:

```
no SoraAgent config found; using desktop defaults (local features on, provider=mock until `sora setup` writes one)
```

## Files

| Path | Purpose |
|---|---|
| `~/.sora_agent/desktop/config.json` | bridge host, port, limits, streaming, progress |
| `~/.sora_agent/desktop/pairing.json` | paired devices and live pairing codes |
| `~/.sora_agent/desktop/media/` | uploaded files and the session attachment index |
| `~/.sora_agent/desktop/desktop.log` | daemon log |
| `~/.sora_agent/run/desktop.json` | run state read by the Tauri shell |
| `~/.sora_agent/sessions/` | SoraAgent's conversation store (read/write) |

## Protocol notes

- `hello.result.features` is empty: SoraAgent has no mid-turn injection, so the
  client queues text while a turn runs.
- Streaming follows both `streaming` in the bridge config and SoraAgent's
  `features.streaming` / `SORA_STREAMING` setting. Deltas are coalesced by the
  handler; the bridge diffs cumulative snapshots before emitting them.
- `calendar.list` is read-only and backed by SoraAgent's local calendar.
- `memory.stats` reports SoraAgent's `memory.doctor()` shape plus database and
  daily-store byte sizes.
- One active turn per session; `turn.interrupt` cancels the running turn and
  denies any pending approval.
