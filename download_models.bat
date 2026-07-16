@echo off
setlocal enabledelayedexpansion
title WhisperFlow Model Downloader — small.en + medium.en
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo           WHISPERFLOW MODEL DOWNLOADER
echo           Downloading ggml-small.en + ggml-medium.en
echo =======================================================================
echo.
echo These models will be used for fast transcription (target: ^<5s total).
echo   small.en  = 466MB, 244M params, ~1.5s per 30s audio
echo   medium.en = 1.5GB, 769M params, ~3-5s per 30s audio
echo.
echo You need both — we will test and select the best one.
echo.

:: -----------------------------------------------------------------------
:: Check if curl is available (built into Windows 10+)
:: -----------------------------------------------------------------------
where curl >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] curl not found in PATH!
    echo curl is built into Windows 10+. Please update or install curl.
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------
:: Create models directory if it doesn't exist
:: -----------------------------------------------------------------------
if not exist "models" (
    mkdir models
)

:: -----------------------------------------------------------------------
:: Download small.en (466MB)
:: -----------------------------------------------------------------------
echo =======================================================================
echo  [1/2] Downloading ggml-small.en (466MB)...
echo =======================================================================
if exist "models\ggml-small.en.bin" (
    echo [SKIP] ggml-small.en.bin already exists at models\
) else (
    echo Downloading from HuggingFace...
    curl -L --fail --progress-bar -o "models\ggml-small.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        color 0C
        echo [ERROR] Download failed for small.en
        echo Trying alternative URL...
        curl -L --fail --progress-bar -o "models\ggml-small.en.bin" "https://huggingface.co/distil-whisper/ggml/resolve/main/ggml-small.en.bin"
        if !ERRORLEVEL! NEQ 0 (
            echo [ERROR] Both download URLs failed for small.en
            pause
            exit /b 1
        )
    )
    echo [OK] ggml-small.en.bin downloaded
)
echo.

:: -----------------------------------------------------------------------
:: Download medium.en (1.5GB)
:: -----------------------------------------------------------------------
echo =======================================================================
echo  [2/2] Downloading ggml-medium.en (1.5GB)...
echo =======================================================================
echo This may take 5-10 minutes depending on your connection...
echo.
if exist "models\ggml-medium.en.bin" (
    echo [SKIP] ggml-medium.en.bin already exists at models\
) else (
    echo Downloading from HuggingFace...
    curl -L --fail --progress-bar -o "models\ggml-medium.en.bin" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin"
    if !ERRORLEVEL! NEQ 0 (
        color 0E
        echo [WARNING] Primary URL failed, trying alternative...
        curl -L --fail --progress-bar -o "models\ggml-medium.en.bin" "https://huggingface.co/distil-whisper/ggml/resolve/main/ggml-medium.en.bin"
        if !ERRORLEVEL! NEQ 0 (
            echo [ERROR] Both download URLs failed for medium.en
            echo You can still test with small.en — run start.bat now.
            echo.
            pause
            exit /b 0
        )
    )
    echo [OK] ggml-medium.en.bin downloaded
)
echo.

:: -----------------------------------------------------------------------
:: Verify both models exist
:: -----------------------------------------------------------------------
echo =======================================================================
echo  VERIFICATION
echo =======================================================================
set "ALL_OK=1"

if exist "models\ggml-small.en.bin" (
    for %%A in ("models\ggml-small.en.bin") do set "SIZE_S=%%~zA"
    set /a SIZE_S_MB=!SIZE_S! / 1048576
    echo [OK] ggml-small.en.bin found ^(!SIZE_S_MB! MB^)
) else (
    echo [FAIL] ggml-small.en.bin NOT found
    set "ALL_OK=0"
)

if exist "models\ggml-medium.en.bin" (
    for %%A in ("models\ggml-medium.en.bin") do set "SIZE_M=%%~zA"
    set /a SIZE_M_MB=!SIZE_M! / 1048576
    echo [OK] ggml-medium.en.bin found ^(!SIZE_M_MB! MB^)
) else (
    echo [INFO] ggml-medium.en.bin not found ^(optional — small.en is enough to test^)
)

echo.

if "!ALL_OK!"=="1" (
    echo =======================================================================
    echo  MODELS DOWNLOADED SUCCESSFULLY
    echo =======================================================================
    echo.
    echo Both models are ready at:
    echo   models\ggml-small.en.bin
    echo   models\ggml-medium.en.bin
    echo.
    echo The config has been pre-updated to use whisper_cpp with small.en.
    echo Run start.bat to test with small.en first.
    echo.
    echo To switch to medium.en, edit config.llama4.toml:
    echo   model = 'C:\Users\Parth\Desktop\whisper\models\ggml-medium.en.bin'
    echo.
    echo To roll back to Qwen3-ASR:
    echo   git checkout savepoint-qwen3-working -- config.llama4.toml whisper_flow\daemon.py
) else (
    echo =======================================================================
    echo  CHECK small.en — it may have failed
    echo =======================================================================
    echo If small.en failed, check your internet connection.
    echo You can also download manually from:
    echo   https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
    echo Save it to: models\ggml-small.en.bin
)

echo.
pause
