// Preload script — the security boundary between the renderer
// (untrusted web context) and Electron's main process (full Node
// access). The renderer can only call methods exposed here through
// ``window.api`` — `contextIsolation: true` in main.cjs keeps the
// Node require() out of the renderer entirely.
//
// Keep this surface small and explicit. Every method is a single
// JSON-in / JSON-out IPC call.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  /**
   * Show the system Open dialog filtered to RTF. Returns the picked
   * file's absolute path + its raw bytes (UTF-8 decoded best-effort),
   * or null if the user cancelled.
   * @returns {Promise<{path: string, rtf: string} | null>}
   */
  openRtf: () => ipcRenderer.invoke("openRtf"),

  /**
   * Run the v2 conversion pipeline on the given RTF path and return
   * the structured JSON bundle (see Agent/v2/tools/convert.py
   * --json schema: jda-pine-convert/v1).
   * @param {{ path: string, org?: string }} args
   * @returns {Promise<object>}
   */
  convertRtf: (args) => ipcRenderer.invoke("convertRtf", args),

  /**
   * Show the system Save dialog and write the Pine RTF to disk.
   * @param {{ defaultPath?: string, rtf: string }} args
   * @returns {Promise<{ path: string } | null>}  null if user cancelled
   */
  saveRtf: (args) => ipcRenderer.invoke("saveRtf", args),

  /**
   * Auto-save a recovery snapshot for the given source RTF path. The
   * payload is round-tripped JSON, so callers must pre-strip any
   * Svelte $state proxies (JSON.parse(JSON.stringify(...)) is fine).
   * @param {{ sourcePath: string, payload: object }} args
   * @returns {Promise<{ ok: true } | { error: string }>}
   */
  saveRecovery: (args) => ipcRenderer.invoke("saveRecovery", args),

  /**
   * Read the recovery snapshot for the given source RTF path, or null
   * if none exists.
   * @param {{ sourcePath: string }} args
   * @returns {Promise<{ sourcePath: string, savedAt: number, payload: object } | null>}
   */
  loadRecovery: (args) => ipcRenderer.invoke("loadRecovery", args),

  /**
   * Delete the recovery snapshot for the given source RTF path. No-op
   * if none exists.
   * @param {{ sourcePath: string }} args
   * @returns {Promise<{ ok: true }>}
   */
  clearRecovery: (args) => ipcRenderer.invoke("clearRecovery", args),

  /**
   * Auto-persist an inline edit as a scoped suggestion. Fire-and-forget
   * semantics: failures are returned, not thrown, so the UI can decide
   * whether to surface them.
   * @param {{
   *   org: string,
   *   scope: { kind: "template"|"audience"|"global", value: string },
   *   jda_tokens: string[],
   *   pine_tokens: string[],
   *   source_template?: string,
   *   segment_index?: number,
   *   note?: string,
   * }} args
   * @returns {Promise<{ ok: true, path: string, removed: string[] } | { error: string }>}
   */
  persistEdit: (args) => ipcRenderer.invoke("persistEdit", args),

  /**
   * Read the user's persisted OpenAI settings (API key, model). Missing
   * values are returned as empty strings.
   * @returns {Promise<{ api_key: string, model: string }>}
   */
  getSettings: () => ipcRenderer.invoke("getSettings"),

  /**
   * Merge the given fields into the persisted settings file. Unspecified
   * fields are left at their existing values.
   * @param {{ api_key?: string, model?: string }} args
   * @returns {Promise<{ ok: true }>}
   */
  setSettings: (args) => ipcRenderer.invoke("setSettings", args),

  /**
   * Subscribe to "open-settings" messages from the application menu.
   * The callback fires whenever the user picks the Settings/Preferences
   * menu item (or its accelerator).
   * @param {() => void} cb
   */
  onOpenSettings: (cb) => {
    ipcRenderer.on("open-settings", cb);
  },

  /**
   * Subscribe to "open-help" messages from the Help menu (or its F1
   * accelerator).
   * @param {() => void} cb
   */
  onOpenHelp: (cb) => {
    ipcRenderer.on("open-help", cb);
  },
});
