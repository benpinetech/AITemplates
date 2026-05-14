// Playwright config for the Electron-based e2e tests.
//
// The e2e tests drive a real Electron window through Playwright's
// ``_electron`` API. They need the renderer to be prebuilt (Vite
// ``build:renderer``) and the project venv's Python on PATH — that's
// what the main process spawns to run the conversion pipeline. The
// ``globalSetup`` below does the renderer build automatically.

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  workers: 1,                // Electron windows don't parallelize cleanly
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure",
  },
  globalSetup: "./tests/e2e/global-setup.js",
});
