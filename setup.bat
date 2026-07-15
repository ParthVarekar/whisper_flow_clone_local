@echo off
setlocal enabledelayedexpansion
title WhisperFlow Setup
color 0B
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPERFLOW SETUP
echo =======================================================================
echo.

:: Check Python
where python >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

:: Create venv
if not exist ".venv\Scripts\activate.bat" (
    if not exist ".qa-venv\Scripts\activate.bat" (
        echo [1/5] Creating virtual environment...
        python -m venv .venv
    )
)

:: Activate venv
set "VENV_DIR="
if exist ".qa-venv\Scripts\activate.bat" set "VENV_DIR=.qa-venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"
if defined VENV_DIR call "!VENV_DIR!\Scripts\activate.bat"

:: Install pip deps
echo [2/5] Installing Python dependencies...
python -m pip install --upgrade pip -q 2>nul
python -c "import sounddevice" >nul 2>&1 || python -m pip install sounddevice -q
python -c "import pynput" >nul 2>&1 || python -m pip install pynput -q
python -c "import pystray" >nul 2>&1 || python -m pip install pystray Pillow -q
python -c "import numpy" >nul 2>&1 || python -m pip install numpy -q
python -c "import pyperclip" >nul 2>&1 || python -m pip install pyperclip -q
python -c "import tomllib" >nul 2>&1 || (python -c "import tomli" >nul 2>&1 || python -m pip install tomli -q)
echo [OK] Dependencies installed.

:: Create directories
if not exist "models" mkdir models
if not exist "bin" mkdir bin

:: Download small.en model
echo.
echo [3/5] Downloading whisper.cpp small.en model (466MB)...
if exist "models\ggml-small.en.bin" (
    echo [SKIP] Already exists.
) else (
    powershell -ExecutionPolicy Bypass -File "%~dp0_download_helper.ps1" -Mode "model" -Url "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin" -OutPath "models\ggml-small.en.bin"
)

:: Download whisper-cli.exe
echo.
echo [4/5] Downloading CUDA whisper-cli.exe (266MB)...
if exist "bin\whisper-cli.exe" (
    echo [SKIP] Already exists.
) else (
    powershell -ExecutionPolicy Bypass -File "%~dp0_download_helper.ps1" -Mode "whisper" -OutDir "bin"
)

:: Download llama-server.exe + Gemma-4 model
echo.
echo [5/5] Downloading llama-server + Gemma-4 model (2.7GB total)...
if not exist "bin\llama-server.exe" (
    powershell -ExecutionPolicy Bypass -File "%~dp0_download_helper.ps1" -Mode "llama" -OutDir "bin"
)
if not exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
    echo Downloading Gemma-4 E4B model (2.5GB — this takes 5-15 min)...
    powershell -ExecutionPolicy Bypass -File "%~dp0_download_helper.ps1" -Mode "model" -Url "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-UD-Q4_K_XL.gguf" -OutPath "models\gemma-4-e4b-it-q4_k_xl.gguf"
)

:: Done
echo.
echo =======================================================================
echo               SETUP COMPLETE
echo =======================================================================
echo.
if exist "models\ggml-small.en.bin" (echo   [OK] small.en model) else (echo   [MISSING] small.en model)
if exist "bin\whisper-cli.exe" (echo   [OK] whisper-cli.exe) else (echo   [MISSING] whisper-cli.exe)
if exist "bin\llama-server.exe" (echo   [OK] llama-server.exe) else (echo   [MISSING] llama-server.exe)
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (echo   [OK] Gemma-4 model) else (echo   [MISSING] Gemma-4 model)
echo.
echo Run start.bat to start WhisperFlow.
echo.
pause
