// Electron main process. Owns:
//   - the BrowserWindow lifecycle
//   - the preload script registration (security boundary)
//   - the IPC handlers that spawn the Python pipeline as a sidecar
//
// The renderer never touches Node directly. Everything that needs
// filesystem or process access is invoked via window.api (see
// preload.cjs).

const { app, BrowserWindow, Menu, dialog, ipcMain } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

// Dev mode means "load the renderer from the Vite dev server". When
// running automated tests we want the prebuilt ``dist/`` bundle even
// though the app isn't actually packaged — set ``JDAPINE_USE_DIST=1``
// to force that path.
const isDev = !app.isPackaged && !process.env.JDAPINE_USE_DIST;

// Repo root — used to locate the Python venv + the v2 tooling in dev
// mode. In packaged mode the Python sidecar will be bundled as a
// PyInstaller binary under process.resourcesPath/binaries/. Both
// paths share the same JSON wire format (see convert.py --json).
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const VENV_PYTHON = path.join(REPO_ROOT, "venv", "bin", "python");
const SIDECAR_NAME =
  process.platform === "win32" ? "jda_pine_sidecar.exe" : "jda_pine_sidecar";

// Load the repo-root .env (OPENAI_API_KEY, OPENAI_MODEL, etc.) into
// process.env at startup so the Python pipeline child inherits the
// LLM credentials without the user having to ``export`` them in the
// shell before launching. Values already set in the shell win — we
// don't clobber. Values set later by the Settings dialog still win
// via ``pipelineEnv()``.
function loadRepoDotEnv() {
  const envPath = path.join(REPO_ROOT, ".env");
  let text;
  try {
    text = fs.readFileSync(envPath, "utf-8");
  } catch {
    return;            // .env is optional
  }
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = val;
  }
}
loadRepoDotEnv();

// User preferences live next to the app's other userData. Plain JSON
// for now — keytar would be safer for the API key but adds a native
// build dep; the file is local-user-only and not shipped anywhere.
function settingsPath() {
  return path.join(app.getPath("userData"), "settings.json");
}

function readSettings() {
  try {
    const raw = fs.readFileSync(settingsPath(), "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed;
  } catch {
    // missing / unreadable / not-JSON → fall through to defaults
  }
  return {};
}

function writeSettings(next) {
  const file = settingsPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(next, null, 2), "utf-8");
}

function pipelineEnv() {
  // Settings override the inherited env so the user's choices win
  // over whatever shell launched the app.
  const s = readSettings();
  const env = { ...process.env };
  if (s.api_key) env.OPENAI_API_KEY = s.api_key;
  if (s.model) env.OPENAI_MODEL = s.model;
  // In packaged mode, suggestions must live outside the read-only bundle.
  // Point the sidecar at a writable userData directory.
  if (app.isPackaged) {
    env.JDA_SUGGESTIONS_DIR = path.join(app.getPath("userData"), "suggestions");
  }
  return env;
}

// ─────────────────────────────────────────────────────────────────────
// Application menu
// ─────────────────────────────────────────────────────────────────────

function buildMenu() {
  const isMac = process.platform === "darwin";
  const sendToFocused = (channel) => {
    const win = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0];
    if (win) win.webContents.send(channel);
  };
  const openSettings = () => sendToFocused("open-settings");
  const openMappings = () => sendToFocused("open-mappings");
  const openHelp = () => sendToFocused("open-help");
  const openFile = () => sendToFocused("open-file");
  const saveFile = () => sendToFocused("save-file");
  const saveFileAs = () => sendToFocused("save-file-as");

  const template = [
    // macOS app menu — gets Preferences here per platform convention.
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { label: "Preferences…", accelerator: "Cmd+,", click: openSettings },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    }] : []),
    {
      label: "File",
      submenu: [
        { label: "Open…",    accelerator: isMac ? "Cmd+O"       : "Ctrl+O",       click: openFile },
        { label: "Save",     accelerator: isMac ? "Cmd+S"       : "Ctrl+S",       click: saveFile },
        { label: "Save As…", accelerator: isMac ? "Cmd+Shift+S" : "Ctrl+Shift+S", click: saveFileAs },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { label: "Undo", accelerator: isMac ? "Cmd+Z"       : "Ctrl+Z",       click: () => sendToFocused("app-undo") },
        { label: "Redo", accelerator: isMac ? "Cmd+Shift+Z" : "Ctrl+Shift+Z", click: () => sendToFocused("app-redo") },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { type: "separator" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "zoomIn" },
        { role: "zoomOut" },
        { role: "resetZoom" },
        { type: "separator" },
        { role: "togglefullscreen" },
        { type: "separator" },
        { role: "reload" },
      ],
    },
    {
      label: "Settings",
      submenu: [
        { label: "LLM Settings…",    accelerator: isMac ? "Cmd+," : "Ctrl+,", click: openSettings },
        { label: "Saved Mappings…",  click: openMappings },
      ],
    },
    {
      role: "help",
      submenu: [
        {
          label: "How to Use JDA to Pine Converter",
          accelerator: "F1",
          click: openHelp,
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0a0a0f",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      // sandbox=false so the preload can use the standard ipcRenderer
      // API. The contextBridge still isolates the renderer; we are
      // not giving it Node access.
      sandbox: false,
    },
  });

  if (isDev) {
    win.loadURL("http://localhost:1421");
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

// ─────────────────────────────────────────────────────────────────────
// IPC handlers
// ─────────────────────────────────────────────────────────────────────

ipcMain.handle("openRtf", async () => {
  const result = await dialog.showOpenDialog({
    title: "Open JDA RTF",
    filters: [
      { name: "RTF files", extensions: ["rtf"] },
      { name: "All", extensions: ["*"] },
    ],
    properties: ["openFile"],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  const filePath = result.filePaths[0];
  const rtf = fs.readFileSync(filePath, "utf-8");
  return { path: filePath, rtf };
});

ipcMain.handle("convertRtf", async (_evt, args = {}) => {
  const { path: rtfPath, org = "oba" } = args;
  if (!rtfPath) throw new Error("convertRtf: missing 'path'");
  return runPipeline(rtfPath, org);
});

ipcMain.handle("getSettings", async () => {
  const s = readSettings();
  return {
    api_key: s.api_key || "",
    model: s.model || "",
  };
});

ipcMain.handle("setSettings", async (_evt, next = {}) => {
  const current = readSettings();
  const merged = {
    ...current,
    api_key: typeof next.api_key === "string" ? next.api_key : current.api_key || "",
    model: typeof next.model === "string" ? next.model : current.model || "",
  };
  writeSettings(merged);
  return { ok: true };
});

ipcMain.handle("saveRtf", async (_evt, args = {}) => {
  const { defaultPath, rtf } = args;
  if (typeof rtf !== "string") throw new Error("saveRtf: missing 'rtf'");
  const result = await dialog.showSaveDialog({
    title: "Save Converted RTF",
    defaultPath: defaultPath || "converted.rtf",
    filters: [
      { name: "RTF files", extensions: ["rtf"] },
      { name: "All", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePath) return null;
  fs.writeFileSync(result.filePath, rtf, "utf-8");
  return { path: result.filePath };
});

ipcMain.handle("writeRtf", async (_evt, args = {}) => {
  const { path: filePath, rtf } = args;
  if (!filePath || typeof rtf !== "string") throw new Error("writeRtf: missing path or rtf");
  fs.writeFileSync(filePath, rtf, "utf-8");
  return { path: filePath };
});

// Crash-recovery snapshot file for a given source RTF. Keyed by a
// SHA-1 of the source path so we don't have to worry about
// filename-unsafe characters, and so the recovery dir stays flat.
function recoveryFile(sourcePath) {
  const dir = path.join(app.getPath("userData"), "recovery");
  fs.mkdirSync(dir, { recursive: true });
  const hash = crypto.createHash("sha1").update(sourcePath).digest("hex");
  return path.join(dir, `${hash}.json`);
}

ipcMain.handle("saveRecovery", async (_evt, args = {}) => {
  const { sourcePath, payload } = args;
  if (!sourcePath || typeof sourcePath !== "string") {
    return { error: "saveRecovery: missing sourcePath" };
  }
  try {
    const file = recoveryFile(sourcePath);
    const tmp = `${file}.tmp`;
    // Write-rename so a crash mid-write can't corrupt the snapshot.
    fs.writeFileSync(tmp, JSON.stringify({
      sourcePath,
      savedAt: Date.now(),
      payload,
    }), "utf-8");
    fs.renameSync(tmp, file);
    return { ok: true };
  } catch (e) {
    return { error: String(e?.message || e) };
  }
});

ipcMain.handle("loadRecovery", async (_evt, args = {}) => {
  const { sourcePath } = args;
  if (!sourcePath || typeof sourcePath !== "string") return null;
  try {
    const raw = fs.readFileSync(recoveryFile(sourcePath), "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
});

ipcMain.handle("clearRecovery", async (_evt, args = {}) => {
  const { sourcePath } = args;
  if (!sourcePath || typeof sourcePath !== "string") return { ok: true };
  try {
    fs.unlinkSync(recoveryFile(sourcePath));
  } catch { /* already gone — fine */ }
  return { ok: true };
});

ipcMain.handle("persistEdit", async (_evt, args = {}) => {
  // Spawns the persist_suggestion CLI with the payload as a single
  // JSON arg. We never want this to block the user — it returns
  // either { ok, path, removed } or { error } and the renderer can
  // decide what to surface. No exception propagation to the renderer.
  return runPersistEdit(args);
});

// ─────────────────────────────────────────────────────────────────────
// Pipeline dispatch — venv Python in dev, PyInstaller sidecar in prod
// ─────────────────────────────────────────────────────────────────────

/**
 * Spawn the conversion. Resolves with the parsed JSON bundle from
 * convert.py (schema: jda-pine-convert/v1).
 *
 * In dev mode we shell out to the project's venv python so we can
 * iterate on v2/ source without rebuilding anything. In packaged mode
 * we run the bundled PyInstaller sidecar binary (no Python required
 * on the user's machine).
 */
function runPipeline(rtfPath, org) {
  const usePackagedSidecar = app.isPackaged;
  const cmd = usePackagedSidecar
    ? path.join(process.resourcesPath, "binaries", SIDECAR_NAME)
    : VENV_PYTHON;
  const args = usePackagedSidecar
    ? ["convert", rtfPath, "--org", org, "--json"]
    : ["-m", "pipeline.tools.convert", rtfPath, "--org", org, "--json", "--quiet"];

  return new Promise((resolve, reject) => {
    if (!usePackagedSidecar && !fs.existsSync(VENV_PYTHON)) {
      reject(new Error(
        `Dev mode needs the project venv at ${VENV_PYTHON}. ` +
        `Run \`make install\` from the repo root.`,
      ));
      return;
    }
    const cwd = usePackagedSidecar ? path.dirname(cmd) : REPO_ROOT;
    const child = spawn(cmd, args, { cwd, env: pipelineEnv() });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => { stdout += d.toString("utf-8"); });
    child.stderr.on("data", (d) => {
      const s = d.toString("utf-8");
      stderr += s;
      // Forward python warnings + diagnostic prints to the Electron
      // main-process stdout in dev so we can see them in the terminal
      // running ``make run:converter``. Was previously swallowed,
      // which made "is the LLM firing?" impossible to verify.
      if (isDev) process.stderr.write(`[convert.py] ${s}`);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      // convert.py exit codes: 0 = ok, 1 = ok-with-validation-errors,
      // 2 = bad input/argument. Both 0 and 1 produce a valid JSON
      // bundle. Anything else means the pipeline blew up.
      if (code !== 0 && code !== 1) {
        reject(new Error(
          `pipeline exited ${code}${stderr ? `:\n${stderr.trim()}` : ""}`,
        ));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error(
          `pipeline produced non-JSON output. ` +
          `stdout head: ${stdout.slice(0, 200)}\n` +
          (stderr ? `stderr: ${stderr.trim()}` : ""),
        ));
      }
    });
  });
}

/**
 * Spawn the persist-suggestion CLI. Always resolves (never rejects);
 * shape is ``{ ok, path, removed }`` on success or ``{ error }`` on
 * failure. The CLI itself emits a JSON line on stdout in both cases
 * and exits 0/1 accordingly.
 */
function runPersistEdit(payload) {
  const usePackagedSidecar = app.isPackaged;
  const cmd = usePackagedSidecar
    ? path.join(process.resourcesPath, "binaries", SIDECAR_NAME)
    : VENV_PYTHON;
  const args = usePackagedSidecar
    ? ["persist_suggestion", "--payload", JSON.stringify(payload)]
    : ["-m", "pipeline.tools.persist_suggestion", "--payload", JSON.stringify(payload)];
  return new Promise((resolve) => {
    if (!usePackagedSidecar && !fs.existsSync(VENV_PYTHON)) {
      resolve({ error: `Dev mode needs the project venv at ${VENV_PYTHON}` });
      return;
    }
    const cwd = usePackagedSidecar ? path.dirname(cmd) : REPO_ROOT;
    const child = spawn(cmd, args, { cwd, env: pipelineEnv() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => { stdout += d.toString("utf-8"); });
    child.stderr.on("data", (d) => { stderr += d.toString("utf-8"); });
    child.on("error", (e) => resolve({ error: `spawn failed: ${e.message}` }));
    child.on("close", () => {
      try {
        resolve(JSON.parse(stdout.trim() || "{}"));
      } catch {
        resolve({
          error: `persist CLI emitted non-JSON: ${stdout.slice(0, 200)}` +
                 (stderr ? `\nstderr: ${stderr.trim()}` : ""),
        });
      }
    });
  });
}

/**
 * Spawn manage_suggestions.py with the given CLI args. Always resolves;
 * shape is the JSON the CLI prints, or { error } on spawn/parse failure.
 */
function runManageSuggestions(cliArgs) {
  const usePackagedSidecar = app.isPackaged;
  const cmd = usePackagedSidecar
    ? path.join(process.resourcesPath, "binaries", SIDECAR_NAME)
    : VENV_PYTHON;
  const args = usePackagedSidecar
    ? ["manage_suggestions", ...cliArgs]
    : ["-m", "pipeline.tools.manage_suggestions", ...cliArgs];
  return new Promise((resolve) => {
    if (!usePackagedSidecar && !fs.existsSync(VENV_PYTHON)) {
      resolve({ error: `Dev mode needs the project venv at ${VENV_PYTHON}` });
      return;
    }
    const cwd = usePackagedSidecar ? path.dirname(cmd) : REPO_ROOT;
    const child = spawn(cmd, args, { cwd, env: pipelineEnv() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => { stdout += d.toString("utf-8"); });
    child.stderr.on("data", (d) => { stderr += d.toString("utf-8"); });
    child.on("error", (e) => resolve({ error: `spawn failed: ${e.message}` }));
    child.on("close", () => {
      try {
        resolve(JSON.parse(stdout.trim() || "null"));
      } catch {
        resolve({
          error: `manage_suggestions emitted non-JSON: ${stdout.slice(0, 200)}` +
                 (stderr ? `\nstderr: ${stderr.trim()}` : ""),
        });
      }
    });
  });
}

/**
 * Spawn update_prelude.py with the RTF as stdin. Sends current Pine
 * token strings so the tool can strip the old CreateVar prelude and
 * regenerate from the live chip state. Always resolves; shape is
 * { ok: true, rtf: string } or { error: string }.
 */
function runUpdatePrelude(rtf, pineTokens, org = "oba", preludeCount = 0) {
  const usePackagedSidecar = app.isPackaged;
  const cmd = usePackagedSidecar
    ? path.join(process.resourcesPath, "binaries", SIDECAR_NAME)
    : VENV_PYTHON;
  const args = usePackagedSidecar
    ? ["update_prelude", "--tokens", JSON.stringify(pineTokens), "--org", org, "--prelude-count", String(preludeCount)]
    : ["-m", "pipeline.tools.update_prelude", "--tokens", JSON.stringify(pineTokens), "--org", org, "--prelude-count", String(preludeCount)];
  return new Promise((resolve) => {
    if (!usePackagedSidecar && !fs.existsSync(VENV_PYTHON)) {
      resolve({ error: `Dev mode needs the project venv at ${VENV_PYTHON}` });
      return;
    }
    const cwd = usePackagedSidecar ? path.dirname(cmd) : REPO_ROOT;
    const child = spawn(cmd, args, { cwd, env: pipelineEnv() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => { stdout += d.toString("utf-8"); });
    child.stderr.on("data", (d) => { stderr += d.toString("utf-8"); });
    child.on("error", (e) => resolve({ error: `spawn failed: ${e.message}` }));
    child.on("close", () => {
      try {
        resolve(JSON.parse(stdout.trim() || "{}"));
      } catch {
        resolve({
          error: `update_prelude emitted non-JSON: ${stdout.slice(0, 200)}` +
                 (stderr ? `\nstderr: ${stderr.trim()}` : ""),
        });
      }
    });
    // Write RTF to the child's stdin, then close it so the process knows
    // the input is complete.
    child.stdin.write(rtf, "utf-8");
    child.stdin.end();
  });
}

ipcMain.handle("refreshPrelude", async (_evt, { rtf, pine_tokens, org = "oba", prelude_count = 0 } = {}) => {
  if (typeof rtf !== "string") return { error: "refreshPrelude: missing rtf" };
  if (!Array.isArray(pine_tokens)) return { error: "refreshPrelude: missing pine_tokens" };
  return runUpdatePrelude(rtf, pine_tokens, org, prelude_count);
});

ipcMain.handle("listSuggestions", async (_evt, { org = "oba" } = {}) => {
  return runManageSuggestions(["--mode", "list", "--org", org]);
});

ipcMain.handle("deleteSuggestion", async (_evt, { file } = {}) => {
  if (!file) return { error: "deleteSuggestion: missing file" };
  return runManageSuggestions(["--mode", "delete", "--file", file]);
});

ipcMain.handle("updateSuggestion", async (_evt, { file, payload } = {}) => {
  if (!file || !payload) return { error: "updateSuggestion: missing file or payload" };
  return runManageSuggestions([
    "--mode", "update",
    "--file", file,
    "--payload", JSON.stringify(payload),
  ]);
});

// ─────────────────────────────────────────────────────────────────────
// App lifecycle
// ─────────────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  buildMenu();
  createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
