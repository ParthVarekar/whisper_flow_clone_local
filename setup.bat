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

:: =======================================================================
:: 1. Check Python
:: =======================================================================
where python >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Python not found. Install Python 3.10+ from python.org
    echo.
    pause
    exit /b 1
)
echo [OK] Python found.
echo.

:: =======================================================================
:: 2. Create venv + install deps
:: =======================================================================
echo [1/5] Setting up Python environment...
if not exist ".venv\Scripts\activate.bat" (
    if not exist ".qa-venv\Scripts\activate.bat" (
        python -m venv .venv
    )
)
set "VENV_DIR="
if exist ".qa-venv\Scripts\activate.bat" set "VENV_DIR=.qa-venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"
if defined VENV_DIR call "!VENV_DIR!\Scripts\activate.bat"

python -m pip install --upgrade pip -q 2>nul
python -c "import sounddevice" >nul 2>&1 || python -m pip install sounddevice -q
python -c "import pynput" >nul 2>&1 || python -m pip install pynput -q
python -c "import pystray" >nul 2>&1 || python -m pip install pystray Pillow -q
python -c "import numpy" >nul 2>&1 || python -m pip install numpy -q
python -c "import pyperclip" >nul 2>&1 || python -m pip install pyperclip -q
python -c "import tomllib" >nul 2>&1 || (python -c "import tomli" >nul 2>&1 || python -m pip install tomli -q)
echo [OK] Python dependencies installed.
echo.

:: =======================================================================
:: 3. Download whisper.cpp small.en model
:: =======================================================================
if not exist "models" mkdir models

echo [2/5] Downloading whisper.cpp small.en model (466MB)...
if exist "models\ggml-small.en.bin" (
    echo [SKIP] Already exists.
) else (
    curl -L --fail -o "models\ggml-small.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
    if exist "models\ggml-small.en.bin" (
        echo [OK] Downloaded.
    ) else (
        echo [WARNING] Download failed. You can download manually later.
    )
)
echo.

:: =======================================================================
:: 4. Download whisper-cli.exe (CUDA)
:: =======================================================================
if not exist "bin" mkdir bin

echo [3/5] Downloading CUDA whisper-cli.exe (266MB)...
if exist "bin\whisper-cli.exe" (
    echo [SKIP] Already exists.
) else (
    set "WHISPER_ZIP=%TEMP%\whisper_setup.zip"
    echo Downloading CUDA 12.4 binary...
    curl -L --fail -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip"
    if !ERRORLEVEL! NEQ 0 (
        echo CUDA 12.4 failed, trying CPU version...
        curl -L --fail -o "!WHISPER_ZIP!" "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip"
    )
    if exist "!WHISPER_ZIP!" (
        echo Extracting...
        set "EXTRACT_DIR=%TEMP%\whisper_setup_extract"
        if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
        powershell -Command "Expand-Archive -Path '!WHISPER_ZIP!' -DestinationPath '!EXTRACT_DIR!' -Force" 2>nul
        if exist "!EXTRACT_DIR!\whisper-cli.exe" (
            copy /Y "!EXTRACT_DIR!\whisper-cli.exe" "bin\whisper-cli.exe" >nul
            echo [OK] whisper-cli.exe installed.
        ) else (
            dir /s /b "!EXTRACT_DIR!\whisper*.exe" 2>nul | findstr /i "whisper-cli.exe" >nul
            if !ERRORLEVEL! EQU 0 (
                for /f "delims=" %%f in ('dir /s /b "!EXTRACT_DIR!\whisper-cli.exe" 2^>nul') do (
                    copy /Y "%%f" "bin\whisper-cli.exe" >nul
                    echo [OK] whisper-cli.exe installed.
                )
            ) else (
                echo [WARNING] Could not find whisper-cli.exe in archive.
            )
        )
        :: Copy DLLs
        if exist "!EXTRACT_DIR!" (
            for /f "delims=" %%f in ('dir /s /b "!EXTRACT_DIR!\*.dll" 2^>nul') do (
                copy /Y "%%f" "bin\" >nul 2>&1
            )
        )
        rmdir /s /q "!EXTRACT_DIR!" 2>nul
        del "!WHISPER_ZIP!" 2>nul
    ) else (
        echo [WARNING] Could not download whisper-cli.exe.
    )
)
echo.

:: =======================================================================
:: 5. Download llama-server.exe
:: =======================================================================
echo [4/5] Downloading llama-server.exe...
if exist "bin\llama-server.exe" (
    echo [SKIP] Already exists.
) else (
    set "LLAMA_ZIP=%TEMP%\llama_setup.zip"
    echo Downloading llama-server CUDA 12.4...
    curl -L --fail -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/b10015/llama-b10015-bin-win-cuda-12.4-x64.zip"
    if !ERRORLEVEL! NEQ 0 (
        echo CUDA failed, trying CPU version...
        curl -L --fail -o "!LLAMA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/b10015/llama-b10015-bin-win-cpu-x64.zip"
    )
    if exist "!LLAMA_ZIP!" (
        echo Extracting...
        set "EXTRACT_DIR=%TEMP%\llama_setup_extract"
        if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
        powershell -Command "Expand-Archive -Path '!LLAMA_ZIP!' -DestinationPath '!EXTRACT_DIR!' -Force" 2>nul
        :: Find llama-server.exe
        for /f "delims=" %%f in ('dir /s /b "!EXTRACT_DIR!\llama-server.exe" 2^>nul') do (
            copy /Y "%%f" "bin\llama-server.exe" >nul
            echo [OK] llama-server.exe installed.
        )
        :: Find llama-cli.exe
        for /f "delims=" %%f in ('dir /s /b "!EXTRACT_DIR!\llama-cli.exe" 2^>nul') do (
            copy /Y "%%f" "bin\llama-cli.exe" >nul
        )
        :: Copy all DLLs
        for /f "delims=" %%f in ('dir /s /b "!EXTRACT_DIR!\*.dll" 2^>nul') do (
            copy /Y "%%f" "bin\" >nul 2>&1
        )
        rmdir /s /q "!EXTRACT_DIR!" 2>nul
        del "!LLAMA_ZIP!" 2>nul
    ) else (
        echo [WARNING] Could not download llama-server.exe.
    )

    :: Download CUDA runtime DLLs
    if exist "bin\llama-server.exe" (
        echo Downloading CUDA runtime DLLs...
        set "CUDA_ZIP=%TEMP%\cudart_setup.zip"
        curl -L --fail -o "!CUDA_ZIP!" "https://github.com/ggml-org/llama.cpp/releases/download/b10015/cudart-llama-bin-win-cuda-12.4-x64.zip" 2>nul
        if exist "!CUDA_ZIP!" (
            set "CUDA_DIR=%TEMP%\cudart_setup_extract"
            if exist "!CUDA_DIR!" rmdir /s /q "!CUDA_DIR!"
            powershell -Command "Expand-Archive -Path '!CUDA_ZIP!' -DestinationPath '!CUDA_DIR!' -Force" 2>nul
            for /f "delims=" %%f in ('dir /s /b "!CUDA_DIR!\*.dll" 2^>nul') do (
                copy /Y "%%f" "bin\" >nul 2>&1
            )
            echo [OK] CUDA runtime DLLs installed.
            rmdir /s /q "!CUDA_DIR!" 2>nul
            del "!CUDA_ZIP!" 2>nul
        )
    )
)
echo.

:: =======================================================================
:: 6. Download Gemma-4 model
:: =======================================================================
echo [5/5] Downloading Gemma-4 E4B model (2.5GB)...
echo This may take 5-15 minutes. Please be patient.
echo.
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
    echo [SKIP] Already exists.
) else (
    curl -L --fail -o "models\gemma-4-e4b-it-q4_k_xl.gguf" "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-UD-Q4_K_XL.gguf"
    if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (
        echo [OK] Gemma-4 model downloaded.
    ) else (
        echo [WARNING] Download failed. You can download manually from:
        echo   https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF
    )
)
echo.

:: =======================================================================
:: DONE
:: =======================================================================
echo =======================================================================
echo               SETUP COMPLETE
echo =======================================================================
echo.
echo Results:
if exist "models\ggml-small.en.bin" (echo   [OK] small.en model) else (echo   [MISSING] small.en model)
if exist "bin\whisper-cli.exe" (echo   [OK] whisper-cli.exe) else (echo   [MISSING] whisper-cli.exe)
if exist "bin\llama-server.exe" (echo   [OK] llama-server.exe) else (echo   [MISSING] llama-server.exe)
if exist "models\gemma-4-e4b-it-q4_k_xl.gguf" (echo   [OK] Gemma-4 model) else (echo   [MISSING] Gemma-4 model)
echo.
echo Run start.bat to start WhisperFlow.
echo.
echo Press any key to close this window...
pause >nul
