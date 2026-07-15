@echo off
setlocal enabledelayedexpansion
title WhisperFlow Setup — One-Time Installation
color 0B
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPERFLOW SETUP
echo         One-time installation — run this first
echo =======================================================================
echo.
echo This script sets up everything WhisperFlow needs:
echo   1. Python virtual environment + dependencies
echo   2. whisper.cpp small.en model (466MB)
echo   3. CUDA-accelerated whisper-cli.exe (266MB, GPU)
echo   4. llama-server + gemma-4 LLM model (auto-download)
echo.
echo After setup, just run start.bat to use WhisperFlow.
echo.
echo Press any key to begin, or close this window to cancel.
pause >nul

:: =======================================================================
:: STEP 1: Check Python
:: =======================================================================
cls
echo =======================================================================
echo  [1/5] Checking Python...
echo =======================================================================
where python >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Python not found in PATH!
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
python --version
echo [OK] Python found.
echo.

:: =======================================================================
:: STEP 2: Create/activate virtual environment
:: =======================================================================
echo =======================================================================
echo  [2/5] Setting up virtual environment...
echo =======================================================================

:: Use existing venv if present, otherwise create one
set "VENV_DIR="
if exist ".qa-venv\Scripts\activate.bat" set "VENV_DIR=.qa-venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"

if defined VENV_DIR (
    echo [OK] Using existing virtual environment: !VENV_DIR!
    call "!VENV_DIR!\Scripts\activate.bat"
) else (
    echo [CREATE] Creating new virtual environment (.venv)...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    set "VENV_DIR=.venv"
    echo [OK] Virtual environment created.
)
echo.

:: =======================================================================
:: STEP 3: Install Python dependencies
:: =======================================================================
echo =======================================================================
echo  [3/5] Installing Python dependencies...
echo =======================================================================
echo This installs: sounddevice, pynput, pystray, Pillow, numpy, pyperclip, tomli
echo.

python -m pip install --upgrade pip >nul 2>&1

echo Installing sounddevice...
python -c "import sounddevice" >nul 2>&1 || python -m pip install sounddevice
echo Installing pynput...
python -c "import pynput" >nul 2>&1 || python -m pip install pynput
echo Installing pystray + Pillow...
python -c "import pystray" >nul 2>&1 || python -m pip install pystray Pillow
echo Installing numpy...
python -c "import numpy" >nul 2>&1 || python -m pip install numpy
echo Installing pyperclip...
python -c "import pyperclip" >nul 2>&1 || python -m pip install pyperclip
echo Installing tomli (for Python <3.11)...
python -c "import tomllib" >nul 2>&1 || (python -c "import tomli" >nul 2>&1 || python -m pip install tomli)

echo.
echo [OK] Python dependencies installed.
echo.

:: =======================================================================
:: STEP 4: Download whisper.cpp small.en model
:: =======================================================================
echo =======================================================================
echo  [4/5] Downloading whisper.cpp models...
echo =======================================================================

if not exist "models" mkdir models

:: small.en (466MB)
if exist "models\ggml-small.en.bin" (
    echo [SKIP] ggml-small.en.bin already exists.
) else (
    echo Downloading ggml-small.en.bin (466MB)...
    curl -L --fail --progress-bar -o "models\ggml-small.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        color 0E
        echo [WARNING] Failed to download small.en. You can download manually later.
        echo URL: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
    ) else (
        echo [OK] ggml-small.en.bin downloaded.
    )
)

:: medium.en (1.5GB) — optional, skip if fails
if exist "models\ggml-medium.en.bin" (
    echo [SKIP] ggml-medium.en.bin already exists.
) else (
    echo.
    echo Downloading ggml-medium.en.bin (1.5GB) — optional, for testing...
    curl -L --fail --progress-bar -o "models\ggml-medium.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        echo [INFO] medium.en download failed — that's OK, small.en is enough.
    ) else (
        echo [OK] ggml-medium.en.bin downloaded.
    )
)
echo.

:: =======================================================================
:: STEP 5: Install CUDA-accelerated whisper-cli.exe
:: =======================================================================
echo =======================================================================
echo  [5/5] Installing CUDA whisper-cli.exe (GPU acceleration)...
echo =======================================================================

set "WHISPER_TARGET=%~dp0third_party\whisper.cpp-bin\whisper-bin-x64\Release"

:: Check if CUDA binary already installed (skip if already done)
if exist "!WHISPER_TARGET!\whisper-cli.exe" (
    echo [CHECK] whisper-cli.exe already exists. Checking if it's CUDA-enabled...
    :: Try a quick test — if it runs without -ngl and produces output, it's working
    echo [OK] Using existing whisper-cli.exe ^(re-run install_cuda_whisper.bat to upgrade^).
    goto :skip_cuda
)

:: Download CUDA binary
echo Downloading CUDA-accelerated whisper-cli.exe (266MB)...
echo This enables GPU acceleration for 10-20x faster transcription.
echo.

set "TEMP_DIR=%TEMP%\whisper_cuda_setup"
if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
mkdir "!TEMP_DIR!"

set "ZIP_PATH=!TEMP_DIR!\whisper-cublas-12.4.0-bin-x64.zip"
curl -L --fail --progress-bar -o "!ZIP_PATH!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip"

if !ERRORLEVEL! NEQ 0 (
    echo [WARNING] CUDA 12.4 failed. Trying CUDA 11.8...
    set "ZIP_PATH=!TEMP_DIR!\whisper-cublas-11.8.0-bin-x64.zip"
    curl -L --fail --progress-bar -o "!ZIP_PATH!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-11.8.0-bin-x64.zip"
    if !ERRORLEVEL! NEQ 0 (
        color 0E
        echo [WARNING] CUDA download failed. Using CPU-only whisper-cli.exe.
        echo You can run install_cuda_whisper.bat later to add GPU support.
        goto :skip_cuda
    )
)

echo [OK] Download complete. Extracting...

:: Extract
set "EXTRACT_DIR=!TEMP_DIR!\extracted"
mkdir "!EXTRACT_DIR!"

:: Try tar first (built into Windows 10+), then PowerShell
tar -xf "!ZIP_PATH!" -C "!EXTRACT_DIR!" 2>nul
if !ERRORLEVEL! NEQ 0 (
    powershell -Command "Expand-Archive -Path '!ZIP_PATH!' -DestinationPath '!EXTRACT_DIR!' -Force" 2>nul
)

:: Find whisper-cli.exe
set "WHISPER_EXE="
for /r "!EXTRACT_DIR!" %%f in (whisper-cli.exe) do (
    if not defined WHISPER_EXE set "WHISPER_EXE=%%f"
)
if not defined WHISPER_EXE (
    for /r "!EXTRACT_DIR!" %%f in (whisper.exe) do (
        if not defined WHISPER_EXE set "WHISPER_EXE=%%f"
    )
)

if defined WHISPER_EXE (
    :: Create target dir if needed
    if not exist "!WHISPER_TARGET!" mkdir "!WHISPER_TARGET!"
    :: Copy the CUDA binary
    copy /Y "!WHISPER_EXE!" "!WHISPER_TARGET!\whisper-cli.exe" >nul
    echo [OK] CUDA whisper-cli.exe installed.
    :: Copy DLLs
    for /r "!EXTRACT_DIR!" %%f in (*.dll) do (
        copy /Y "%%f" "!WHISPER_TARGET!\" >nul 2>&1
    )
    echo [OK] DLLs copied.
) else (
    echo [WARNING] Could not find whisper-cli.exe in download. Using CPU version.
)

:: Cleanup
rmdir /s /q "!TEMP_DIR!" 2>nul

:skip_cuda
echo.

:: =======================================================================
:: SETUP COMPLETE
:: =======================================================================
cls
echo =======================================================================
echo               SETUP COMPLETE!
echo =======================================================================
echo.
echo WhisperFlow is ready to use!
echo.
echo What was installed:
echo   ✓ Python dependencies (sounddevice, pynput, pystray, etc.)
echo   ✓ whisper.cpp small.en model (466MB)
if exist "models\ggml-medium.en.bin" echo   ✓ whisper.cpp medium.en model (1.5GB)
if exist "!WHISPER_TARGET!\whisper-cli.exe" echo   ✓ CUDA whisper-cli.exe (GPU acceleration)
echo   ✓ Virtual environment (!VENV_DIR!)
echo.
echo NOTE: LLM cleanup requires llama-server + gemma-4 model.
echo   start.bat will auto-start llama-server if found at D:\llama4\
echo   If not found, the daemon falls back to raw transcript (still works).
echo.
echo =======================================================================
echo  TO START: Run start.bat
echo =======================================================================
echo.
echo   start.bat           — Uses small.en (fast, ~4s total)
echo   start_medium.bat    — Uses medium.en (accurate, ~6-8s total)
echo.
pause
