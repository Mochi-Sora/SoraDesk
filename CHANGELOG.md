# Changelog

All notable changes to SoraDesk are documented here.

## [0.2.3] - 2026-11-23

### Fixed

- Sidebar: the paired badge now hugs its text instead of stretching across the
  footer.
- Chat: long session titles ellipsize to one line, and **New session** / **Stop**
  keep their labels on one line at the 900 px minimum window width.
- Topbar: long connection-profile names and status text truncate cleanly, and
  the profile picker is capped so the status chip and connect button stay
  visible.
- Approvals: cards are scoped to the open conversation and are removed when
  their turn ends or the connection closes, so timed-out approvals no longer
  linger above the composer.
- Specialists: empty-state commands render as inline code instead of literal
  backticks.
- Memories: the card grid uses wider columns, keeping a 4×2 layout on desktop and
  two columns at the minimum width without splitting paths mid-word.

### Changed

- App, protocol, Tauri, Cargo, and bridge versions are 0.2.3.

## [0.2.2] - 2026-11-17

### Added

- Desktop bridge now passes session-state configuration to SoraAgent 0.3.0+
  (`features.state`, `SORA_STATE`, `SORA_STATE_ROOT`).

### Changed

- Requires SoraAgent 0.3.0 or newer for the state-aware `ChatHandler`.
- App, protocol, Tauri, Cargo, and bridge versions are 0.2.2.

## [0.2.1] - 2026-11-17

### Fixed

- The Specialists view now checks the bridge's advertised `features` before
  requesting `specialists.list`. Older bridges show an actionable
  "update the `sora-desktop` bridge" message instead of an `unknown request
  type` error.
- The bridge advertises `specialists` and reports its own version in `hello`
  (`bridge_version`), so the client can distinguish an old bridge from a
  SoraAgent/runtime problem.
- `sora-desktop --version` now reads the bridge package version instead of a
  hard-coded string.

### Changed

- Bridge package version is 0.2.1. Updating the app alone is not enough:
  reinstall/restart the bridge in the same Python environment as SoraAgent.

## [0.2.0] - 2026-11-16

### Added

- **Specialists** view and bridge API (`specialists.list`, `set_status`,
  `delete`, `promote`) for persistent delegation agents created by SoraAgent's
  `sora delegate` CLI or opt-in auto-creation.

### Changed

- The bridge forwards SoraAgent's specialist runner and registry into the chat
  handler, so the main agent can route work to specialists. This requires
  SoraAgent 0.2.0 or newer.
- App, protocol, Tauri, and Cargo versions are now 0.2.0.

## [0.1.1] - 2026-11-13

### Added

- SSH connection profiles now ask for a profile name, a `user@host` target, an
  SSH port (default `22`), and a private-key file. The app passes the key with
  `ssh -i` and `IdentitiesOnly=yes`.
- Native private-key picker in **Settings → Connection profiles** (Tauri dialog
  plugin).

### Changed

- SSH profile validation requires a target and a key file; the remote bridge
  port defaults to `18791`.
- `~/.ssh/config` aliases still resolve, but the profile's port takes precedence
  over an alias's `Port`.
- README and connection docs describe the new profile fields and key handling.

## [0.1.0] - 2026-11-09

Initial release: the Tauri v2 client, the local `sora_desktop` bridge, local /
SSH-tunnel / direct-IP connections, chat with streaming and approvals, sessions,
calendar, memories, settings, attachments, and signed updater artifacts.
