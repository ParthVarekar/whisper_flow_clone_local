@echo off
title WhisperFlow - Voice Dictation Launcher
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo            Qwen3-ASR (1.7B) + rule-based cleanup
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

:: -----------------------------------------------------------------------
:: 4. Verify Qwen3-ASR binary and model files exist
:: -----------------------------------------------------------------------
echo [CHECK] Verifying Qwen3-ASR installation...

set "QWEN_BIN=C:\Users\Parth\Desktop\whisper\third_party\crispasr\crispasr.exe"
set "QWEN_MODEL=C:\Users\Parth\Desktop\whisper\models\qwen3-asr-1.7b-q4_k.gguf"
set "QWEN_MMPROJ=C:\Users\Parth\Desktop\whisper\models\mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"

set "ALL_FOUND=1"

if not exist "%QWEN_BIN%" (
    color 0E
    echo [WARNING] crispasr.exe not found at:
    echo   %QWEN_BIN%
    echo.
    set "ALL_FOUND=0"
    color 0A
)

if not exist "%QWEN_MODEL%" (
    color 0E
    echo [WARNING] Qwen3-ASR model not found at:
    echo   %QWEN_MODEL%
    echo.
    set "ALL_FOUND=0"
    color 0A
)

if "%ALL_FOUND%"=="1" (
    echo [OK] Qwen3-ASR binary and model found.
) else (
    echo [ERROR] Qwen3-ASR files are missing. The daemon will fail to transcribe.
    echo.
    echo To use a different backend, edit config.llama4.toml:
    echo   - whisper_cpp: needs whisper-cli.exe + ggml-*.bin model
    echo   - moonshine:   needs 'pip install moonshine-voice' (in-process, no binary)
    echo.
)

:: -----------------------------------------------------------------------
:: 5. Start the WhisperFlow daemon
::    Config: config.llama4.toml
::      - backend = "qwen3_asr"  (1.7B speech-LLM, most accurate)
::      - language = "en"        (English)
::      - mode = "raw"           (rule-based cleanup, no LLM needed)
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon...
echo.
echo   Backend:  Qwen3-ASR (1.7B, English, CrispASR)
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
