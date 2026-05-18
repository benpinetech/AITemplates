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
   * Write an RTF directly to a known path without showing a dialog.
   * @param {{ path: string, rtf: string }} args
   * @returns {Promise<{ path: string }>}
   */
  writeRtf: (args) => ipcRenderer.invoke("writeRtf", args),

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

  /**
   * Subscribe to "open-file" messages from File → Open… (or Ctrl/Cmd+O).
   * @param {() => void} cb
   */
  onOpenFile: (cb) => {
    ipcRenderer.on("open-file", cb);
  },

  /**
   * Subscribe to "save-file" messages from File → Save… (or Ctrl/Cmd+S).
   * @param {() => void} cb
   */
  onSaveFile: (cb) => {
    ipcRenderer.on("save-file", cb);
  },

  /**
   * Subscribe to "save-file-as" messages from File → Save As… (or Ctrl+Shift+S).
   * @param {() => void} cb
   */
  onSaveFileAs: (cb) => {
    ipcRenderer.on("save-file-as", cb);
  },

  /** Subscribe to Edit → Undo menu item. */
  onUndo: (cb) => { ipcRenderer.on("app-undo", cb); },

  /** Subscribe to Edit → Redo menu item. */
  onRedo: (cb) => { ipcRenderer.on("app-redo", cb); },

  /** Subscribe to Settings → Saved Mappings… menu item. */
  onOpenMappings: (cb) => { ipcRenderer.on("open-mappings", cb); },

  /**
   * Strip the old generated CreateVar prelude from the converted RTF and
   * regenerate it from the current live Pine token strings. Call this before
   * saving to ensure the output is valid even when the user has edited chips.
   * ``prelude_count`` must be the ``prelude_pine_token_count`` from the
   * original conversion result — when 0, no stripping is done (safe for
   * documents where @[CreateVar] tokens come from the source file).
   * @param {{ rtf: string, pine_tokens: string[], org?: string, prelude_count?: number }} args
   * @returns {Promise<{ ok: true, rtf: string } | { error: string }>}
   */
  refreshPrelude: (args) => ipcRenderer.invoke("refreshPrelude", args),

  /**
   * List all persisted suggestions for an org.
   * @param {{ org?: string }} args
   * @returns {Promise<Array<{ file, scope_kind, scope_value, org, jda_tokens, pine_tokens }> | { error }>}
   */
  listSuggestions: (args) => ipcRenderer.invoke("listSuggestions", args),

  /**
   * Delete a suggestion file by its absolute path.
   * @param {{ file: string }} args
   * @returns {Promise<{ ok: true } | { error: string }>}
   */
  deleteSuggestion: (args) => ipcRenderer.invoke("deleteSuggestion", args),

  /**
   * Replace a suggestion's Pine tokens. Deletes the old file and writes a new one.
   * @param {{ file: string, payload: { org, scope: {kind, value}, jda_tokens, pine_tokens } }} args
   * @returns {Promise<{ ok: true, path: string } | { error: string }>}
   */
  updateSuggestion: (args) => ipcRenderer.invoke("updateSuggestion", args),
});
