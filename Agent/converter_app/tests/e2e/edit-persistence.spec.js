// End-to-end test: a Pine-chip edit made in the GUI must
//   1. update the chip's rendered text immediately,
//   2. land on disk as a scoped suggestion TOML, and
//   3. be picked up the next time the same file is converted, so the
//      new Pine output reflects the user's edit (not the original
//      pattern output).
//
// The test drives a real Electron window through Playwright's
// ``_electron`` driver. ``dialog.showOpenDialog`` and
// ``dialog.showSaveDialog`` are monkey-patched in the main process so
// the test can supply a fixture path / a temp save target without a
// human poking the OS file picker. ``JDAPINE_SUGGESTIONS_ROOT`` and
// ``--user-data-dir`` redirect every disk side-effect into per-test
// temp directories, so nothing leaks into the real Agent/v2/suggestions
// store or the user's Electron profile.

import { _electron as electron, expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, "..", "..");          // converter_app/
const REPO_ROOT = path.resolve(APP_ROOT, "..", "..");           // AITemplates/
const FIXTURE_RTF = path.join(__dirname, "fixture.rtf");

test("inline edit persists and round-trips through reconvert", async () => {
  // Per-test temp dirs so we never touch the real suggestion store or
  // the user's Electron profile.
  const tmpUserData = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-userdata-"));
  const tmpSuggestions = fs.mkdtempSync(path.join(os.tmpdir(), "jdapine-sugg-"));

  const app = await electron.launch({
    cwd: APP_ROOT,
    args: [APP_ROOT, `--user-data-dir=${tmpUserData}`],
    env: {
      ...process.env,
      // Force the production dist/ bundle so the test doesn't need
      // a Vite dev server running.
      JDAPINE_USE_DIST: "1",
      // Redirect the suggestion store into the per-test temp dir.
      JDAPINE_SUGGESTIONS_ROOT: tmpSuggestions,
      // Skip the LLM call in convert.py — the fixture's only token is
      // handled by library/common/current_date.toml deterministically.
      OPENAI_API_KEY: "",
    },
  });

  // Forward main-process stderr only when something goes wrong — keeps
  // a clean test report on success but surfaces the python persist CLI
  // error if the suggestion-write step ever fails again.
  app.process().stderr.on("data", (b) => process.stderr.write(`[main] ${b}`));

  // Replace the Open/Save dialogs with stubs that return the fixture
  // path / a temp output path. ``app.evaluate`` runs in the Electron
  // main process and patches the dialog module *before* the renderer
  // makes its first ``window.api.openRtf`` call.
  const tmpSaveOut = path.join(tmpUserData, "out.pine.rtf");
  await app.evaluate(
    async ({ dialog }, { fixturePath, savePath }) => {
      dialog.showOpenDialog = async () => ({
        canceled: false,
        filePaths: [fixturePath],
      });
      dialog.showSaveDialog = async () => ({
        canceled: false,
        filePath: savePath,
      });
    },
    { fixturePath: FIXTURE_RTF, savePath: tmpSaveOut },
  );

  const window = await app.firstWindow();
  // Surface renderer errors (e.g., IPC structured-clone refusals on
  // Svelte $state proxies) in the test log instead of silently
  // letting them turn into downstream "no TOML on disk" assertions.
  window.on("pageerror", (e) => process.stderr.write(`[renderer error] ${e.message}\n`));
  await window.waitForSelector(".app");

  // ── 1. Open the fixture ────────────────────────────────────────
  await window.click("button:has-text('Open')");
  await expect(window.locator(".file-name")).toContainText("fixture.rtf");

  // ── 2. Convert ─────────────────────────────────────────────────
  await window.click("button:has-text('Convert')");
  // The common-library current_date pattern produces a single Pine chip.
  await window.waitForSelector(".token-pine");

  const originalText = await window.locator(".token-pine").first().innerText();
  expect(originalText).toContain("FormatDate");

  // ── 3. Click chip → contenteditable opens. The chip splits into
  //       static @[ / ] brackets plus an editable inner span. ──────
  await window.locator(".token-pine").first().click();
  const inner = window.locator(".chip-inner");
  await inner.waitFor({ state: "visible" });

  // Replace the inner text with our edit. The original contents are
  // selected on focus via the autofocus action — but to be sure we
  // override exactly, select-all first.
  await window.keyboard.press("Control+a");
  const EDITED_INNER = "builtin.today.FormatDate(preset5)";
  await window.keyboard.type(EDITED_INNER);
  await window.keyboard.press("Enter");

  // ── 4. Chip should now render the edited text and the "saved" tint.
  const editedChip = window.locator(".token-pine").first();
  await expect(editedChip).toContainText("preset5");

  // ── 5. The persistEdit IPC writes a TOML under the temp root.
  //       Wait for the file to land (the IPC spawns a python child). ─
  const byTemplateDir = path.join(
    tmpSuggestions, "verified", "oba", "by_template",
  );
  await expect.poll(
    () => (fs.existsSync(byTemplateDir)
      ? fs.readdirSync(byTemplateDir, { recursive: true }).filter(
          (f) => typeof f === "string" && f.endsWith(".toml"),
        ).length
      : 0),
    { message: "expected a verified-suggestion TOML under by_template/", timeout: 10_000 },
  ).toBeGreaterThan(0);

  // Sanity: the TOML's rewrite line should contain our edit.
  const tomls = fs
    .readdirSync(byTemplateDir, { recursive: true })
    .map((f) => path.join(byTemplateDir, f))
    .filter((p) => p.endsWith(".toml"));
  const tomlText = fs.readFileSync(tomls[0], "utf-8");
  expect(tomlText).toContain(EDITED_INNER);

  // ── 6. Re-convert the same file — the persisted suggestion should
  //       beat the original pattern (priority 500 vs 100). ─────────
  await window.click("button:has-text('Convert')");
  await window.waitForSelector(".token-pine");
  await expect(window.locator(".token-pine").first()).toContainText("preset5");

  await app.close();
});
