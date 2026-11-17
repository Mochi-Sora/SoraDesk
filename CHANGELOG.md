# Changelog

All notable changes to SoraDesk are documented here.

## [0.2.1] - 2026-11-16

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
