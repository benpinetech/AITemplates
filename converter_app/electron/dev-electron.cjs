// Dev-only launcher: runs Electron and restarts the MAIN process whenever
// main.cjs or preload.cjs change.
//
// Vite already hot-reloads the renderer, but the Electron main process is
// launched once and never reloaded — so edits to it (e.g. a newly added IPC
// handler) are silently ignored until a full manual restart. That gap shows
// up in the renderer as "No handler registered for '<channel>'". This watcher
// closes it with no extra dependency (electronmon/nodemon aren't installed).
//
// Used by ``npm run dev:electron``. Not used by the packaged app or the
// Playwright e2e tests (those launch Electron directly).

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const electronBin = require("electron"); // the electron pkg exports the binary path

const ELECTRON_DIR = __dirname; // converter_app/electron
const APP_ROOT = path.resolve(ELECTRON_DIR, ".."); // converter_app
const WATCH_FILES = ["main.cjs", "preload.cjs"].map((f) => path.join(ELECTRON_DIR, f));

let child = null;
let restarting = false;

function start() {
  child = spawn(electronBin, ["."], { cwd: APP_ROOT, stdio: "inherit", env: process.env });
  child.on("exit", (code) => {
    // A normal exit (user closed the window) tears the watcher down too, so
    // ``concurrently -k`` ends the whole dev session. A restart-triggered
    // exit is handled by the one-shot listener in restart().
    if (!restarting) process.exit(code == null ? 0 : code);
  });
}

function restart() {
  if (!child || restarting) return;
  restarting = true;
  process.stdout.write("\n[dev-electron] main/preload changed — restarting Electron…\n");
  child.once("exit", () => { restarting = false; start(); });
  child.kill("SIGTERM");
}

let debounce = null;
for (const file of WATCH_FILES) {
  try {
    fs.watch(file, () => {
      clearTimeout(debounce);
      debounce = setTimeout(restart, 150); // coalesce editors' multi-write saves
    });
  } catch {
    /* file may not exist yet — skip */
  }
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => { if (child) child.kill(sig); process.exit(0); });
}

start();
