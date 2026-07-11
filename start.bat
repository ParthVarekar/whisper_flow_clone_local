@echo off
title WhisperFlow - Voice Dictation Launcher
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo            whisper.cpp (base.en) + rule-based cleanup
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
:: -----------------------------------------------------------------------
echo [CHECK] Verifying Python dependencies...

python -c "import sounddevice" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing sounddevice ^(mic capture^)...
    python -m pip install sounddevice
)

python -c "import pynput" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing pynput ^(global hotkeys^)...
    python -m pip install pynput
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

python -c "import pyperclip" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing pyperclip ^(clipboard fallback^)...
    python -m pip install pyperclip
)

python -c "import tomllib" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    python -c "import tomli" >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [INSTALL] Installing tomli ^(TOML config for Python ^< 3.11^)...
        python -m pip install tomli
    )
)

:: Also install moonshine-voice (optional, for users who want to switch
:: to the faster-but-less-accurate Moonshine backend)
python -c "import moonshine_voice" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INSTALL] Installing moonshine-voice ^(optional backend, ~80MB^)...
    python -m pip install moonshine-voice
)

:: -----------------------------------------------------------------------
:: 4. Verify whisper.cpp binary and model exist
:: -----------------------------------------------------------------------
echo [CHECK] Verifying whisper.cpp installation...

set "WHISPER_BIN=C:\Users\Parth\Desktop\whisper\third_party\whisper.cpp-bin\whisper-bin-x64\Release\whisper-cli.exe"
set "WHISPER_MODEL=C:\Users\Parth\Desktop\whisper\models\ggml-base.en.bin"

if not exist "%WHISPER_BIN%" (
    color 0E
    echo [WARNING] whisper-cli.exe not found at:
    echo   %WHISPER_BIN%
    echo.
    echo The daemon will fail to transcribe. Either:
    echo   1. Install whisper.cpp at the expected path, OR
    echo   2. Edit config.llama4.toml to use a different backend (moonshine/qwen3_asr)
    echo.
    color 0A
)

if not exist "%WHISPER_MODEL%" (
    color 0E
    echo [WARNING] ggml-base.en.bin model not found at:
    echo   %WHISPER_MODEL%
    echo.
    echo Download it with:
    echo   cd C:\Users\Parth\Desktop\whisper\third_party\whisper.cpp
    echo   bash models\download-ggml-model.sh base.en
    echo   move models\ggml-base.en.bin C:\Users\Parth\Desktop\whisper\models\
    echo.
    color 0A
)

if exist "%WHISPER_BIN%" if exist "%WHISPER_MODEL%" (
    echo [OK] whisper.cpp binary and model found.
)

:: -----------------------------------------------------------------------
:: 5. Start the WhisperFlow daemon
::    Config: config.llama4.toml
::      - backend = "whisper_cpp"  (whisper-cli + ggml-base.en, 74M params)
::      - language = "en"          (English-only model)
::      - mode = "raw"             (rule-based cleanup, no LLM needed)
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon...
echo.
echo   Backend:  whisper.cpp + base.en (74M, English)
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
