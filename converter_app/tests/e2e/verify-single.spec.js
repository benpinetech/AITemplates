// The fillpoint card has a "Verify" button that saves just that one mapping.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixture.rtf");

const CONVERTED = "{\\rtf1\\ansi\\paperw12240\\paperh15840 The respondent @[Respondent.first.NameFull] and " +
  "@[Complainant.first.NameFull] appear.\\par " +
  Array.from({ length: 20 }, (_, i) => `Filler ${i}.\\par`).join(" ") + "}";

test("card Verify saves a single mapping", async () => {
  test.setTimeout(60000);
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const tmpSugg = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-sugg-"));
  const app = await electron.launch({
    cwd: APP_ROOT, args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", JDA_SUGGESTIONS_DIR: tmpSugg, OPENAI_API_KEY: "" },
  });
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));
  await app.evaluate(async ({ dialog, ipcMain }, { fixturePath, converted }) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [fixturePath] });
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: "x", agency: "oba" }));
    ipcMain.removeHandler("listAgencies");
    ipcMain.handle("listAgencies", async () => [{ id: "oba", description: "OBA" }]);
    ipcMain.removeHandler("convertRtf");
    ipcMain.handle("convertRtf", async () => ({
      source: { rtf: "{\\rtf1\\ansi x %[A] %[B].\\par}" },
      converted: { rtf: converted },
      template_name: "fixture.rtf", audience: null, agency: "oba", prelude_pine_token_count: 0,
      totals: { jda_tokens: 2, pine_tokens: 2, suggestion_segments: 0, llm_segments: 2, unmatched_segments: 0, edit_segments: 0 },
      segments: [
        { index: 0, provenance: "llm", jda_tokens: ["%[A]"], pine_tokens: ["@[Respondent.first.NameFull]"] },
        { index: 1, provenance: "llm", jda_tokens: ["%[B]"], pine_tokens: ["@[Complainant.first.NameFull]"] },
      ],
    }));
  }, { fixturePath: FIXTURE, converted: CONVERTED });

  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");
  await w.click('button[title^="Open"]');
  await w.waitForSelector(".pane .paper .prose p", { timeout: 15000 });
  await w.click("button.btn-primary");
  await w.waitForSelector(".pine-editor .token-pine", { timeout: 15000 });

  await expect(w.locator(".verify-btn")).toHaveText(/Verify 2 suggestions/);

  // Click the first chip → its card shows a Verify button.
  await w.locator(".pine-editor .token-pine").first().click();
  await w.waitForTimeout(150);
  await expect(w.locator(".info-verify-btn")).toBeVisible();
  await w.locator(".info-verify-btn").click();

  // The bulk count drops to 1 (one mapping verified), and a TOML lands on disk.
  await expect(w.locator(".verify-btn")).toHaveText(/Verify 1 suggestion/, { timeout: 8000 });
  await w.waitForTimeout(300);
  const tomls = fs.existsSync(path.join(tmpSugg, "verified", "oba"))
    ? fs.readdirSync(path.join(tmpSugg, "verified", "oba"), { recursive: true }).filter((f) => String(f).endsWith(".toml"))
    : [];
  console.log("PERSISTED " + tomls.length);
  expect(tomls.length).toBe(1);
  await app.close();
});
