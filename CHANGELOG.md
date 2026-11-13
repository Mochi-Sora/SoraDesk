# Changelog

All notable changes to SoraDesk are documented here.

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
