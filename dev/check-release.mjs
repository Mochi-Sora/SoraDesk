#!/usr/bin/env node
// dev/check-release.mjs — verify that a SoraDesk GitHub release is complete.
//
//   node dev/check-release.mjs --tag v0.2.3
//   node dev/check-release.mjs --tag v0.2.3 --retry 120
//
// The release matrix uploads `latest.json` once per platform, so the manifest is
// only whole after the last build finishes. Publishing before then leaves the
// updater endpoint advertising a subset of platforms, and clients on the missing
// platforms fail their update check ("None of the fallback platforms … were
// found"). This check is the gate that catches that.
//
// It reads the release through the GitHub API (drafts need GITHUB_TOKEN), then
// asserts that latest.json exists, names the tagged version, lists every platform
// the matrix builds, and points at assets that are actually attached. For a
// published release it also confirms every platform URL returns 200.
//
// Exit code 0 means the release is complete; 1 means it is not (or could not be
// read). --retry N waits up to N seconds for the manifest to fill in, which
// absorbs the short window where jobs finish out of order.

const DEFAULTS = {
  repo: process.env.GITHUB_REPOSITORY ?? "Mochi-Sora/SoraDesk",
  // Keep in sync with the matrix in .github/workflows/release.yml.
  platforms: [
    "darwin-aarch64",
    "darwin-aarch64-app",
    "darwin-x86_64",
    "darwin-x86_64-app",
    "linux-x86_64",
    "linux-x86_64-appimage",
    "linux-x86_64-deb",
    "linux-x86_64-rpm",
    "windows-x86_64",
    "windows-x86_64-nsis",
  ],
};

const parseArgs = () => {
  const args = { tag: "", retry: 0, repo: DEFAULTS.repo, platforms: DEFAULTS.platforms };
  for (let i = 2; i < process.argv.length; i += 1) {
    const arg = process.argv[i];
    if (arg === "--tag") args.tag = process.argv[++i] ?? "";
    else if (arg === "--retry") args.retry = Number(process.argv[++i] ?? 0);
    else if (arg === "--repo") args.repo = process.argv[++i] ?? DEFAULTS.repo;
    else if (arg === "--help" || arg === "-h") {
      console.log("usage: check-release.mjs --tag vX.Y.Z [--retry seconds] [--repo owner/name]");
      process.exit(0);
    } else {
      console.error(`check-release: unknown argument ${arg}`);
      process.exit(2);
    }
  }
  if (!args.tag) {
    console.error("check-release: --tag is required (for example --tag v0.2.3)");
    process.exit(2);
  }
  if (!Number.isFinite(args.retry) || args.retry < 0) {
    console.error("check-release: --retry must be a non-negative number of seconds");
    process.exit(2);
  }
  return args;
};

const fail = (message) => {
  console.error(`check-release: ${message}`);
  process.exit(1);
};

const headers = (accept) => {
  const result = {
    Accept: accept,
    "User-Agent": "soradesk-check-release",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (process.env.GITHUB_TOKEN) result.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  return result;
};

const getRelease = async (repo, tag) => {
  const response = await fetch(`https://api.github.com/repos/${repo}/releases/tags/${tag}`, {
    headers: headers("application/vnd.github+json"),
  });
  if (response.status === 404) fail(`release ${tag} does not exist in ${repo}`);
  if (!response.ok) fail(`release lookup failed: HTTP ${response.status} ${await response.text()}`);
  return response.json();
};

const readManifestText = async (repo, asset) => {
  // The API asset endpoint works for draft releases when GITHUB_TOKEN is set and
  // for published releases without one; browser_download_url does not work for
  // drafts.
  const response = await fetch(
    `https://api.github.com/repos/${repo}/releases/assets/${asset.id}`,
    { headers: headers("application/octet-stream"), redirect: "follow" },
  );
  if (!response.ok) fail(`latest.json download failed: HTTP ${response.status}`);
  return response.text();
};

const headOk = async (url) => {
  const response = await fetch(url, { method: "HEAD", redirect: "follow" });
  return response.status;
};

/** Returns a list of problems; an empty list means the release is complete. */
const inspect = async (release, args, manifestText) => {
  const problems = [];
  const expectedVersion = args.tag.replace(/^v/, "");
  const assets = new Map(release.assets.map((asset) => [asset.name, asset]));

  if (!manifestText) {
    return [`release has no latest.json asset (found: ${[...assets.keys()].join(", ") || "none"})`];
  }

  let manifest;
  try {
    manifest = JSON.parse(manifestText);
  } catch (error) {
    return [`latest.json is not valid JSON: ${error.message}`];
  }

  if (manifest.version !== expectedVersion) {
    problems.push(`latest.json version is ${manifest.version}, expected ${expectedVersion}`);
  }

  const platforms = Object.keys(manifest.platforms ?? {});
  const missing = args.platforms.filter((platform) => !platforms.includes(platform));
  if (missing.length > 0) problems.push(`latest.json is missing platforms: ${missing.join(", ")}`);

  for (const platform of args.platforms) {
    const entry = manifest.platforms?.[platform];
    if (!entry?.url) continue;
    const file = entry.url.split("/").pop() ?? "";
    if (!assets.has(file)) {
      problems.push(`${platform} points at ${file}, which is not attached to the release`);
    }
    if (!entry.signature) problems.push(`${platform} has no signature`);
  }

  // A draft's asset URLs are not publicly reachable yet, so link checks only make
  // sense once the release is public.
  if (!release.draft && problems.length === 0) {
    for (const platform of args.platforms) {
      const url = manifest.platforms[platform].url;
      const status = await headOk(url);
      if (status !== 200) problems.push(`${platform} URL returns HTTP ${status}: ${url}`);
    }
  }

  return problems;
};

const main = async () => {
  const args = parseArgs();
  const deadline = Date.now() + args.retry * 1000;
  let problems = [];
  let release;

  for (;;) {
    release = await getRelease(args.repo, args.tag);
    const asset = release.assets.find((candidate) => candidate.name === "latest.json");
    const manifestText = asset ? await readManifestText(args.repo, asset) : null;
    problems = await inspect(release, args, manifestText);

    if (problems.length === 0 || Date.now() >= deadline) break;
    console.log(
      `check-release: ${problems.length} problem(s) after ${new Date().toISOString()}, ` +
        `retrying in 15 s (${Math.ceil((deadline - Date.now()) / 1000)} s left)`,
    );
    await new Promise((resolve) => setTimeout(resolve, 15_000));
  }

  if (problems.length === 0) {
    const state = release.draft ? "draft" : "published";
    console.log(
      `check-release: ${args.tag} is complete (${state}) — ` +
        `${args.platforms.length} platforms, every asset attached`,
    );
    return;
  }

  console.error(`check-release: ${args.tag} is incomplete (${problems.length} problem(s))`);
  for (const problem of problems) console.error(`  - ${problem}`);
  if (!release.draft) {
    console.error(
      "  the release is already public: clients on the missing platforms fail their update " +
        "check until latest.json lists them",
    );
  }
  process.exit(1);
};

await main();
