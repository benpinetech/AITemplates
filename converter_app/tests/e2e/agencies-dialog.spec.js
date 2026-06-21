// E2E for the Manage Agencies dialog: open from the picker, then add, rename,
// and delete an agency end-to-end (real Python backend). A second test injects
// a failing IPC to confirm the dialog surfaces the error instead of wedging.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const REPO_ROOT = path.resolve(APP_ROOT, "..");
const OVERRIDES = path.join(REPO_ROOT, "pipeline/grammar/agency_overrides");

// Remove any agency files this test could have created (defensive — the happy
// path deletes them itself, but a mid-test failure shouldn't leave repo cruft).
function cleanup() {
  for (const f of fs.readdirSync(OVERRIDES)) {
    if (f.startsWith("zz-e2e")) fs.rmSync(path.join(OVERRIDES, f), { force: true });
  }
}

async function launch({ inject } = {}) {
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const tmpSuggestions = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-sugg-"));
  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", JDA_SUGGESTIONS_DIR: tmpSuggestions, OPENAI_API_KEY: "" },
  });
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));
  await app.evaluate(async ({ ipcMain }, injectName) => {
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: "x", agency: "oba" }));
    if (injectName) {
      ipcMain.removeHandler(injectName);
      ipcMain.handle(injectName, async () => { throw new Error("simulated backend failure"); });
    }
  }, inject);
  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");
  // Open the dialog via the picker's "Manage agencies…" sentinel.
  await w.selectOption("select.agency-select", "__manage__");
  await expect(w.locator(".modal h2")).toHaveText("Manage Agencies");
  return { app, w };
}

test("agencies: add, rename, delete from the dialog", async () => {
  test.setTimeout(60000);
  cleanup();
  const { app, w } = await launch();
  try {
    // oba is listed and marked active (getSettings stub selects it).
    await expect(w.locator(".agency").filter({ hasText: "oba" }).first()).toBeVisible();

    // Add.
    await w.fill(".add-row .text-input", "ZZ E2E Agency");
    await w.click('.add-row button:has-text("Add")');
    const row = w.locator(".agency").filter({ hasText: "ZZ E2E Agency" });
    await expect(row).toBeVisible({ timeout: 8000 });
    await expect(row.locator(".slug")).toHaveText("zz-e2e-agency");

    // Rename.
    await row.locator('button:has-text("Rename")').click();
    await w.fill(".agency .rename-input", "ZZ E2E Renamed");
    await w.click('.agency .act.save');
    const renamed = w.locator(".agency").filter({ hasText: "ZZ E2E Renamed" });
    await expect(renamed).toBeVisible({ timeout: 8000 });
    await expect(renamed.locator(".slug")).toHaveText("zz-e2e-renamed");
    await expect(w.locator(".agency").filter({ hasText: "ZZ E2E Agency" })).toHaveCount(0);

    // Delete (with confirmation popup).
    await renamed.locator("button.act.delete").click();
    await expect(w.locator(".confirm-modal")).toBeVisible();
    await w.click(".confirm-modal .btn-danger");
    await expect(w.locator(".agency").filter({ hasText: "ZZ E2E Renamed" })).toHaveCount(0, { timeout: 8000 });
  } finally {
    await app.close();
    cleanup();
  }
});

test("agencies: a failed delete surfaces an error (not a wedge)", async () => {
  test.setTimeout(60000);
  const { app, w } = await launch({ inject: "deleteAgency" });
  try {
    const oba = w.locator(".agency").filter({ hasText: "oba" }).first();
    await oba.locator("button.act.delete").click();
    await expect(w.locator(".confirm-modal")).toBeVisible();
    await w.click(".confirm-modal .btn-danger");
    // The popup stays open and shows the error; nothing is deleted.
    await expect(w.locator(".confirm-modal .inline-error")).toBeVisible({ timeout: 5000 });
    await w.click(".confirm-modal .act.ghost"); // Cancel
    await expect(w.locator(".agency").filter({ hasText: "oba" }).first()).toBeVisible();
  } finally {
    await app.close();
  }
});
