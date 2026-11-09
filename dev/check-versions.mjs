// The app version appears in three places: package.json (JS metadata),
// tauri.conf.json (bundle + updater), and protocol.ts (reported in hello).
// They must agree or update checks compare the wrong versions.
import { readFile } from "node:fs/promises";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");

const pkg = JSON.parse(await read("package.json"));
const tauri = JSON.parse(await read("src-tauri/tauri.conf.json"));
const protocol = await read("src/protocol.ts");
const match = protocol.match(/export const APP_VERSION = "([^"]+)";/);

if (!match) {
  console.error("check-versions: APP_VERSION not found in src/protocol.ts");
  process.exit(1);
}

const versions = new Set([pkg.version, tauri.version, match[1]]);
if (versions.size !== 1) {
  console.error(
    `check-versions: mismatch — package.json=${pkg.version}, ` +
      `tauri.conf.json=${tauri.version}, protocol.ts=${match[1]}`,
  );
  process.exit(1);
}

console.log(`check-versions: ${pkg.version}`);
