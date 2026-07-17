// Typing prose next to a fillpoint after the caret has walked out of one.
// Clicking a chip opens an in-place edit; the arrow keys can then carry the
// caret out into the prose. Text typed there is prose, not chip text — it has
// to reach the RTF (the source of truth) and survive the next rebuild.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixture.rtf");

// Two adjacent fillpoints — there's no text position between them to click, so
// the arrow keys are the only way in.
const CONVERTED = `{\\rtf1\\ansi\\paperw12240\\paperh15840 Hello @[A.One]@[B.Two] there friend.\\par ` +
  Array.from({ length: 50 }, (_, i) => `Filler paragraph ${i} here.\\par`).join(" ") + "}";

async function setup() {
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", OPENAI_API_KEY: "" },
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
      source: { rtf: "{\\rtf1\\ansi Hello %[X] %[Y] there.\\par}" },
      converted: { rtf: converted },
      template_name: "fixture.rtf", audience: null, agency: "oba",
      prelude_pine_token_count: 0,
      totals: { jda_tokens: 2, pine_tokens: 2, suggestion_segments: 0, llm_segments: 2, unmatched_segments: 0, edit_segments: 0 },
      segments: [
        { index: 0, provenance: "llm", jda_tokens: ["%[X]"], pine_tokens: ["@[A.One]"], pattern_id: null },
        { index: 1, provenance: "llm", jda_tokens: ["%[Y]"], pine_tokens: ["@[B.Two]"], pattern_id: null },
      ],
    }));
    ipcMain.removeHandler("persistEdit");
    ipcMain.handle("persistEdit", async () => ({ ok: true, path: "/tmp/x.toml", removed: [] }));
  }, { fixturePath: FIXTURE, converted: CONVERTED });

  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");
  await w.click('button[title^="Open"]');
  await w.waitForSelector(".pane .paper .prose p", { timeout: 15000 });
  await w.click("button.btn-primary");
  await w.waitForSelector(".pine-editor .token-pine", { timeout: 15000 });
  return { app, w };
}

const pineText = (w) => w.evaluate(() => document.querySelector(".pine-editor").textContent);

test("prose typed after arrowing out of a chip reaches the RTF", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    // Open the first chip, then walk the caret out past its closing ].
    await w.locator(".pine-editor .token-pine").nth(0).click();
    await w.waitForTimeout(150);
    for (let i = 0; i < 8; i++) await w.keyboard.press("ArrowRight");

    await w.keyboard.type("HELLO");
    await w.waitForTimeout(400);
    expect(await pineText(w)).toContain("HELLO");

    // The chips themselves are untouched — what was typed is prose.
    await expect(w.locator(".pine-editor .token-pine").nth(0)).toHaveText("@[A.One]");
    await expect(w.locator(".pine-editor .token-pine").nth(1)).toHaveText("@[B.Two]");

    // It must survive a rebuild from the RTF: undo/redo forces one, so a
    // DOM-only edit would vanish here.
    await w.keyboard.press("Control+z");
    await w.waitForTimeout(200);
    expect(await pineText(w)).not.toContain("HELLO");
    await w.keyboard.press("Control+y");
    await w.waitForTimeout(200);
    expect(await pineText(w)).toContain("HELLO");
  } finally {
    await app.close();
  }
});

test("clicking from a chip into prose keeps the caret there", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    await w.locator(".pine-editor .token-pine").nth(0).click();
    await w.waitForTimeout(150);
    // Clicking out ends the (untouched) chip edit; the click's caret must
    // survive, so what's typed next lands where it was clicked (mid-paragraph,
    // since the click lands on the paragraph's centre).
    await w.locator(".pine-editor p").filter({ hasText: /^Filler paragraph 7 here\.$/ }).click();
    await w.waitForTimeout(200);
    await w.keyboard.type("ZZ");
    await w.waitForTimeout(400);
    // Which column it lands in follows the click's pixel position — all that
    // matters is that it stayed in the paragraph that was clicked.
    const para = await w.evaluate(() =>
      [...document.querySelectorAll(".pine-editor p")].find((p) => p.textContent.includes("ZZ"))?.textContent);
    expect(para).toMatch(/^Filler paragraph.*7.*here\.$/);
    await expect(w.locator(".info-card")).toHaveCount(0);
  } finally {
    await app.close();
  }
});
