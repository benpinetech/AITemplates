// E2E for the Saved Mappings dialog: open from the toolbar, add a mapping
// with multiple Pine tokens, edit it to add another, and delete it. Uses a
// per-test suggestions root so the real store is untouched.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");

test("saved mappings: add multi-pine, edit, delete from the toolbar", async () => {
  test.setTimeout(60000);
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const tmpSuggestions = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-sugg-"));
  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: {
      ...process.env,
      JDAPINE_USE_DIST: "1",
      JDA_SUGGESTIONS_DIR: tmpSuggestions,
      OPENAI_API_KEY: "",
    },
  });
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));
  await app.evaluate(async ({ ipcMain }) => {
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: "x", agency: "oba" }));
    ipcMain.removeHandler("listAgencies");
    ipcMain.handle("listAgencies", async () => [{ id: "oba", description: "OBA" }]);
  });

  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");

  // Open the dialog from the toolbar.
  await w.click('button[title="Saved mappings"]');
  await expect(w.locator(".modal h2")).toHaveText("Saved Mappings");

  // Add a mapping: one JDA token → two Pine tokens.
  await w.click("button.add-btn");
  await w.fill(".form .field .text-input.mono", "%[Cust_Name]"); // JDA field (first mono input)
  const pineInputs = () => w.locator(".pine-row input");
  await pineInputs().nth(0).fill("@[Complainant.first.NameFirst]");
  await w.click("button.add-row");
  await pineInputs().nth(1).fill("@[Complainant.first.NameLast]");
  await w.click('.form-actions button:has-text("Save mapping")');

  // The new row shows with two pine chips.
  const row = w.locator("tbody tr").filter({ hasText: "Cust_Name" });
  await expect(row).toBeVisible({ timeout: 8000 });
  await expect(row.locator(".chip.pine")).toHaveCount(2);

  // Edit: add a third pine token.
  await row.locator("button.act.edit").click();
  await expect(w.locator(".form-title")).toHaveText("Edit mapping");
  await expect(pineInputs()).toHaveCount(2);
  await w.click("button.add-row");
  await pineInputs().nth(2).fill("@[Complainant.first.Suffix]");
  await w.click('.form-actions button:has-text("Save changes")');

  const row2 = w.locator("tbody tr").filter({ hasText: "Cust_Name" });
  await expect(row2.locator(".chip.pine")).toHaveCount(3, { timeout: 8000 });

  // Delete.
  await row2.locator("button.act.delete").click();
  await expect(w.locator("tbody tr").filter({ hasText: "Cust_Name" })).toHaveCount(0, { timeout: 8000 });

  await app.close();
});
