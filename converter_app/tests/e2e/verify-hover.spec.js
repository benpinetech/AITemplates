import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixture.rtf");

// Three chips: two LLM (verifiable) + one already-saved (suggestion) which
// must NOT highlight.
const CONVERTED = "{\\rtf1\\ansi\\paperw12240\\paperh15840 The respondent @[Respondent.first.NameFull] and " +
  "@[Complainant.first.NameFull] before @[builtin.CaseID].\\par " +
  Array.from({ length: 20 }, (_, i) => `Filler ${i}.\\par`).join(" ") + "}";

test("verify hover highlights affected chips", async () => {
  test.setTimeout(60000);
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const app = await electron.launch({
    cwd: APP_ROOT, args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", OPENAI_API_KEY: "" },
  });
  await app.evaluate(async ({ dialog, ipcMain }, { fixturePath, converted }) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [fixturePath] });
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: "x", agency: "oba" }));
    ipcMain.removeHandler("listAgencies");
    ipcMain.handle("listAgencies", async () => [{ id: "oba", description: "OBA" }]);
    ipcMain.removeHandler("convertRtf");
    ipcMain.handle("convertRtf", async () => ({
      source: { rtf: "{\\rtf1\\ansi x %[A] %[B] %[C].\\par}" },
      converted: { rtf: converted },
      template_name: "fixture.rtf", audience: null, agency: "oba", prelude_pine_token_count: 0,
      totals: { jda_tokens: 3, pine_tokens: 3, suggestion_segments: 1, llm_segments: 2, unmatched_segments: 0, edit_segments: 0 },
      segments: [
        { index: 0, provenance: "llm", jda_tokens: ["%[A]"], pine_tokens: ["@[Respondent.first.NameFull]"] },
        { index: 1, provenance: "llm", jda_tokens: ["%[B]"], pine_tokens: ["@[Complainant.first.NameFull]"] },
        { index: 2, provenance: "suggestion", jda_tokens: ["%[C]"], pine_tokens: ["@[builtin.CaseID]"] },
      ],
    }));
  }, { fixturePath: FIXTURE, converted: CONVERTED });

  const w = await app.firstWindow();
  await w.waitForSelector(".app");
  await w.click('button[title^="Open"]');
  await w.waitForSelector(".pane .paper .prose p", { timeout: 15000 });
  await w.click("button.btn-primary");
  await w.waitForSelector(".pine-editor .token-pine", { timeout: 15000 });

  await expect(w.locator(".verify-btn")).toHaveText(/Verify 2 suggestions/);
  await w.locator(".verify-btn").hover();
  await w.waitForTimeout(200);
  const n = await w.locator(".pine-editor .token-pine.will-verify").count();
  console.log("HIGHLIGHTED " + n);
  expect(n).toBe(2);                       // the two LLM chips, not the saved one

  // Moving away clears the highlight.
  await w.locator(".pane-label-row span").first().hover();
  await w.waitForTimeout(150);
  expect(await w.locator(".pine-editor .token-pine.will-verify").count()).toBe(0);
  await app.close();
});
