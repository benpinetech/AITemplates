// The legacy→Pine fillpoint mapping must stay aligned while a converter edits.
//
// ``chipMap`` maps the k-th body (post-prelude) chip to its segment, and
// defaults to the identity. Any edit that changes the number of chips shifts
// every later chip's slot — if the map isn't re-aligned, each chip reports its
// neighbour's legacy source: the off-by-one drift.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");
const FIXTURE = path.join(__dirname, "fixture.rtf");

// One CreateVar prelude chip, then three body fillpoints — the shape
// prepend_prelude_to_rtf produces.
const CONVERTED = "{\\rtf1\\ansi\\paperw12240\\paperh15840 @[CreateVar(@Respondent, Person)] \\par \\par " +
  "One @[Alpha.Name] two @[Bravo.Name] three @[Charlie.Name] four.\\par " +
  Array.from({ length: 30 }, (_, i) => `Filler ${i}.\\par`).join(" ") + "}";

async function setup() {
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const app = await electron.launch({
    cwd: APP_ROOT, args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
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
      source: { rtf: "{\\rtf1\\ansi One %[A] two %[B] three %[C] four.\\par}" },
      converted: { rtf: converted },
      template_name: "fixture.rtf", audience: null, agency: "oba",
      prelude_pine_token_count: 1,
      totals: { jda_tokens: 3, pine_tokens: 4, suggestion_segments: 0, llm_segments: 3, unmatched_segments: 0, edit_segments: 0 },
      segments: [
        { index: 0, provenance: "llm", jda_tokens: ["%[A]"], pine_tokens: ["@[Alpha.Name]"] },
        { index: 1, provenance: "llm", jda_tokens: ["%[B]"], pine_tokens: ["@[Bravo.Name]"] },
        { index: 2, provenance: "llm", jda_tokens: ["%[C]"], pine_tokens: ["@[Charlie.Name]"] },
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

// Raw mouse moves throughout: locator.hover() aims at the bounding-box centre,
// which lands in empty space when a chip's inline box wraps, and its
// actionability check trips over the info card that hovering just opened.
async function moveToChip(w, chip, jitter = 0) {
  const pt = await chip.evaluate((el) => {
    const r = el.getClientRects()[0];            // the chip's first line box
    return { x: r.x + Math.min(6, r.width / 2), y: r.y + r.height / 2 };
  });
  await w.mouse.move(pt.x + jitter, pt.y);
}

// Hover a chip until its info card opens. Retries because the page repaginates
// in a rAF after any edit: a point measured before that lands somewhere else
// after it, and the hover never fires.
async function hoverChipForCard(w, chip) {
  let n = 0;
  await expect(async () => {
    await moveToChip(w, chip, n++ % 2);          // nudge: an identical move is a no-op
    await expect(w.locator(".info-card")).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15000 });
}

// Park the pointer on prose inside the editor: leaving the editor entirely
// never fires its hover handler, so the highlight would linger.
async function parkPointer(w) {
  const pt = await w.locator(".pine-editor p").filter({ hasText: /^Filler 0\.$/ })
    .evaluate((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x + 2, y: r.y + r.height / 2 };
    });
  await w.mouse.move(pt.x, pt.y);
  await expect(w.locator(".info-card")).toHaveCount(0);
}

// Hover each body Pine chip in turn and record the legacy chip that lights up
// as its source — the converter-visible link, driven by segmentForPinePart.
// Waits on the card rather than sleeping past the 400ms hover delay. Prelude
// CreateVars are skipped: they have no source, so they never open a card
// (asserted separately).
async function mappingByHover(w) {
  const out = {};
  const chips = await w.locator(".pine-editor .token-pine").all();
  for (const chip of chips) {
    const text = (await chip.textContent()).trim();
    if (text.includes("CreateVar")) continue;
    await hoverChipForCard(w, chip);
    out[text] = await w.evaluate(() =>
      [...document.querySelectorAll(".token-legacy.linked")].map((e) => e.textContent).join(","));
    await parkPointer(w);
  }
  return out;
}

// Select a whole chip and delete it — a converter dragging over a fillpoint
// and retyping. (A caret + Backspace can't: Chromium refuses to delete the
// atomic widget that way.)
async function deleteChipBySelection(w, name) {
  await w.evaluate((n) => {
    const ed = document.querySelector(".pine-editor");
    ed.focus();
    const chip = [...ed.querySelectorAll(".token-pine")].find((c) => c.textContent.includes(n));
    const r = document.createRange();
    r.setStartBefore(chip);
    r.setEndAfter(chip);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
  }, name);
  await w.keyboard.press("Delete");
  await w.waitForTimeout(500);   // past the 140ms sync debounce
}

test("deleting a fillpoint in the prose editor keeps the rest mapped", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    expect(await mappingByHover(w)).toEqual({
      "@[Alpha.Name]": "%[A]",
      "@[Bravo.Name]": "%[B]",
      "@[Charlie.Name]": "%[C]",
    });

    // The prelude CreateVar has no legacy source, so it opens no card at all.
    await moveToChip(w, w.locator(".pine-editor .token-pine").filter({ hasText: "CreateVar" }));
    await w.waitForTimeout(600);
    await expect(w.locator(".info-card")).toHaveCount(0);
    await parkPointer(w);

    await deleteChipBySelection(w, "Alpha");
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(3);

    // The survivors keep their own sources — no shift onto %[A] / %[B].
    expect(await mappingByHover(w)).toEqual({
      "@[Bravo.Name]": "%[B]",
      "@[Charlie.Name]": "%[C]",
    });
  } finally {
    await app.close();
  }
});

test("deleting a prelude CreateVar keeps the body mapped", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    // The prelude has no body slot, so dropping it must not consume one.
    await w.locator(".pine-editor .token-pine").filter({ hasText: "CreateVar" }).click();
    await w.waitForTimeout(150);
    await w.locator(".info-card .info-delete-btn").click();
    await w.waitForTimeout(300);
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(3);

    expect(await mappingByHover(w)).toEqual({
      "@[Alpha.Name]": "%[A]",
      "@[Bravo.Name]": "%[B]",
      "@[Charlie.Name]": "%[C]",
    });
  } finally {
    await app.close();
  }
});

test("undo after a delete restores the mapping", async () => {
  test.setTimeout(60000);
  const { app, w } = await setup();
  try {
    await deleteChipBySelection(w, "Bravo");
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(3);
    await w.keyboard.press("Control+z");
    await w.waitForTimeout(400);
    await expect(w.locator(".pine-editor .token-pine")).toHaveCount(4);

    expect(await mappingByHover(w)).toEqual({
      "@[Alpha.Name]": "%[A]",
      "@[Bravo.Name]": "%[B]",
      "@[Charlie.Name]": "%[C]",
    });
  } finally {
    await app.close();
  }
});
