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
    curl -L --fail -o "models\ggml-small.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        echo [WARNING] Failed. You can download manually later.
    ) else (
        echo [OK] Downloaded.
    )
)

:: Download whisper-cli.exe using PowerShell
echo.
echo [4/5] Downloading CUDA whisper-cli.exe (266MB)...
if exist "bin\whisper-cli.exe" (
    echo [SKIP] Already exists.
) else (
    powershell -ExecutionPolicy Bypass -Command ^
        "Write-Host 'Downloading...';" ^
        "$ProgressPreference='SilentlyContinue';" ^
        "try {" ^
        "  Invoke-WebRequest -Uri 'https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip' -OutFile '$env:TEMP\whisper_cuda.zip' -UseBasicParsing;" ^
        "  Write-Host 'Extracting...';" ^
        "  Expand-Archive -Path '$env:TEMP\whisper_cuda.zip' -DestinationPath '$env:TEMP\whisper_extract' -Force;" ^
        "  $exe = Get-ChildItem -Path '$env:TEMP\whisper_extract' -Recurse -Filter 'whisper-cli.exe' | Select-Object -First 1;" ^
        "  if ($exe) {" ^
        "    Copy-Item $exe.FullName -Destination 'bin\whisper-cli.exe' -Force;" ^
        "    Write-Host '[OK] whisper-cli.exe installed';" ^
        "    Get-ChildItem -Path '$env:TEMP\whisper_extract' -Recurse -Filter '*.dll' | ForEach-Object { Copy-Item $_.FullName -Destination 'bin\' -Force };" ^
        "    Write-Host '[OK] DLLs copied';" ^
        "  } else {" ^
        "    Write-Host '[WARNING] whisper-cli.exe not found in archive';" ^
        "  }" ^
        "  Remove-Item '$env:TEMP\whisper_cuda.zip' -Force -ErrorAction SilentlyContinue;" ^
        "  Remove-Item '$env:TEMP\whisper_extract' -Recurse -Force -ErrorAction SilentlyContinue;" ^
        "} catch {" ^
        "  Write-Host '[WARNING] CUDA download failed. Trying CPU version...';" ^
        "  try {" ^
        "    Invoke-WebRequest -Uri 'https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip' -OutFile '$env:TEMP\whisper_cpu.zip' -UseBasicParsing;" ^
        "    Expand-Archive -Path '$env:TEMP\whisper_cpu.zip' -DestinationPath '$env:TEMP\whisper_extract2' -Force;" ^
        "    $exe = Get-ChildItem -Path '$env:TEMP\whisper_extract2' -Recurse -Filter 'whisper-cli.exe' | Select-Object -First 1;" ^
        "    if ($exe) { Copy-Item $exe.FullName -Destination 'bin\whisper-cli.exe' -Force; Write-Host '[OK] CPU whisper-cli.exe installed'; }" ^
        "    Remove-Item '$env:TEMP\whisper_cpu.zip' -Force -ErrorAction SilentlyContinue;" ^
        "    Remove-Item '$env:TEMP\whisper_extract2' -Recurse -Force -ErrorAction SilentlyContinue;" ^
        "  } catch {" ^
        "    Write-Host '[WARNING] Both downloads failed';" ^
        "  }" ^
        "}"
)

:: Download llama-server.exe + Gemma-4 model using PowerShell
echo.
echo [5/5] Downloading llama-server + Gemma-4 model (2.7GB total)...

:: llama-server.exe
if exist "bin\llama-server.exe" (
    echo [SKIP] llama-server.exe already exists.
) else (
    powershell -ExecutionPolicy Bypass -Command ^
        "Write-Host 'Downloading llama-server (CUDA x64)...';" ^
        "$ProgressPreference='SilentlyContinue';" ^
        "try {" ^
        "  Invoke-WebRequest -Uri 'https://github.com/ggml-org/llama.cpp/releases/download/b10015/llama-b10015-bin-win-cuda-12.4-x64.zip' -OutFile '$env:TEMP\llama_cuda.zip' -UseBasicParsing;" ^
        "  Expand-Archive -Path '$env:TEMP\llama_cuda.zip' -DestinationPath '$env:TEMP\llama_extract' -Force;" ^
        "  $srv = Get-ChildItem -Path '$env:TEMP\llama_extract' -Recurse -Filter 'llama-server.exe' | Select-Object -First 1;" ^
        "  if ($srv) {" ^
        "    Copy-Item $srv.FullName -Destination 'bin\llama-server.exe' -Force;" ^
        "    Write-Host '[OK] llama-server.exe installed';" ^
        "    Get-ChildItem -Path '$env:TEMP\llama_extract' -Recurse -Filter '*.dll' | ForEach-Object { Copy-Item $_.FullName -Destination 'bin\' -Force };" ^
        "    $cli = Get-ChildItem -Path '$env:TEMP\llama_extract' -Recurse -Filter 'llama-cli.exe' | Select-Object -First 1;" ^
        "    if ($cli) { Copy-Item $cli.FullName -Destination 'bin\llama-cli.exe' -Force };" ^
        "  }" ^
        "  Remove-Item '$env:TEMP\llama_cuda.zip' -Force -ErrorAction SilentlyContinue;" ^
        "  Remove-Item '$env:TEMP\llama_extract' -Recurse -Force -ErrorAction SilentlyContinue;" ^
        "} catch {" ^
        "  Write-Host '[WARNING] CUDA llama-server failed. Trying CPU...';" ^
        "  try {" ^
        "    Invoke-WebRequest -Uri 'https://github.com/ggml-org/llama.cpp/releases/download/b10015/llama-b10015-bin-win-cpu-x64.zip' -OutFile '$env:TEMP\llama_cpu.zip' -UseBasicParsing;" ^
        "    Expand-Archive -Path '$env:TEMP\llama_cpu.zip' -DestinationPath '$env:TEMP\llama_extract2' -Force;" ^
        "    $srv = Get-ChildItem -Path '$env:TEMP\llama_extract2' -Recurse -Filter 'llama-server.exe' | Select-Object -First 1;" ^
        "    if ($srv) {" ^
        "      Copy-Item $srv.FullName -Destination 'bin\llama-server.exe' -Force;" ^
        "      Write-Host '[OK] CPU llama-server.exe installed';" ^
        "      Get-ChildItem -Path '$env:TEMP\llama_extract2' -Recurse -Filter '*.dll' | ForEach-Object { Copy-Item $_.FullName -Destination 'bin\' -Force };" ^
        "    }" ^
        "    Remove-Item '$env:TEMP\llama_cpu.zip' -Force -ErrorAction SilentlyContinue;" ^
        "    Remove-Item '$env:TEMP\llama_extract2' -Recurse -Force -ErrorAction SilentlyContinue;" ^
        "  } catch {" ^
        "    Write-Host '[WARNING] llama-server download failed';" ^
        "  }" ^
        "}"

    :: Also download CUDA runtime DLLs
    if exist "bin\llama-server.exe" (
        powershell -ExecutionPolicy Bypass -Command ^
            "$ProgressPreference='SilentlyContinue';" ^
            "try {" ^
            "  Invoke-WebRequest -Uri 'https://github.com/ggml-org/llama.cpp/releases/download/b10015/cudart-llama-bin-win-cuda-12.4-x64.zip' -OutFile '$env:TEMP\cudart.zip' -UseBasicParsing;" ^
            "  Expand-Archive -Path '$env:TEMP\cudart.zip' -DestinationPath '$env:TEMP\cudart_extract' -Force;" ^
            "  Get-ChildItem -Path '$env:TEMP\cudart_extract' -Recurse -Filter '*.dll' | ForEach-Object { Copy-Item $_.FullName -Destination 'bin\' -Force };" ^
            "  Write-Host '[OK] CUDA runtime DLLs installed';" ^
            "  Remove-Item '$env:TEMP\cudart.zip' -Force -ErrorAction SilentlyContinue;" ^
            "  Remove-Item '$env:TEMP\cudart_extract' -Recurse -Force -ErrorAction SilentlyContinue;" ^
            "} catch { Write-Host '[INFO] CUDA runtime DLLs not needed for CPU build' }"
    )
)

:: Gemma-4 model
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
    echo [SKIP] Gemma-4 model already exists.
) else (
    echo Downloading Gemma-4 E4B model (2.5GB — this takes 5-15 min)...
    powershell -ExecutionPolicy Bypass -Command ^
        "$ProgressPreference='SilentlyContinue';" ^
        "Write-Host 'Downloading Gemma-4 E4B (Q4_K_XL, 2.5GB)...';" ^
        "try {" ^
        "  Invoke-WebRequest -Uri 'https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-UD-Q4_K_XL.gguf' -OutFile 'models\gemma-4-e4b-it-q4_k_xl.gguf' -UseBasicParsing;" ^
        "  Write-Host '[OK] Gemma-4 model downloaded';" ^
        "} catch {" ^
        "  Write-Host '[WARNING] Failed to download Gemma-4 model';" ^
        "  Write-Host 'You can download manually from: https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF';" ^
        "}"
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
