# Connection modes — local, SSH, direct IP

The app has exactly one protocol ([`protocol.md`](./protocol.md)) and exactly one client
implementation. A *connection mode* only decides **how a reachable `ws://` URL is
obtained**. Nothing above that boundary ever branches on the mode.

## The three modes

| Mode | How the URL is obtained | Notes |
|---|---|---|
| **Local** | Read the bridge run-state file, dial `ws://127.0.0.1:<port>` | Optionally start/stop the bridge through the `sora-desktop` CLI |
| **SSH** (default remote) | Supervise `ssh -N -L -i <key> …`, dial `ws://127.0.0.1:<localport>` | Profile target (`user@host`), port (`22` default), and private key; `~/.ssh/config` aliases still resolve |
| **IP** (opt-in) | Dial the profile's `wss://host:port` | Requires TLS + token; gated behind a warning |

## Local mode

Run-state file: `~/.sora_agent/run/desktop.json` — written by the bridge, read by the app.

- The reader is **tolerant**: keys are matched case/spacing-insensitively (`port`,
  `Port`, `pid`, `launch_mode`, `started_at`, `log_path`…), because the exact serialization
  is the bridge's business.
- If the file is missing or has no port, the app shows "bridge not running" and offers
  `sora-desktop start` (executed through the CLI, never in a shell).
- The memories screen gets its data from the protocol's `memory.stats` frame, which combines
  SoraAgent's `MemoryHook.doctor()` diagnostics with database/daily-store byte sizes.

CLI actions exposed in Settings (local mode only, spawn without a shell):

```
sora-desktop start
sora-desktop status | restart | stop
sora-desktop logs --tail 80 --no-follow
```

The binary is resolved in this order: `$SORA_DESKTOP_BIN`, `~/.local/bin/sora-desktop`,
`~/.sora_agent/venv/bin/sora-desktop`, `/opt/homebrew/bin/sora-desktop`,
`/usr/local/bin/sora-desktop`, then `PATH`.

## SSH mode

Implemented as a **tunnel only** — the app supervises the system `ssh` binary and then dials
loopback, exactly like local mode.

```
ssh -N -T \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=accept-new \
    [-p <ssh_port>] \
    -i <private_key> \
    -o IdentitiesOnly=yes \
    -L 127.0.0.1:<localport>:127.0.0.1:<remote_port> \
    <target>
```

- `<target>` is `user@host` as you would type it after `ssh`, for example
  `ubuntu@203.0.113.10`. A plain `~/.ssh/config` alias still resolves (including
  `ProxyJump`), but the profile's SSH port takes precedence over the alias's `Port`.
- `<private_key>` is the private-key file selected in the profile. `~/` is expanded,
  the file must exist, and `IdentitiesOnly=yes` makes ssh offer only that key instead of
  exhausting the agent's identity limit.
- `ssh_port` defaults to `22`; enter a custom port for servers that listen elsewhere.
- `remote_port` is the bridge port **on the remote machine's loopback** — default
  `18791`.
- `<localport>` is chosen by binding `127.0.0.1:0` and releasing it, then passed to `ssh`.
- `BatchMode=yes`: the app **never** prompts for or stores passwords. Authentication is
  the selected private key; an encrypted key must already be loaded in ssh-agent.
- `ExitOnForwardFailure=yes`: if the forward can't be established, `ssh` exits nonzero and
  the supervisor reports a real error instead of hanging.
- `-T -N`: no remote command, no TTY.

**Supervisor duties** (Rust, `src-tauri/src/tunnel.rs` + `ws.rs`):

1. Spawn; poll TCP `127.0.0.1:<localport>` (250 ms) until reachable, up to 20 s.
2. If `ssh` exits or the port stops answering, respawn with exponential backoff
   (1 s → 30 s, jittered).
3. Kill the child on disconnect and on app exit (`kill_on_drop` + explicit kill).

A healthy tunnel is **kept across WebSocket reconnects**; it is only torn down when it fails
a health check or the user disconnects.

## Direct IP mode (opt-in, later)

- URL must be `wss://…` **unless** the host is loopback (`ws://` to loopback is allowed for
  development). The app refuses plaintext `ws://` to non-loopback hosts: the bridge is
  designed for loopback or a TLS-terminating tunnel, and direct exposure is opt-in.
- A device token is required; the pairing handshake is identical.
- The UI states the trade-off explicitly before this mode can be saved.

## Profiles

Persisted in the Tauri app-config directory (`profiles.json`):

| field | meaning |
|---|---|
| `id`, `name` | identity + display name |
| `mode` | `local` \| `ssh` \| `ip` |
| `sshHost`, `sshPort?`, `sshKey?`, `sshRemotePort?` | SSH target, port (default 22), private-key path, remote bridge port |
| `url` | IP mode endpoint (`wss://…`) |
| `token` | device token issued by the pairing handshake (revocable) |
| `autoConnect?` | connect on app start |

Rules:

- No passwords, ever — not stored, not prompted. The profile stores the **path** to the
  private key, never the key material; ssh reads the file directly. The only secret in the
  profile file is the revocable device token.
- Tokens are replaced automatically when the server rotates them, and are cleared with
  "Forget token" in Settings (the owner should also `sora-desktop pairing revoke <token-prefix>`
  on the bridge host).

## Reconnect and status

One status machine is shared by all modes (`connecting → connected → reconnecting →
disconnected | error | unauthorized`). Backoff and stop conditions are specified in
[`protocol.md` §7](./protocol.md#7-client-reconnect-policy).
