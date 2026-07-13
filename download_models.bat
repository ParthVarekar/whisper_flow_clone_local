@echo off
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
:: Check if whisper.cpp download script exists
:: -----------------------------------------------------------------------
set "WHISPER_DIR=third_party\whisper.cpp"
if not exist "%WHISPER_DIR%\models\download-ggml-model.sh" (
    echo [ERROR] whisper.cpp not found at: %WHISPER_DIR%
    echo Please ensure whisper.cpp is cloned at third_party\whisper.cpp
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
:: Download small.en
:: -----------------------------------------------------------------------
echo =======================================================================
echo  [1/2] Downloading ggml-small.en (466MB)...
echo =======================================================================
if exist "models\ggml-small.en.bin" (
    echo [SKIP] ggml-small.en.bin already exists at models\
) else (
    cd /d "%WHISPER_DIR%"
    bash models\download-ggml-model.sh small.en
    if exist "models\ggml-small.en.bin" (
        move /Y "models\ggml-small.en.bin" "..\..\models\ggml-small.en.bin" >nul
        echo [OK] ggml-small.en.bin moved to models\
    ) else (
        echo [ERROR] Download failed for small.en
        cd /d "%~dp0"
        pause
        exit /b 1
    )
    cd /d "%~dp0"
)
echo.

:: -----------------------------------------------------------------------
:: Download medium.en
:: -----------------------------------------------------------------------
echo =======================================================================
echo  [2/2] Downloading ggml-medium.en (1.5GB)...
echo =======================================================================
if exist "models\ggml-medium.en.bin" (
    echo [SKIP] ggml-medium.en.bin already exists at models\
) else (
    cd /d "%WHISPER_DIR%"
    echo This may take 5-10 minutes depending on your connection...
    bash models\download-ggml-model.sh medium.en
    if exist "models\ggml-medium.en.bin" (
        move /Y "models\ggml-medium.en.bin" "..\..\models\ggml-medium.en.bin" >nul
        echo [OK] ggml-medium.en.bin moved to models\
    ) else (
        echo [ERROR] Download failed for medium.en
        cd /d "%~dp0"
        pause
        exit /b 1
    )
    cd /d "%~dp0"
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
    echo [OK] ggml-small.en.bin found (!SIZE_S_MB! MB)
) else (
    echo [FAIL] ggml-small.en.bin NOT found
    set "ALL_OK=0"
)

if exist "models\ggml-medium.en.bin" (
    for %%A in ("models\ggml-medium.en.bin") do set "SIZE_M=%%~zA"
    set /a SIZE_M_MB=!SIZE_M! / 1048576
    echo [OK] ggml-medium.en.bin found (!SIZE_M_MB! MB)
) else (
    echo [FAIL] ggml-medium.en.bin NOT found
    set "ALL_OK=0"
)

echo.

if "%ALL_OK%"=="1" (
    echo =======================================================================
    echo  ✅ MODELS DOWNLOADED SUCCESSFULLY
    echo =======================================================================
    echo.
    echo Both models are ready at:
    echo   models\ggml-small.en.bin
    echo   models\ggml-medium.en.bin
    echo.
    echo PINGING THE AGENT...
    echo.
    echo The config has been pre-updated to use whisper_cpp with small.en.
    echo Run start.bat to test with small.en first.
    echo.
    echo To switch to medium.en, edit config.llama4.toml:
    echo   model = 'C:\\Users\\Parth\\Desktop\\whisper\\models\\ggml-medium.en.bin'
    echo.
    echo To roll back to Qwen3-ASR:
    echo   git checkout savepoint-qwen3-working -- config.llama4.toml whisper_flow\\daemon.py
) else (
    echo =======================================================================
    echo  ❌ SOME MODELS FAILED TO DOWNLOAD
    echo =======================================================================
    echo Check your internet connection and try again.
)

echo.
pause
