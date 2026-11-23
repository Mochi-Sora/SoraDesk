# Releasing SoraDesk

SoraDesk ships as a Tauri v2 desktop bundle. Tagged builds are produced by
[`.github/workflows/release.yml`](../.github/workflows/release.yml); the in-app
**Settings → Updates** button reads the updater manifest from GitHub Releases.

## Release status

v0.2.3 is published. The updater key is configured as
`TAURI_SIGNING_PRIVATE_KEY`, so every release produces signed updater artifacts.
Apple notarization and Windows Authenticode secrets are not configured yet; the
v0.2.3 macOS and Windows installers are unsigned. Gatekeeper and SmartScreen will
warn on first launch. A signed follow-up release will be cut once the certificates
are available.

## Updater key

The public key is committed in
[`src-tauri/tauri.conf.json`](../src-tauri/tauri.conf.json) under
`plugins.updater.pubkey`. The matching private key for this checkout was generated at:

```text
~/.tauri/soradesk-updater.key
```

It is unencrypted (empty password) and mode `0600`. Back it up somewhere safe and
**never commit it**. If you prefer a password, regenerate the pair before the first
public release and replace the public key in the config:

```sh
npm run tauri -- signer generate -w ~/.tauri/soradesk-updater.key -f
```

Losing the private key means you cannot sign future updates. Rotating the key after
users have installed a build requires publishing an intermediate release signed with
the old key; ship the new public key in that build first.

## Build a bundle locally

A normal build produces the `.app` / `.dmg` (or the platform equivalents) without
updater artifacts:

```sh
npm ci
npm run tauri build
```

A release build adds the signed updater artifacts:

```sh
export TAURI_SIGNING_PRIVATE_KEY="$HOME/.tauri/soradesk-updater.key"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

npm run tauri build -- --config src-tauri/tauri.release.conf.json
```

`src-tauri/tauri.release.conf.json` only turns on `bundle.createUpdaterArtifacts`; keep
it out of the default config so ordinary builds do not need a signing key.

## macOS signing and notarization

Tauri picks these up automatically when they are set:

```sh
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="you@example.com"
export APPLE_PASSWORD="app-specific-password"
export APPLE_TEAM_ID="TEAMID"
```

In CI the certificate is imported from `APPLE_CERTIFICATE` (base64-encoded `.p12`)
and `APPLE_CERTIFICATE_PASSWORD`. Export the certificate and its private key from
Keychain Access as a `.p12`, then encode it:

```sh
openssl base64 -A -in DeveloperID.p12 -out apple-certificate-base64.txt
```

`APPLE_PASSWORD` is an app-specific password, not your Apple ID password. Creating
the certificate requires a Mac and a paid Apple Developer account; the CI build runs
on GitHub's macOS runner. Without these secrets the macOS job still produces an
ad-hoc-signed build, but Gatekeeper will warn on other machines and the release is
not notarized. This is the state of the v0.2.3 macOS build.

The webview records voice notes, so `src-tauri/Info.plist` carries the microphone
usage string and `src-tauri/Entitlements.plist` grants the hardened-runtime audio
input entitlement.

## Windows signing

Windows Authenticode needs a code-signing certificate exported as a `.pfx`. The
release workflow imports it on the Windows runner from `WINDOWS_CERTIFICATE`
(base64-encoded `.pfx`) and `WINDOWS_CERTIFICATE_PASSWORD`, reads the resulting
thumbprint, and writes it into `src-tauri/tauri.release.conf.json` for that build.
It signs with SHA-256 and timestamps via `http://timestamp.digicert.com`; change the
URL in the workflow if your certificate authority provides a different timestamp
server.

Without these secrets the Windows job still builds and uploads installers, but they
are unsigned and SmartScreen will warn until the certificate builds reputation. This
is the state of the v0.2.3 Windows installer.

Encode the `.pfx` as a single line before adding it as a secret:

```sh
# Linux/macOS
base64 -w0 certificate.pfx > windows-certificate-base64.txt

# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("certificate.pfx")) | Set-Content windows-certificate-base64.txt
```

## GitHub repository and secrets

The updater endpoint is configured for `Mochi-Sora/SoraDesk`. If the repository lives
somewhere else, update the URL in `src-tauri/tauri.conf.json` before releasing.

The repository is already initialized and pushed. To recreate it from a fresh
checkout:

```sh
git init -b main
git add .
git commit -m "SoraDesk 0.2.1"
gh repo create Mochi-Sora/SoraDesk --public --source=. --remote=origin --push
```

Use `--private` instead of `--public` if the repository should stay private; the
updater endpoint works with either, as long as the release assets are reachable by
the app.

Set the release secrets (repository → Settings → Secrets and variables → Actions, or
`gh secret set`):

| Secret | Purpose |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | Contents of `~/.tauri/soradesk-updater.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Key password; omit for an unencrypted key |
| `APPLE_CERTIFICATE` | Base64 `.p12` Developer ID certificate |
| `APPLE_CERTIFICATE_PASSWORD` | Password for the `.p12` |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: …` |
| `APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID` | Notarization credentials |
| `WINDOWS_CERTIFICATE` | Base64 `.pfx` Authenticode certificate (optional) |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password for the `.pfx` (optional) |

`GITHUB_TOKEN` is provided by Actions.

## Cut a release

1. Bump the version everywhere it is stamped: `package.json` and
   `package-lock.json` (`npm version 0.2.4 --no-git-tag-version` updates both),
   `src-tauri/tauri.conf.json`, `src/protocol.ts` (`APP_VERSION`),
   `src-tauri/Cargo.toml` with its `Cargo.lock` entry, and the bridge
   (`bridge/pyproject.toml`, `bridge/sora_desktop/__init__.py`, and the version
   assertion in `bridge/tests/smoke.py`). Give the CHANGELOG section a released
   heading (start a new `[Unreleased]` heading at the top when the next cycle
   begins) and refresh the README badge. Run
   `npm run check-versions` to confirm they agree — CI enforces the
   package/tauri/protocol trio.
2. Commit, then tag and push:

   ```sh
   git tag v0.2.3
   git push origin v0.2.3
   ```

3. The workflow creates a **draft** GitHub release with the bundles, updater
   signatures, and `latest.json`, then runs **Verify release completeness**
   (`dev/check-release.mjs`). That job asserts the manifest names the tagged
   version, lists every platform from the build matrix, points only at attached
   assets, and — once the release is public — that every platform URL returns 200.
   Each matrix leg uploads its own `latest.json`, so this is the signal that the
   last build has landed.
4. **Wait for the completeness job to pass before publishing.** Publishing while a
   platform is still building advertises a partial manifest, and clients on the
   missing platform fail their update check with
   `None of the fallback platforms … were found in the response platforms object`.
   If that happens, set the release back to draft until the job is green, or
   re-run it after the manifest is repaired:

   ```sh
   node dev/check-release.mjs --tag v0.2.3
   ```
5. Download and smoke-test the draft assets, then publish the release.
6. The updater endpoint
   (`https://github.com/Mochi-Sora/SoraDesk/releases/latest/download/latest.json`)
   only serves published releases, so existing clients see the update after step 5.

## Security notes

- Tauri verifies every update against the public key in the app; a compromised GitHub
  release without the private key still cannot install an update.
- Never put the private key, its password, or the Apple certificate in the repo.
- Keep a backup of the updater key outside this machine.
