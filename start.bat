@echo off
title WhisperFlow - Voice Dictation Launcher
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo            Moonshine Tiny (27M) + rule-based cleanup
echo =======================================================================
echo.

:: -----------------------------------------------------------------------
:: 1. Check Python is available
:: -----------------------------------------------------------------------
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.10+ and add it to your System PATH.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------
:: 2. Activate virtual environment if one exists (recommended)
::    Looks for .venv or .qa-venv in the project root.
:: -----------------------------------------------------------------------
set "VENV_DIR="
if exist ".qa-venv\Scripts\activate.bat" set "VENV_DIR=.qa-venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"

if defined VENV_DIR (
    echo [OK] Activating virtual environment: %VENV_DIR%
    call "%VENV_DIR%\Scripts\activate.bat"
) else (
    echo [INFO] No virtual environment found. Using system Python.
    echo        (recommended: create one with: python -m venv .venv)
)

:: -----------------------------------------------------------------------
:: 3. Ensure required Python packages are installed
::    Moonshine runs in-process via ONNX Runtime — no external server needed.
:: -----------------------------------------------------------------------
echo [CHECK] Verifying Python dependencies...

python -c "import moonshine_voice" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing moonshine-voice ^(one-time, ~80MB download^)...
    python -m pip install moonshine-voice
)

python -c "import sounddevice" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing sounddevice ^(mic capture^)...
    python -m pip install sounddevice
)

python -c "import pystray" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing pystray + Pillow ^(system tray^)...
    python -m pip install pystray Pillow
)

python -c "import numpy" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing numpy...
    python -m pip install numpy
)

:: -----------------------------------------------------------------------
:: 4. Verify the Moonshine model is available
::    (moonshine-voice auto-downloads "tiny-en" on first use)
:: -----------------------------------------------------------------------
echo [CHECK] Verifying Moonshine Tiny model...
python -c "from moonshine_voice import get_model_path; get_model_path('tiny-en'); print('[OK] Moonshine Tiny model ready')" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Could not pre-load Moonshine model. It will download on first dictation.
)

:: -----------------------------------------------------------------------
:: 5. Start the WhisperFlow daemon
::    Config: config.llama4.toml
::      - backend = "moonshine"  (in-process, no external server)
::      - language = "en"        (Moonshine Tiny is English-only)
::      - mode = "raw"           (rule-based cleanup, no LLM needed)
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon...
echo.
echo   Backend:  Moonshine Tiny (27M, English, ONNX Runtime)
echo   Cleanup:  Rule-based (formatting.py)
echo   Config:   config.llama4.toml
echo.
echo   Dictation hotkey:  Ctrl+Shift+Space  (hold to record)
echo   Command hotkey:    Ctrl+Shift+T      (select text, hold + speak)
echo   Quit:              right-click tray icon -^> Quit
echo =======================================================================
echo.

python -m whisper_flow daemon --config config.llama4.toml

echo.
echo =======================================================================
echo   WhisperFlow has stopped.
echo =======================================================================
pause
