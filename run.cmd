@echo off
setlocal EnableDelayedExpansion

REM Launch the JDA -> Pine converter on Windows.
REM On first run: creates the venv, installs Python deps, installs npm deps.
REM On subsequent runs: just starts the Electron app.

REM Ensure essential Windows dirs are on PATH (guards against broken machine PATH registry).
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Program Files\nodejs;%PATH%"

cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js not found. Install from https://nodejs.org/ and re-run.
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 from https://www.python.org/ and re-run.
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  python -m venv venv || exit /b 1
  echo Installing Python dependencies...
  venv\Scripts\python -m pip install -r requirements.txt || exit /b 1
)

if not exist "converter_app\node_modules" (
  echo Installing npm dependencies...
  pushd converter_app
  set "ELECTRON_SKIP_BINARY_DOWNLOAD=1" && call npm install || ( popd ^& exit /b 1 )
  popd
)

REM Electron self-heal: npm postinstall (extract-zip) is unreliable on Windows
REM (antivirus mid-write, path-length limits, Node 24+ incompatibility).
REM Fix: bypass postinstall entirely and use PowerShell Expand-Archive, which
REM is built into every Windows 10+ machine and always works.
if not exist "converter_app\node_modules\electron\dist\electron.exe" (
  echo Electron binary missing - downloading directly from GitHub releases...
  pushd converter_app
  %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pkg = Get-Content 'node_modules\electron\package.json' | ConvertFrom-Json;" ^
    "$ver = $pkg.version;" ^
    "$url = \"https://github.com/electron/electron/releases/download/v$ver/electron-v$ver-win32-x64.zip\";" ^
    "$zip = \"$env:TEMP\electron-v$ver-win32-x64.zip\";" ^
    "Write-Host \"  Fetching Electron v$ver...\";" ^
    "Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing;" ^
    "if (Test-Path 'node_modules\electron\dist') { Remove-Item 'node_modules\electron\dist' -Recurse -Force };" ^
    "New-Item -ItemType Directory -Path 'node_modules\electron\dist' | Out-Null;" ^
    "Expand-Archive -Path $zip -DestinationPath 'node_modules\electron\dist' -Force;" ^
    "$pt = Join-Path (Get-Location) 'node_modules\electron\path.txt'; [System.IO.File]::WriteAllText($pt, 'electron.exe');" ^
    "Write-Host '  Electron binary installed successfully.'"
  if not exist node_modules\electron\dist\electron.exe (
    echo   Download or extraction failed. Check your internet connection and try again.
    popd
    exit /b 1
  )
  popd
)

pushd converter_app
call npm run dev
popd

endlocal
