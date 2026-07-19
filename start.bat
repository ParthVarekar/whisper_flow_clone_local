@echo off
title WhisperFlow - Voice Dictation Launcher
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo            Qwen3-ASR (1.7B) + LLM cleanup (gemma-4)
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
)

:: -----------------------------------------------------------------------
:: 5. Check if llama-server is running (for LLM cleanup); auto-start if not
:: -----------------------------------------------------------------------
echo [CHECK] Checking for llama-server on port 8081...
powershell -Command "$s = New-Object System.Net.Sockets.TcpClient; try { $s.Connect('127.0.0.1', 8081); exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] llama-server is already running on port 8081. LLM cleanup active.
) else (
    echo [STARTING] llama-server not running. Launching it now...
    if exist "D:\llama4\llama-server.exe" (
        start "llama-server" /min "D:\llama4\llama-server.exe" -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL --host 127.0.0.1 --port 8081 --ctx-size 32768 --n-gpu-layers 999 --parallel 2 --alias gemma-4-e2b-it --reasoning off --reasoning-budget 0
        echo [OK] llama-server process started in background window.
        echo        Waiting for it to load the model ^(may take 10-30 seconds^)...
        timeout /t 15 /nobreak >nul
        echo [OK] Wait complete. Proceeding with daemon startup.
    ) else (
        color 0E
        echo [WARNING] D:\llama4\llama-server.exe not found.
        echo   LLM cleanup will fail — daemon falls back to raw transcript.
        echo   To enable LLM cleanup, install llama.cpp and set the path in config.llama4.toml
        echo.
        color 0A
    )
)

:: -----------------------------------------------------------------------
:: 6. Start the WhisperFlow daemon
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon...
echo.
echo   Backend:  Qwen3-ASR (1.7B) + LLM cleanup (gemma-4)
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
