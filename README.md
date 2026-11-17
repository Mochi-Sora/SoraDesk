<p align="center">
  <img src="https://raw.githubusercontent.com/Mochi-Sora/SoraDesk/main/src-tauri/icons/icon.png" alt="SoraDesk" width="112">
</p>

<h1 align="center">SoraDesk</h1>

<p align="center"><strong>SoraAgent, on your desktop.</strong></p>

<p align="center">
  <a href="https://github.com/Mochi-Sora/SoraDesk/releases"><img src="https://img.shields.io/badge/version-0.2.2-1f6feb" alt="Version 0.2.2"></a>
  <a href="https://tauri.app/"><img src="https://img.shields.io/badge/Tauri-v2-24c8db" alt="Tauri v2"></a>
  <a href="https://github.com/Mochi-Sora/SoraDesk/releases"><img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-555" alt="macOS, Windows, Linux"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--only-2ea44f" alt="AGPL-3.0-only"></a>
</p>

<p align="center">
  <a href="https://discord.gg/s8W6JfBSMH">Discord</a> ·
  <a href="https://mochi-sora.github.io/SoraOfficialWebsite/">Website</a> ·
  <a href="https://github.com/Mochi-Sora/SoraDesk/releases/latest">Download</a> ·
  <a href="docs/protocol.md">Protocol</a> ·
  <a href="https://github.com/Mochi-Sora/SoraAgent">SoraAgent</a>
</p>

**SoraDesk is the native desktop client for [SoraAgent](https://github.com/Mochi-Sora/SoraAgent).** It gives a local or remote SoraAgent runtime a real window: streaming chat, persistent sessions, Specialists, calendar, memories, attachments, voice notes, approvals, and settings — over a direct local connection, a supervised SSH tunnel, or opt-in direct IP.

> **Status:** v0.2.2 is published. SoraDesk is a **client**, not a standalone agent: it needs SoraAgent **0.3.0+** and the matching `sora-desktop` bridge. Updater artifacts are signed; macOS and Windows installers are unsigned, so first launch may show a Gatekeeper or SmartScreen warning.

## At a glance

**It is**

- a Tauri v2 desktop app: a Rust transport shell and a vanilla TypeScript UI;
- a client for one SoraAgent runtime, with the same protocol locally, over SSH, or over TLS;
- a place to chat, inspect sessions, manage persistent Specialists, use the calendar, search memories, and approve consequential tool calls;
- a small front end that deliberately does **not** duplicate the agent's state — sessions, calendar, memory, and identity stay in SoraAgent.

**It is not**

- a model, a hosted chat service, or a replacement for SoraAgent;
- a mobile app;
- a second agent with its own prompts, tools, or memory.

**Best for**

- people already running SoraAgent who want a native desktop surface;
- a Mac/Windows/Linux workstation connecting to a home server or VPS over SSH;
- anyone who prefers explicit approvals and local state to a hosted dashboard.

## Start here

| You want to… | Go to |
| --- | --- |
| Download and run the app | [Download](#download) |
| Try the UI without Rust or SoraAgent | [UI development](#ui-development-without-rust-or-soraagent-recommended-first-run) |
| Connect a real SoraAgent backend | [Real SoraAgent backend](#real-soraagent-backend) |
| Connect over SSH to a VPS | [Real SoraAgent backend](#real-soraagent-backend) and [connection modes](docs/connections.md) |
| Build the desktop app yourself | [Desktop app](#desktop-app-needs-the-rust-toolchain) |
| Understand the client/server protocol | [docs/protocol.md](docs/protocol.md) |
| Cut a release | [docs/release.md](docs/release.md) |
| Ask a question | [Discord](https://discord.gg/s8W6JfBSMH) |

## Why SoraDesk

- **One protocol, three connections.** Local loopback, supervised SSH, or opt-in direct IP; no duplicated agent state.
- **Chat that streams and asks.** Tool chips, file-edit previews, human approvals, stop/interrupt, and queued drafts.
- **Specialists, managed.** Inspect, pause/resume, promote, or delete persistent delegation agents from the desktop.
- **Native files and voice.** Paperclip, drag & drop, paste, image/audio/video/file upload, and microphone voice notes.
- **Yours to inspect.** AGPL-3.0-only, local data, and a documented protocol with a mock gateway.

## Download

Get the latest build from [GitHub Releases](https://github.com/Mochi-Sora/SoraDesk/releases/latest).
v0.2.2 ships a universal macOS `.dmg`, a Windows NSIS installer, and Linux
`.AppImage`, `.deb`, and `.rpm` packages. The updater artifacts are signed; the
macOS and Windows installers are unsigned for now.

## Quickstart

### UI development, without Rust or SoraAgent (recommended first run)

```bash
npm install
npm run mock     # terminal 1: mock gateway on ws://127.0.0.1:18791
npm run dev      # terminal 2: open the printed URL in a browser
```

The UI detects it is running outside Tauri and talks directly to the mock gateway with the
same protocol frames the desktop app uses. Try:

- `hello` — auto-pairs the dev profile;
- send any message — streamed deltas, a final message, `turn.end`;
- include `tool` in a message — the approval flow (`Approve` / `Deny`), tool and file-edit chips;
- include `long` in a message — a long stream to exercise `Stop` (turn interrupt);
- attach files (paperclip, drag & drop, or paste), record a voice note with the microphone, and
  send — each uploads first (`media.upload`) and is echoed back on the user message;
- while an answer streams, press Enter to **queue** the draft; Esc stops the turn;
- open **Specialists** to inspect persistent delegation agents created with
  `sora delegate create`, pause/resume them, promote provisional agents, or delete them;
- `npm run smoke` — a 69-check conformance suite for [`docs/protocol.md`](docs/protocol.md)
  against the mock gateway.

### Desktop app (needs the Rust toolchain)

```bash
npm run tauri dev      # dev window
npm run tauri build    # .app / .dmg (or the platform equivalent)
```

Requires `cargo`/`rustc` on `PATH` (`~/.cargo/bin`). The Rust side owns the WebSocket, the
SSH tunnel supervisor, reconnect/backoff, and connection profiles. Release signing,
notarization, updater artifacts, and the tag workflow are covered in
[`docs/release.md`](docs/release.md).

### Real SoraAgent backend

```bash
# SoraAgent and the bridge share one Python environment.
cd ../SoraAgent
python -m venv .venv
.venv/bin/pip install -e .

cd ../SoraDesk/bridge
../SoraAgent/.venv/bin/pip install -e .

# Start the bridge in the background.
../SoraAgent/.venv/bin/sora-desktop start
../SoraAgent/.venv/bin/sora-desktop status
```

The app's Settings → Local desktop bridge buttons (`start` / `restart` / `stop` / logs) look
for `sora-desktop` on a fixed path list. If your SoraAgent venv is elsewhere, either export
`SORA_DESKTOP_BIN` before launching the app, or link the console script into that list:

```bash
# Still in SoraDesk/bridge:
mkdir -p ~/.sora_agent/venv/bin
ln -sf "$(cd ../../SoraAgent && pwd)/.venv/bin/sora-desktop" ~/.sora_agent/venv/bin/sora-desktop

# Then launch SoraDesk.
cd ..
npm run tauri dev
```

Loopback connections are trusted by default, so no pairing prompt appears for local use or an
SSH tunnel. To require pairing on loopback, set `trust_localhost: false` in
`~/.sora_agent/desktop/config.json`; non-loopback (direct IP) connections always require a
token. Approve the code shown in the app with:

```bash
sora-desktop pairing pending
sora-desktop pairing approve <code>
sora-desktop pairing revoke <token-prefix>
```

## How it is put together

```
SoraDesk/
├── .github/workflows/     # CI + tagged release builds
├── docs/
│   ├── protocol.md        # the frozen frame contract
│   ├── connections.md     # local / SSH / IP semantics and tunnel rules
│   └── release.md         # signing, notarization, updater key, release process
├── dev/
│   ├── mock-gateway.mjs   # reference implementation of the protocol
│   ├── smoke.mjs          # protocol conformance checks
│   └── check-versions.mjs # keeps package/tauri/client versions aligned
├── bridge/                # Python bridge to SoraAgent
│   ├── sora_desktop/      # server, runtime adapter, sessions, media, pairing, CLI
│   └── tests/smoke.py     # end-to-end bridge checks
├── src/                   # UI (vanilla TypeScript)
│   ├── main.ts            # shell, router, wiring
│   ├── protocol.ts        # frame types — mirrors docs/protocol.md
│   ├── chat.ts            # events → transcript (streaming, tools, approvals, one box per turn)
│   ├── media.ts           # attachments: classify, base64, upload, voice notes
│   ├── connection/        # client, profiles, bridge CLI helpers
│   └── views/             # chat, sessions, calendar, memories, settings
└── src-tauri/
    ├── icons/             # generated from icons/soradesk-icon.svg
    ├── tauri.conf.json    # bundle, signing, and updater config
    ├── tauri.release.conf.json  # release-only updater artifacts
    └── src/
        ├── transport.rs   # profile → "a reachable ws:// URL"
        ├── tunnel.rs      # supervised `ssh -N -L` tunnel
        ├── config.rs      # persisted connection profiles + device tokens
        └── ws.rs          # protocol client, status machine, reconnect loop
```

The load-bearing idea: **all three connection modes are the same protocol**. SSH is a
tunnel, not a transport — see [`docs/connections.md`](docs/connections.md).

Design rules:

- one protocol everywhere, even locally — no second code path;
- pairing-code device authorization (revocable), never passwords;
- direct IP is opt-in, requires TLS + token, and plaintext `ws://` is refused for
  non-loopback hosts;
- read-only views (`calendar.list`, `memory.stats`) surface SoraAgent's own state instead of
  duplicating it in the client.

## Connecting the real thing

1. Install SoraAgent and the bridge in the same Python environment (above). The
   `sora-desktop` console script runs the WebSocket bridge and manages pairing.
2. In Settings, add a profile:
   - **local** — reads `~/.sora_agent/run/desktop.json` for the port (optionally start/stop the
     bridge via the `sora-desktop` CLI);
   - **SSH** — a profile name, `user@host` target (for example
     `ubuntu@203.0.113.10`), SSH port (default `22`), the private-key file, and the
     remote loopback port (default `18791`); the app supervises
     `ssh -N -L -i <key>` with `IdentitiesOnly=yes`;
   - **IP** — `wss://host:port`, opt-in.
3. If `trust_localhost` is off, approve the pairing code with
   `sora-desktop pairing approve <code>`. The device token is stored per profile.

Bridge files live under `~/.sora_agent/desktop/` (config, pairing ledger, media, log), and
run-state is `~/.sora_agent/run/desktop.json`. Conversations are SoraAgent's own session store
under `~/.sora_agent/sessions/`.

## Attachments

Composer media follows [`docs/protocol.md`](docs/protocol.md) §3.1: every file is uploaded with
`media.upload` (base64, standard alphabet) and then referenced by id from `chat.send`. Photos,
voice notes, audio, video and arbitrary files are all the same frame.

- Sources: the paperclip picker, drag & drop onto the chat view, paste from the clipboard, and
  the microphone (voice notes) via `MediaRecorder`;
- kinds: `image` (click to open), `audio` / `video` (inline player), `file` (chip);
- sizing: the client pre-checks against `hello.limits.max_upload_bytes` (falling back to 25 MiB),
  the bridge cap is `max_upload_mb` in the bridge config (default 25) and answers `too_large`
  beyond it; a frame past the transport's own maximum closes the socket with 1009 instead;
- reloads: `sessions.history` repeats the attachment records, and when the bridge advertises
  `hello.limits.max_fetch_bytes` the client fetches the bytes back over `media.fetch` to restore
  thumbnails and players (`media_gone` / `too_large` fall back to the chip, and results are
  cached per id);
- audio attachments go through SoraAgent's file understander when one is configured (the
  transcript is appended to the message the model sees);
- recording needs macOS microphone permission — `src-tauri/Info.plist` carries the usage
  description and `src-tauri/Entitlements.plist` the hardened-runtime audio entitlement, so the
  prompt only appears for a bundled build.

## Queueing

SoraAgent runs one turn per session: there is no mid-turn injection. While Sora answers, the
composer stays usable:

- **Enter** queues the draft. The client holds it and sends it as the next turn when the
  running one ends (FIFO, per session); queued chips above the input show what is waiting —
  remove one with its ✕.
- **Esc** (or **Stop**) interrupts the turn.

The bridge advertises `features: []`, so steering is not offered by the UI.

## Updates

Settings → Updates checks the manifest at the endpoint in `tauri.conf.json`
(`https://github.com/Mochi-Sora/SoraDesk/releases/latest/download/latest.json`).
Update packages are signed with the Tauri updater key; the private key never lives in
the repository. Release notes are in [CHANGELOG.md](CHANGELOG.md). The tag workflow builds macOS (universal), Linux, and Windows assets,
notarizes macOS when the Apple secrets are present, signs Windows when the Authenticode
secrets are present, and uploads `latest.json`. v0.2.2 was published without Apple
notarization or Windows Authenticode, so those installers are unsigned; the updater
packages are signed. See [`docs/release.md`](docs/release.md).

## Not done yet

- macOS notarization and Windows Authenticode are not configured yet. The v0.1.1
  installers are unsigned; Gatekeeper and SmartScreen will warn on first launch — see
  [`docs/release.md`](docs/release.md).
- A signed follow-up release is planned once the Apple and Windows certificates are
  available.

## SoraPlatform

SoraDesk is part of [SoraPlatform](https://github.com/Mochi-Sora), an umbrella of
open-source, independent products built against telemetry and bloat. The platform also
includes [SoraAgent](https://github.com/Mochi-Sora/SoraAgent), the runtime this client
talks to; [SoraSkills](https://www.agensi.io/creators/mochi-sora), framework-agnostic
agent skills published on Agensi; and the rest of the ten-product roadmap. The official
site lives at
[SoraOfficialWebsite](https://github.com/Mochi-Sora/SoraOfficialWebsite).

## Licence

**AGPL-3.0-only** — full text in [`LICENSE`](LICENSE); the copyright holder and the
standard application notice are in [`NOTICE`](NOTICE). © 2026 Tam Kuan Hou
(譚君浩 / Marco Tam), GitHub: [Mochi-Sora](https://github.com/Mochi-Sora). SoraDesk is a
separate work licensed the same way as the
[SoraAgent runtime](https://github.com/Mochi-Sora/SoraAgent) it talks to.
