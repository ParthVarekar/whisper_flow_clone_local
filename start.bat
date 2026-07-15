@echo off
setlocal enabledelayedexpansion
title WhisperFlow - Voice Dictation Launcher
color 0A
cd /d "%~dp0"
cls

:: =======================================================================
:: First-run check: if setup hasn't been done, run it automatically
:: =======================================================================
if not exist ".venv\Scripts\activate.bat" (
    if not exist ".qa-venv\Scripts\activate.bat" (
        echo [SETUP] First run detected. Running setup automatically...
        echo.
        call setup.bat
        if !ERRORLEVEL! NEQ 0 (
            echo [ERROR] Setup failed. Please run setup.bat manually.
            pause
            exit /b 1
        )
        cd /d "%~dp0"
    )
)

:: Also check if models are missing
if not exist "models\ggml-small.en.bin" (
    echo [SETUP] Models not found. Running setup...
    call setup.bat
    cd /d "%~dp0"
)

echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo            whisper.cpp (small.en) + LLM cleanup (gemma-4)
echo =======================================================================
echo.

:: -----------------------------------------------------------------------
:: 1. Check Python is available
:: -----------------------------------------------------------------------
where python >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.10+ and add it to your System PATH.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------
:: 2. Activate virtual environment
:: -----------------------------------------------------------------------
set "VENV_DIR="
if exist ".qa-venv\Scripts\activate.bat" set "VENV_DIR=.qa-venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"

if defined VENV_DIR (
    echo [OK] Activating virtual environment: !VENV_DIR!
    call "!VENV_DIR!\Scripts\activate.bat"
) else (
    echo [INFO] No virtual environment found. Using system Python.
)

:: -----------------------------------------------------------------------
:: 3. Quick dependency check (auto-install if missing)
:: -----------------------------------------------------------------------
echo [CHECK] Verifying dependencies...

python -c "import sounddevice" >nul 2>&1 || (echo [INSTALL] sounddevice... && python -m pip install sounddevice -q)
python -c "import pynput" >nul 2>&1 || (echo [INSTALL] pynput... && python -m pip install pynput -q)
python -c "import pystray" >nul 2>&1 || (echo [INSTALL] pystray Pillow... && python -m pip install pystray Pillow -q)
python -c "import numpy" >nul 2>&1 || (echo [INSTALL] numpy... && python -m pip install numpy -q)
python -c "import pyperclip" >nul 2>&1 || (echo [INSTALL] pyperclip... && python -m pip install pyperclip -q)
python -c "import tomllib" >nul 2>&1 || (python -c "import tomli" >nul 2>&1 || (echo [INSTALL] tomli... && python -m pip install tomli -q))

echo [OK] Dependencies verified.

:: -----------------------------------------------------------------------
:: 4. Verify whisper.cpp model
:: -----------------------------------------------------------------------
set "WHISPER_MODEL=C:\Users\Parth\Desktop\whisper\models\ggml-small.en.bin"
if not exist "!WHISPER_MODEL!" (
    color 0E
    echo [WARNING] ggml-small.en.bin not found. Run setup.bat to download it.
    echo.
    color 0A
) else (
    echo [OK] Model found: small.en
)

:: -----------------------------------------------------------------------
:: 5. Auto-start llama-server (for LLM cleanup)
:: -----------------------------------------------------------------------
echo [CHECK] Checking for llama-server on port 8081...
powershell -Command "$s = New-Object System.Net.Sockets.TcpClient; try { $s.Connect('127.0.0.1', 8081); exit 0 } catch { exit 1 }" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo [OK] llama-server is already running on port 8081.
) else (
    echo [STARTING] llama-server not running. Launching it now...
    if exist "D:\llama4\llama-server.exe" (
        start "llama-server" /min "D:\llama4\llama-server.exe" -hf unsloth/gemma-4-E4B-it-GGUF:UD-Q4_K_XL --host 127.0.0.1 --port 8081 --ctx-size 32768 --n-gpu-layers 999 --parallel 2 --alias gemma-4-e4b-it --reasoning off --reasoning-budget 0
        echo [OK] llama-server started. Waiting for model to load ^(15s^)...
        timeout /t 15 /nobreak >nul
        echo [OK] Ready.
    ) else (
        color 0E
        echo [WARNING] llama-server not found at D:\llama4\llama-server.exe
        echo   LLM cleanup disabled — daemon will use raw transcript.
        echo   To enable: install llama.cpp and set path in config.llama4.toml
        echo.
        color 0A
    )
)

:: -----------------------------------------------------------------------
:: 6. Start the daemon
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon...
echo.
echo   Backend:  whisper.cpp (small.en) + LLM cleanup (gemma-4)
echo   Config:   config.llama4.toml
echo.
echo   Dictation:  Ctrl+Shift+Space  (hold to record)
echo   Transform:  Ctrl+Shift+T      (select text + speak)
echo   Quit:       right-click tray icon -^> Quit
echo =======================================================================
echo.

python -m whisper_flow daemon --config config.llama4.toml

echo.
echo =======================================================================
echo   WhisperFlow has stopped.
echo =======================================================================
pause
