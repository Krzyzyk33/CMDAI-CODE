#!/usr/bin/env node
// cmdai-code npm wrapper: bootstraps the Python app from a fixed location
// (~/CMDAI-CODE or CMDAI_CODE_ROOT) and forwards all commands to it.
// Usage: npx -y cmdai-code | npm i -g cmdai-code && cmdai
// ("code" is the app subcommand; the wrapper adds it when no args given,
// so the package name is typed only once.)
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const REPO_URL = "https://github.com/Krzyzyk33/CMDAI-CODE.git";
const FIXED_DIR_NAME = "CMDAI-CODE";

function resolveRoot() {
  const override = (process.env.CMDAICODE_ROOT || process.env.CMDAI_CODE_ROOT || "").trim();
  if (override) return path.resolve(override);
  return path.join(os.homedir(), FIXED_DIR_NAME);
}

function fail(msg) {
  process.stderr.write(`cmdai: ${msg}\n`);
  process.exit(1);
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (res.error) return { ok: false, code: 1, error: res.error };
  return { ok: res.status === 0, code: res.status ?? 1 };
}

function runCapture(cmd, args) {
  const res = spawnSync(cmd, args, { encoding: "utf-8" });
  if (res.error || res.status !== 0) return null;
  return (res.stdout || "").trim();
}

function findGit() {
  const out = runCapture("git", ["--version"]);
  return out ? "git" : null;
}

function findPython() {
  const cands = process.platform === "win32" ? ["python", "py", "python3"] : ["python3", "python"];
  for (const c of cands) {
    const args = c === "py" ? ["-3", "--version"] : ["--version"];
    if (runCapture(c, args) !== null) return c === "py" ? { cmd: "py", prefix: ["-3"] } : { cmd: c, prefix: [] };
  }
  return null;
}

function ensureCheckout(root) {
  const entry = path.join(root, "cmdai.py");
  if (fs.existsSync(entry)) return;
  const git = findGit();
  if (!git) fail(`target checkout not found at ${root} and git is missing. Install git or set CMDAI_CODE_ROOT.`);
  process.stderr.write(`cmdai: cloning ${REPO_URL} to ${root} ...\n`);
  fs.mkdirSync(path.dirname(root), { recursive: true });
  const r = run(git, ["clone", REPO_URL, root]);
  if (!r.ok || !fs.existsSync(entry)) fail(`git clone failed for ${REPO_URL}`);
}

function ensureFirstRun(root, py) {
  const cfg = path.join(root, "config.json");
  if (fs.existsSync(cfg)) return;
  process.stderr.write("cmdai: first run - installing dependencies...\n");
  const req = path.join(root, "requirements.txt");
  if (fs.existsSync(req)) {
    const r = run(py.cmd, [...py.prefix, "-m", "pip", "install", "-r", req], { cwd: root });
    if (!r.ok) fail("'pip install -r requirements.txt' failed. Fix the error above and retry.");
  }
  const example = path.join(root, "config.example.json");
  if (fs.existsSync(example) && !fs.existsSync(cfg)) fs.copyFileSync(example, cfg);
  for (const d of ["models", "logs", "cache", path.join("app", "sessions")]) {
    fs.mkdirSync(path.join(root, d), { recursive: true });
  }
  if (process.platform === "win32") {
    const entry = path.join(root, "cmdai.py");
    if (fs.existsSync(entry)) {
      process.stderr.write("cmdai: registering global 'cmdai code' command...\n");
      run(py.cmd, [...py.prefix, entry, "--install-launcher"], { cwd: root });
    }
  }
}

function main() {
  const root = resolveRoot();
  ensureCheckout(root);
  const py = findPython();
  if (!py) fail("Python 3.10+ not found in PATH. Install it from https://www.python.org/downloads/ and retry.");
  ensureFirstRun(root, py);
  const entry = path.join(root, "cmdai.py");
  const userArgs = process.argv.slice(2);

  // --workdir goes first, ahead of the user's own words. That used to break
  // subcommands: cmdai.py matched `update` only at position 0, so
  // `npx cmdai-code update` fell through to argparse and tried to launch the
  // TUI. The CLI now recognises a subcommand anywhere in the arguments, so this
  // ordering is safe - `code` is added only when the user typed nothing, to
  // keep the documented `cmdai code` form.
  const args = [...py.prefix, entry, "--workdir", process.cwd(), ...(userArgs.length ? userArgs : ["code"])];
  const res = spawnSync(py.cmd, args, { stdio: "inherit" });
  process.exit(res.status ?? 1);
}

main();
