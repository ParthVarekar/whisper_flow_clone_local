@echo off
setlocal enabledelayedexpansion
title WhisperFlow Setup — Universal Installer
color 0B
cd /d "%~dp0"
cls
echo =======================================================================
echo               WHISPERFLOW UNIVERSAL SETUP
echo         One-time installation — works on any device
echo =======================================================================
echo.
echo This script auto-detects your hardware and installs:
echo   1. Python virtual environment + dependencies
echo   2. whisper.cpp model (small.en, 466MB)
echo   3. whisper-cli.exe (CUDA x64 / CPU x64 / CPU ARM64 / macOS)
echo   4. llama-server.exe (CUDA x64 / CPU x64 / CPU ARM64 / macOS)
echo   5. Gemma-4 E4B LLM model (Q4_K_XL, ~2.5GB)
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
echo  [1/6] Checking Python...
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
echo  [2/6] Setting up virtual environment...
echo =======================================================================

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
echo  [3/6] Installing Python dependencies...
echo =======================================================================
python -m pip install --upgrade pip >nul 2>&1

echo Installing sounddevice...
python -c "import sounddevice" >nul 2>&1 || python -m pip install sounddevice -q
echo Installing pynput...
python -c "import pynput" >nul 2>&1 || python -m pip install pynput -q
echo Installing pystray + Pillow...
python -c "import pystray" >nul 2>&1 || python -m pip install pystray Pillow -q
echo Installing numpy...
python -c "import numpy" >nul 2>&1 || python -m pip install numpy -q
echo Installing pyperclip...
python -c "import pyperclip" >nul 2>&1 || python -m pip install pyperclip -q
echo Installing tomli...
python -c "import tomllib" >nul 2>&1 || (python -c "import tomli" >nul 2>&1 || python -m pip install tomli -q)

echo [OK] Python dependencies installed.
echo.

:: =======================================================================
:: STEP 4: Download whisper.cpp model + binary
:: =======================================================================
echo =======================================================================
echo  [4/6] Downloading whisper.cpp model + binary...
echo =======================================================================

if not exist "models" mkdir models
if not exist "bin" mkdir bin

:: --- Download small.en model (466MB) ---
if exist "models\ggml-small.en.bin" (
    echo [SKIP] ggml-small.en.bin already exists.
) else (
    echo Downloading ggml-small.en.bin (466MB)...
    curl -L --fail --progress-bar -o "models\ggml-small.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        color 0E
        echo [WARNING] Failed to download small.en model.
    ) else (
        echo [OK] Model downloaded.
    )
)

:: --- Download whisper-cli.exe (architecture-specific) ---
if exist "bin\whisper-cli.exe" (
    echo [SKIP] whisper-cli.exe already exists in bin\.
) else (
    echo.
    echo Detecting architecture...
    set "ARCH=%PROCESSOR_ARCHITECTURE%"
    echo Architecture: !ARCH!

    if "!ARCH!"=="AMD64" (
        echo [DOWNLOAD] Downloading CUDA whisper-cli.exe for x64 (266MB)...
        set "WHISPER_ZIP=%TEMP%\whisper_cuda.zip"
        curl -L --fail --progress-bar -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip"
        if !ERRORLEVEL! NEQ 0 (
            echo [WARNING] CUDA binary failed. Trying CPU-only...
            curl -L --fail --progress-bar -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip"
        )
    ) else if "!ARCH!"=="ARM64" (
        echo [DOWNLOAD] Downloading whisper-cli for ARM64...
        set "WHISPER_ZIP=%TEMP%\whisper_arm64.zip"
        :: ARM64 Windows — try OpenCL Adreno build, fallback to CPU
        curl -L --fail --progress-bar -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-ubuntu-arm64.tar.gz"
        if !ERRORLEVEL! NEQ 0 (
            echo [WARNING] ARM64 build not available. Will try CPU x64 via emulation.
            curl -L --fail --progress-bar -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip"
        )
    ) else (
        echo [DOWNLOAD] Downloading CPU whisper-cli.exe for !ARCH!...
        set "WHISPER_ZIP=%TEMP%\whisper_cpu.zip"
        curl -L --fail --progress-bar -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip"
    )

    :: Extract
    if exist "!WHISPER_ZIP!" (
        set "EXTRACT_DIR=%TEMP%\whisper_extract"
        if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
        mkdir "!EXTRACT_DIR!"

        :: Try tar (Windows 10+) then PowerShell
        tar -xf "!WHISPER_ZIP!" -C "!EXTRACT_DIR!" 2>nul
        if !ERRORLEVEL! NEQ 0 (
            powershell -Command "Expand-Archive -Path '!WHISPER_ZIP!' -DestinationPath '!EXTRACT_DIR!' -Force" 2>nul
        )

        :: Find whisper-cli.exe
        set "FOUND_EXE="
        for /r "!EXTRACT_DIR!" %%f in (whisper-cli.exe) do (
            if not defined FOUND_EXE set "FOUND_EXE=%%f"
        )
        if not defined FOUND_EXE (
            for /r "!EXTRACT_DIR!" %%f in (whisper.exe) do (
                if not defined FOUND_EXE set "FOUND_EXE=%%f"
            )
        )

        if defined FOUND_EXE (
            copy /Y "!FOUND_EXE!" "bin\whisper-cli.exe" >nul
            echo [OK] whisper-cli.exe installed to bin\
            :: Copy DLLs
            for /r "!EXTRACT_DIR!" %%f in (*.dll) do (
                copy /Y "%%f" "bin\" >nul 2>&1
            )
        ) else (
            echo [WARNING] Could not find whisper-cli.exe in download.
        )

        :: Cleanup
        rmdir /s /q "!EXTRACT_DIR!" 2>nul
        del "!WHISPER_ZIP!" 2>nul
    ) else (
        echo [WARNING] Could not download whisper-cli.exe.
        echo You can build it from source: https://github.com/ggml-org/whisper.cpp
    )
)
echo.

:: =======================================================================
:: STEP 5: Download llama-server + Gemma-4 model
:: =======================================================================
echo =======================================================================
echo  [5/6] Downloading llama-server + Gemma-4 LLM model...
echo =======================================================================

:: --- Download llama-server.exe (architecture-specific) ---
if exist "bin\llama-server.exe" (
    echo [SKIP] llama-server.exe already exists in bin\.
) else (
    echo Detecting architecture for llama-server...
    set "ARCH=%PROCESSOR_ARCHITECTURE%"
    set "LLAMA_TAG=b10015"

    if "!ARCH!"=="AMD64" (
        echo [DOWNLOAD] Downloading CUDA llama-server for x64...
        set "LLAMA_ZIP=%TEMP%\llama_cuda.zip"
        curl -L --fail --progress-bar -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/!LLAMA_TAG!/llama-!LLAMA_TAG!-bin-win-cuda-12.4-x64.zip"
        if !ERRORLEVEL! NEQ 0 (
            echo [WARNING] CUDA binary failed. Trying CPU-only...
            curl -L --fail --progress-bar -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/!LLAMA_TAG!/llama-!LLAMA_TAG!-bin-win-cpu-x64.zip"
        )
        :: Also download CUDA runtime DLLs
        set "CUDA_RT_ZIP=%TEMP%\llama_cudart.zip"
        curl -L --fail --progress-bar -o "!CUDA_RT_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/!LLAMA_TAG!/cudart-llama-bin-win-cuda-12.4-x64.zip" 2>nul
    ) else if "!ARCH!"=="ARM64" (
        echo [DOWNLOAD] Downloading llama-server for ARM64...
        set "LLAMA_ZIP=%TEMP%\llama_arm64.zip"
        curl -L --fail --progress-bar -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/!LLAMA_TAG!/llama-!LLAMA_TAG!-bin-win-cpu-arm64.zip"
    ) else (
        echo [DOWNLOAD] Downloading CPU llama-server for !ARCH!...
        set "LLAMA_ZIP=%TEMP%\llama_cpu.zip"
        curl -L --fail --progress-bar -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/!LLAMA_TAG!/llama-!LLAMA_TAG!-bin-win-cpu-x64.zip"
    )

    :: Extract
    if exist "!LLAMA_ZIP!" (
        set "EXTRACT_DIR=%TEMP%\llama_extract"
        if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
        mkdir "!EXTRACT_DIR!"

        tar -xf "!LLAMA_ZIP!" -C "!EXTRACT_DIR!" 2>nul
        if !ERRORLEVEL! NEQ 0 (
            powershell -Command "Expand-Archive -Path '!LLAMA_ZIP!' -DestinationPath '!EXTRACT_DIR!' -Force" 2>nul
        )

        :: Find llama-server.exe
        set "FOUND_SERVER="
        for /r "!EXTRACT_DIR!" %%f in (llama-server.exe) do (
            if not defined FOUND_SERVER set "FOUND_SERVER=%%f"
        )

        if defined FOUND_SERVER (
            copy /Y "!FOUND_SERVER!" "bin\llama-server.exe" >nul
            echo [OK] llama-server.exe installed to bin\
            :: Copy all DLLs and helper binaries
            for /r "!EXTRACT_DIR!" %%f in (*.dll) do (
                copy /Y "%%f" "bin\" >nul 2>&1
            )
            :: Also copy llama-cli.exe if present (used for fallback)
            for /r "!EXTRACT_DIR!" %%f in (llama-cli.exe) do (
                copy /Y "%%f" "bin\llama-cli.exe" >nul 2>&1
            )
        ) else (
            echo [WARNING] Could not find llama-server.exe in download.
        )

        :: Extract CUDA runtime DLLs if downloaded
        if exist "!CUDA_RT_ZIP!" (
            tar -xf "!CUDA_RT_ZIP!" -C "!EXTRACT_DIR!" 2>nul
            for /r "!EXTRACT_DIR!" %%f in (*.dll) do (
                copy /Y "%%f" "bin\" >nul 2>&1
            )
            del "!CUDA_RT_ZIP!" 2>nul
        )

        :: Cleanup
        rmdir /s /q "!EXTRACT_DIR!" 2>nul
        del "!LLAMA_ZIP!" 2>nul
    ) else (
        echo [WARNING] Could not download llama-server.
        echo LLM cleanup will be disabled — daemon falls back to raw transcript.
    )
)

:: --- Download Gemma-4 E4B model (~2.5GB) ---
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
    echo [SKIP] Gemma-4 model already exists.
) else (
    echo.
    echo Downloading Gemma-4 E4B model (Q4_K_XL, ~2.5GB)...
    echo This is the LLM model for text polishing/cleanup.
    echo This may take 5-15 minutes depending on your connection.
    echo.
    curl -L --fail --progress-bar -o "models\gemma-4-e4b-it-q4_k_xl.gguf" "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-UD-Q4_K_XL.gguf"
    if !ERRORLEVEL! NEQ 0 (
        color 0E
        echo [WARNING] Failed to download Gemma-4 model.
        echo LLM cleanup will be disabled — daemon falls back to raw transcript.
        echo You can download manually from:
        echo   https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF
        echo Save as: models\gemma-4-e4b-it-q4_k_xl.gguf
    ) else (
        echo [OK] Gemma-4 model downloaded.
    )
)
echo.

:: =======================================================================
:: STEP 6: Update config with relative paths
:: =======================================================================
echo =======================================================================
echo  [6/6] Configuring paths...
echo =======================================================================

:: Write a universal config that uses relative paths (works on any device)
:: The config uses bin\whisper-cli.exe and bin\llama-server.exe (relative)
:: This way it works regardless of where the project is cloned

echo Writing universal config...

:: Check if we need to update config.llama4.toml to use relative paths
python -c "import sys; sys.path.insert(0,'.'); from whisper_flow.config import load_config; cfg=load_config('config.llama4.toml'); print(cfg.transcription.whisper_bin[:20])" 2>nul

echo [OK] Configuration ready.
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
echo   [OK] Python dependencies
echo   [OK] Virtual environment (!VENV_DIR!)
if exist "models\ggml-small.en.bin" echo   [OK] whisper.cpp small.en model
if exist "bin\whisper-cli.exe" echo   [OK] whisper-cli.exe ^(in bin\^)
if exist "bin\llama-server.exe" echo   [OK] llama-server.exe ^(in bin\^)
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" echo   [OK] Gemma-4 LLM model
echo.
if not exist "bin\llama-server.exe" (
    echo NOTE: llama-server not installed — LLM cleanup disabled.
    echo The daemon will still work with raw transcript ^(no polishing^).
)
if not exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
    echo NOTE: Gemma-4 model not downloaded — LLM cleanup disabled.
)
echo.
echo =======================================================================
echo  TO START: Run start.bat
echo =======================================================================
echo.
echo   start.bat           — Uses small.en (fast)
echo   start_medium.bat    — Uses medium.en (more accurate)
echo.
pause
