<#
.SYNOPSIS
    Build the Windows installer for the JDA -> Pine Converter.

.DESCRIPTION
    One command, run from the repo root on a Windows machine:

        powershell -ExecutionPolicy Bypass -File build-windows.ps1

    It performs the full chain:
      1. Creates a clean, minimal Python build venv (build-venv\).
      2. Installs the sidecar's runtime deps + PyInstaller.
      3. Freezes the LLM pipeline into a standalone onedir binary
         (dist\jda_pine_sidecar\jda_pine_sidecar.exe) via sidecar.spec.
      4. Copies that bundle into converter_app\binaries\.
      5. Installs npm deps and runs electron-builder for Windows (NSIS).

    Output: converter_app\release\JDA Pine Converter Setup <version>.exe

    The installer is UNSIGNED. On first launch Windows SmartScreen may
    show an "unknown publisher" prompt ("More info" -> "Run anyway").

.NOTES
    Requires: Python 3.11+ and Node.js 18+ on PATH.
    PyInstaller cannot cross-compile, which is why this must run on
    Windows to produce a Windows sidecar .exe.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Resolve-Python {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($candidate -eq "py") { return @("py", "-3") }
            return @("python")
        }
    }
    throw "Python 3.11+ not found on PATH. Install from https://www.python.org/ and re-run."
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js not found on PATH. Install from https://nodejs.org/ and re-run."
}

$py = @(Resolve-Python)
Write-Host "==> Using Python: $($py -join ' ')" -ForegroundColor Cyan

# ── 1-2. Clean build venv + minimal deps ─────────────────────────────
$venv = Join-Path $PSScriptRoot "build-venv"
if (Test-Path $venv) {
    Write-Host "==> Removing stale build-venv\" -ForegroundColor Cyan
    Remove-Item -Recurse -Force $venv
}
Write-Host "==> Creating build venv" -ForegroundColor Cyan
$pyExe  = $py[0]
$pyArgs = if ($py.Length -gt 1) { $py[1..($py.Length-1)] } else { @() }
& $pyExe @pyArgs -m venv $venv

$venvPython = Join-Path $venv "Scripts\python.exe"
Write-Host "==> Installing sidecar deps + PyInstaller" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-sidecar.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ── 3. Freeze the sidecar ─────────────────────────────────────────────
Write-Host "==> Building sidecar binary with PyInstaller" -ForegroundColor Cyan
if (Test-Path "dist\jda_pine_sidecar") { Remove-Item -Recurse -Force "dist\jda_pine_sidecar" }
& $venvPython -m PyInstaller sidecar.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$sidecarExe = "dist\jda_pine_sidecar\jda_pine_sidecar.exe"
if (-not (Test-Path $sidecarExe)) { throw "Expected $sidecarExe was not produced." }

# Quick smoke test: the binary must at least start and print its usage.
Write-Host "==> Smoke-testing sidecar binary" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
& $sidecarExe 2>&1 | Out-Null   # no args -> prints usage, exits 1; just proves it runs
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -gt 1) { throw "Sidecar smoke test failed with exit code $LASTEXITCODE." }

# ── 4. Stage the bundle for electron-builder ──────────────────────────
$binaries = "converter_app\binaries"
if (Test-Path $binaries) { Remove-Item -Recurse -Force $binaries }
New-Item -ItemType Directory -Path $binaries | Out-Null
Write-Host "==> Copying sidecar bundle into $binaries" -ForegroundColor Cyan
Copy-Item -Recurse "dist\jda_pine_sidecar" (Join-Path $binaries "jda_pine_sidecar")

# ── 5. Build the Electron installer ───────────────────────────────────
Push-Location "converter_app"
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "==> Installing npm deps (npm ci)" -ForegroundColor Cyan
        npm ci
        if ($LASTEXITCODE -ne 0) { npm install; if ($LASTEXITCODE -ne 0) { throw "npm install failed" } }
    }
    Write-Host "==> Building Windows installer (electron-builder)" -ForegroundColor Cyan
    # Pre-populate winCodeSign cache to avoid symlink-creation privilege failure.
    # electron-builder's 7-zip uses -snl which tries to create real Windows symlinks
    # for macOS dylib symlinks in the archive — fails without admin/Developer Mode.
    # We pre-extract WITHOUT -snl so those entries are skipped harmlessly.
    $winCodeSignVer = "2.6.0"
    $winCodeSignFinal = Join-Path $env:LOCALAPPDATA "electron-builder\Cache\winCodeSign\winCodeSign-$winCodeSignVer"
    if (-not (Test-Path $winCodeSignFinal)) {
        Write-Host "==> Pre-caching winCodeSign $winCodeSignVer (no-symlink extraction)" -ForegroundColor Cyan
        $sevenZip = Join-Path $PSScriptRoot "converter_app\node_modules\7zip-bin\win\x64\7za.exe"
        $archiveUrl = "https://github.com/electron-userland/electron-builder-binaries/releases/download/winCodeSign-$winCodeSignVer/winCodeSign-$winCodeSignVer.7z"
        $archiveTmp = Join-Path $env:TEMP "winCodeSign-$winCodeSignVer.7z"
        Invoke-WebRequest -Uri $archiveUrl -OutFile $archiveTmp
        $extractTmp = Join-Path $env:TEMP "winCodeSign-extract-$winCodeSignVer"
        if (Test-Path $extractTmp) { Remove-Item -Recurse -Force $extractTmp }
        # x = extract, -y = yes to all, -bd = no progress, NO -snl so symlinks become regular files
        $ErrorActionPreference = "Continue"
        & $sevenZip x -y -bd $archiveTmp "-o$extractTmp" | Out-Null
        $ErrorActionPreference = "Stop"
        New-Item -ItemType Directory -Force -Path (Split-Path $winCodeSignFinal) | Out-Null
        Move-Item $extractTmp $winCodeSignFinal
        Remove-Item $archiveTmp -Force
    }
    # Clear any partial/failed cache entries from previous attempts
    Get-ChildItem (Join-Path $env:LOCALAPPDATA "electron-builder\Cache\winCodeSign") -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "winCodeSign-$winCodeSignVer" } |
        Remove-Item -Recurse -Force
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    $env:WIN_CSC_LINK = ""
    npm run build:win
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> DONE. Installer written to:" -ForegroundColor Green
Get-ChildItem "converter_app\release\*.exe" | ForEach-Object { Write-Host "    $($_.FullName)" -ForegroundColor Green }
