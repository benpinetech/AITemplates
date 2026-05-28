@echo off
setlocal EnableDelayedExpansion

REM Launch the JDA -> Pine converter on Windows.
REM On first run: creates the venv, installs Python deps, installs npm deps.
REM On subsequent runs: just starts the Electron app.

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
  call npm install || ( popd ^& exit /b 1 )
  popd
)

REM Electron self-heal: postinstall sometimes fails to extract the prebuilt
REM binary on Windows (newer Node + extract-zip flake, antivirus mid-write,
REM or path-length limits). Detected by a missing electron.exe.
if not exist "converter_app\node_modules\electron\dist\electron.exe" (
  echo Electron binary missing - attempting repair...
  pushd converter_app
  if exist node_modules\electron\dist rmdir /s /q node_modules\electron\dist
  if exist node_modules\electron\path.txt del /q node_modules\electron\path.txt
  call npm install electron --no-save >nul 2>nul
  if not exist node_modules\electron\dist\electron.exe (
    echo   npm postinstall didn't extract the binary; trying manual unzip from cache...
    set "ZIP="
    for /f "delims=" %%F in ('dir /b /o-d "%LOCALAPPDATA%\electron\Cache\electron-v*-win32-*.zip" 2^>nul') do (
      if not defined ZIP set "ZIP=%LOCALAPPDATA%\electron\Cache\%%F"
    )
    if not defined ZIP (
      echo   No cached Electron zip in %LOCALAPPDATA%\electron\Cache.
      echo   Try: rmdir /s /q node_modules ^&^& npm install
      popd
      exit /b 1
    )
    if exist node_modules\electron\dist rmdir /s /q node_modules\electron\dist
    mkdir node_modules\electron\dist
    tar -xf "!ZIP!" -C node_modules\electron\dist
    if not exist node_modules\electron\dist\electron.exe (
      echo   Manual extract failed. Try: rmdir /s /q node_modules ^&^& npm install
      popd
      exit /b 1
    )
    ^> node_modules\electron\path.txt echo electron
    echo   Repaired via manual unzip.
  )
  popd
)

pushd converter_app
call npm run dev
popd

endlocal
