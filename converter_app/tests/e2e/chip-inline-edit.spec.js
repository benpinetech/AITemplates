// In-place fillpoint editing: clicking a Pine chip edits it inline (no popup),
// the caret lands where you click (not forced to the end), the info card shows
// below the line, deleting all text leaves @[], clicking out commits +
// dismisses, and the card's Delete removes the chip.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixture.rtf");

const INNER = "Foo.Bar";   // chip middle text (single line)
const CONVERTED = `{\\rtf1\\ansi\\paperw12240\\paperh15840 Hello @[${INNER}] there friend.\\par ` +
  Array.from({ length: 50 }, (_, i) => `Filler paragraph ${i} here.\\par`).join(" ") + "}";

async function setup() {
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: { ...process.env, JDAPINE_USE_DIST: "1", OPENAI_API_KEY: "" },
  });
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));
  await app.evaluate(async ({ dialog, ipcMain }, { fixturePath, converted, inner }) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [fixturePath] });
    ipcMain.removeHandler("getSettings");
    ipcMain.handle("getSettings", async () => ({ model: "gpt-5.5", api_key: "x", agency: "oba" }));
    ipcMain.removeHandler("listAgencies");
    ipcMain.handle("listAgencies", async () => [{ id: "oba", description: "OBA" }]);
    ipcMain.removeHandler("convertRtf");
    ipcMain.handle("convertRtf", async () => ({
      source: { rtf: "{\\rtf1\\ansi Hello %[X] there.\\par}" },
      converted: { rtf: converted },
      template_name: "fixture.rtf", audience: null, agency: "oba",
      prelude_pine_token_count: 0,
      totals: { jda_tokens: 1, pine_tokens: 1, suggestion_segments: 0, llm_segments: 1, unmatched_segments: 0, edit_segments: 0 },
      segments: [{ index: 0, provenance: "llm", jda_tokens: ["%[X]"], pine_tokens: [`@[${inner}]`], pattern_id: null }],
    }));
    ipcMain.removeHandler("persistEdit");
    ipcMain.handle("persistEdit", async () => ({ ok: true, path: "/tmp/x.toml", removed: [] }));
  }, { fixturePath: FIXTURE, converted: CONVERTED, inner: INNER });

  const w = await app.firstWindow();
  w.on("pageerror", (e) => process.stderr.write(`[RENDERER ERROR] ${e.message}\n`));
  await w.waitForSelector(".app");
  await w.click('button[title^="Open"]');
  await w.waitForSelector(".pane .paper .prose p", { timeout: 15000 });
  await w.click("button.btn-primary");
  await w.waitForSelector(".pine-editor .token-pine", { timeout: 15000 });
  return { app, w };
}

// Put the caret at the end of the chip's editable middle (deterministic — the
// "End" key escapes to the end of the visual line, out of the inner span).
function caretToInnerEnd(w) {
  return w.evaluate(() => {
    const inner = document.querySelector(".pine-editor .token-pine .chip-inner");
    inner.focus();
    const r = document.createRange();
    r.selectNodeContents(inner);
    r.collapse(false);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
  });
}

// Read the caret offset within the chip's editable middle (null if not there).
function caretInfo(w) {
  return w.evaluate(() => {
    const sel = window.getSelection();
    const inner = document.querySelector(".pine-editor .token-pine .chip-inner");
    const innerLen = inner ? inner.textContent.length : 0;
    let offset = null;
    if (inner && sel.rangeCount && inner.contains(sel.anchorNode)) offset = sel.anchorOffset;
    return { offset, innerLen };
  });
}

test("click edits inline (no popup), caret follows the click, card below", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    const chip = w.locator(".pine-editor .token-pine").first();
    const box = await chip.boundingBox();
    await chip.click();
    await w.waitForTimeout(150);

    const state = await w.evaluate(() => {
      const ae = document.activeElement;
      const card = document.querySelector(".info-card");
      return {
        hasPopup: !!document.querySelector(".chip-editor"),
        activeIsInner: !!(ae && ae.classList.contains("chip-inner")),
        cardTop: card ? Math.round(card.getBoundingClientRect().top) : null,
      };
    });
    expect(state.hasPopup).toBe(false);
    expect(state.activeIsInner).toBe(true);
    expect(state.cardTop).toBeGreaterThan(Math.round(box.y + box.height));
    expect(state.cardTop).toBeLessThan(Math.round(box.y + box.height) + 120);

    // Caret landed where clicked (mid-token), NOT bumped to the end.
    const { offset, innerLen } = await caretInfo(w);
    expect(offset).not.toBeNull();
    expect(offset).toBeGreaterThan(0);
    expect(offset).toBeLessThan(innerLen);
  } finally {
    await app.close();
  }
});

test("edit + commit on click-out", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    await w.locator(".pine-editor .token-pine").first().click();
    await w.waitForTimeout(120);
    await caretToInnerEnd(w);
    await w.keyboard.type("X");
    await w.locator(".pine-editor p").filter({ hasText: /^Filler paragraph 7 here\.$/ }).click();
    await w.waitForTimeout(200);
    await expect(w.locator(".pine-editor .token-pine").first()).toHaveText(`@[${INNER}X]`);
    await expect(w.locator(".info-card")).toHaveCount(0);
  } finally {
    await app.close();
  }
});

test("deleting all chip text leaves @[]", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    await w.locator(".pine-editor .token-pine").first().click();
    await w.waitForTimeout(120);
    await caretToInnerEnd(w);
    for (let i = 0; i < INNER.length; i++) await w.keyboard.press("Backspace");
    await expect(w.locator(".pine-editor .token-pine").first()).toHaveText("@[]");
  } finally {
    await app.close();
  }
});

test("card Delete removes the fillpoint", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(1);
    await w.locator(".pine-editor .token-pine").first().click();
    await w.waitForTimeout(120);
    await w.locator(".info-card .info-delete-btn").click();
    await w.waitForTimeout(200);
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(0);
  } finally {
    await app.close();
  }
});
