// Prebuilds the renderer bundle before any e2e test runs. The
// Electron host loads ``dist/index.html`` when ``JDAPINE_USE_DIST=1``
// (set per-test in the launch env), so the bundle must exist.

import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "..", "..");

export default async function globalSetup() {
  execSync("npm run build:renderer", { cwd: appRoot, stdio: "inherit" });
}
