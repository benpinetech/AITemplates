// E2E: convert a real template, then bulk-"Verify all" its LLM suggestions
// and confirm they persist to the (temp) suggestion store.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const REPO_ROOT = path.resolve(APP_ROOT, "..");
const FIXTURE = path.join(REPO_ROOT, "ground_truth/evaluation_templates/jda_to_pine/legacy/3A.rtf");
const ENV = fs.readFileSync(path.join(REPO_ROOT, ".env"), "utf-8");
const API_KEY = (ENV.match(/OPENAI_API_KEY=(.+)/) || [])[1]?.trim() || "";

test("verify all: persists LLM suggestions in one action", async () => {
  test.setTimeout(300000);
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const tmpSugg = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-sugg-"));
  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", JDA_SUGGESTIONS_DIR: tmpSugg, OPENAI_API_KEY: API_KEY },
  });
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));
  await app.evaluate(async ({ dialog, ipcMain }, { fixturePath, apiKey }) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [fixturePath] });
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: apiKey, agency: "oba" }));
    ipcMain.removeHandler("listAgencies");
    ipcMain.handle("listAgencies", async () => [{ id: "oba", description: "OBA" }]);
  }, { fixturePath: FIXTURE, apiKey: API_KEY });

  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");
  await w.click('button[title^="Open"]');
  await w.waitForSelector(".pane .paper .prose p", { timeout: 15000 });
  await w.click("button.btn-primary"); // Convert
  await w.waitForSelector(".pine-editor p", { timeout: 240000 });

  // The "Verify N suggestions" button appears with a count.
  const verifyBtn = w.locator(".verify-btn");
  await expect(verifyBtn).toBeVisible({ timeout: 10000 });
  const label = await verifyBtn.textContent();
  console.log("VERIFY_BTN " + JSON.stringify(label));
  expect(label).toMatch(/Verify \d+ suggestion/);

  // Open the confirm popup and accept.
  await verifyBtn.click();
  const modal = w.locator(".add-modal").filter({ hasText: "Verify suggestions?" });
  await expect(modal).toBeVisible();
  await modal.locator('button:has-text("Verify")').click();

  // After verifying, the button is gone (segments now marked saved) and a
  // confirmation note shows.
  await expect(verifyBtn).toHaveCount(0, { timeout: 15000 });
  await expect(w.locator(".learned-bar")).toContainText(/Verified \d+ suggestion/);

  // The mappings landed on disk under the temp store.
  const byTemplate = path.join(tmpSugg, "verified", "oba", "by_template", "3A.rtf");
  const tomls = fs.existsSync(byTemplate)
    ? fs.readdirSync(byTemplate).filter((f) => f.endsWith(".toml"))
    : [];
  console.log("PERSISTED " + tomls.length);
  expect(tomls.length).toBeGreaterThan(0);

  await app.close();
});
