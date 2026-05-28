@echo off
setlocal

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
  call npm install || ( popd & exit /b 1 )
  popd
)

pushd converter_app
call npm run dev
popd

endlocal
