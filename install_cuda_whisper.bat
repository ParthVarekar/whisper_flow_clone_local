@echo off
setlocal enabledelayedexpansion
title WhisperFlow CUDA Binary Installer
color 0A
cd /d "%~dp0"
cls
echo =======================================================================
echo           WHISPERFLOW CUDA BINARY INSTALLER
echo           Downloading GPU-accelerated whisper-cli.exe
echo =======================================================================
echo.
echo Your HP Omen has an NVIDIA RTX GPU. This script downloads a
echo CUDA-accelerated whisper-cli.exe that will be 10-20x faster than
echo the CPU-only version you currently have.
echo.
echo Expected speedup:
echo   CPU-only:  ASR ~20s for 20s audio
echo   CUDA GPU:  ASR ~1-2s for 20s audio
echo.

:: -----------------------------------------------------------------------
:: Check if curl is available
:: -----------------------------------------------------------------------
where curl >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] curl not found in PATH!
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------
:: Check if 7zip or tar is available for extraction
:: -----------------------------------------------------------------------
set "EXTRACTOR="
where 7z >nul 2>&1 && set "EXTRACTOR=7z"
where tar >nul 2>&1 && if not defined EXTRACTOR set "EXTRACTOR=tar"

if not defined EXTRACTOR (
    echo [INFO] No 7z or tar found. Will try PowerShell Expand-Archive.
    set "EXTRACTOR=powershell"
)

:: -----------------------------------------------------------------------
:: Create temp directory
:: -----------------------------------------------------------------------
set "TEMP_DIR=%TEMP%\whisper_cuda_install"
if exist "!TEMP_DIR!" rmdir /s /q "!TEMP_DIR!"
mkdir "!TEMP_DIR!"

:: -----------------------------------------------------------------------
:: Download CUDA 12.4 binary (whisper-cublas-12.4.0-bin-x64.zip)
:: This is the one compatible with recent NVIDIA drivers (RTX 30/40/50 series)
:: -----------------------------------------------------------------------
echo =======================================================================
echo  Downloading CUDA-accelerated whisper-cli.exe (266MB)...
echo =======================================================================
echo.

set "ZIP_PATH=!TEMP_DIR!\whisper-cublas-12.4.0-bin-x64.zip"
set "DOWNLOAD_URL=https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip"

echo Downloading from: !DOWNLOAD_URL!
echo This may take 2-5 minutes...
echo.

curl -L --fail --progress-bar -o "!ZIP_PATH!" "!DOWNLOAD_URL!"

if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [WARNING] CUDA 12.4 download failed. Trying CUDA 11.8 version...
    set "ZIP_PATH=!TEMP_DIR!\whisper-cublas-11.8.0-bin-x64.zip"
    set "DOWNLOAD_URL=https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-11.8.0-bin-x64.zip"
    curl -L --fail --progress-bar -o "!ZIP_PATH!" "!DOWNLOAD_URL!"

    if !ERRORLEVEL! NEQ 0 (
        color 0C
        echo [ERROR] Both CUDA downloads failed.
        echo Please check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo [OK] Download complete.
echo.

:: -----------------------------------------------------------------------
:: Extract the zip file
:: -----------------------------------------------------------------------
echo =======================================================================
echo  Extracting...
echo =======================================================================

set "EXTRACT_DIR=!TEMP_DIR!\extracted"
mkdir "!EXTRACT_DIR!"

if "!EXTRACTOR!"=="7z" (
    7z x "!ZIP_PATH!" -o"!EXTRACT_DIR!" -y >nul
) else if "!EXTRACTOR!"=="tar" (
    tar -xf "!ZIP_PATH!" -C "!EXTRACT_DIR!"
) else (
    powershell -Command "Expand-Archive -Path '!ZIP_PATH!' -DestinationPath '!EXTRACT_DIR!' -Force"
)

if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Extraction failed.
    pause
    exit /b 1
)

echo [OK] Extraction complete.
echo.

:: -----------------------------------------------------------------------
:: Find the whisper-cli.exe in the extracted files
:: -----------------------------------------------------------------------
set "WHISPER_EXE="
for /r "!EXTRACT_DIR!" %%f in (whisper-cli.exe) do (
    if not defined WHISPER_EXE set "WHISPER_EXE=%%f"
)

if not defined WHISPER_EXE (
    :: Try alternative name (older versions use whisper.exe)
    for /r "!EXTRACT_DIR!" %%f in (whisper.exe) do (
        if not defined WHISPER_EXE set "WHISPER_EXE=%%f"
    )
)

if not defined WHISPER_EXE (
    color 0C
    echo [ERROR] whisper-cli.exe not found in the extracted files.
    echo Contents of extraction directory:
    dir /s /b "!EXTRACT_DIR!"
    pause
    exit /b 1
)

echo [OK] Found: !WHISPER_EXE!
echo.

:: -----------------------------------------------------------------------
:: Backup the old CPU-only whisper-cli.exe and install the CUDA version
:: -----------------------------------------------------------------------
echo =======================================================================
echo  Installing CUDA binary...
echo =======================================================================

set "TARGET_DIR=%~dp0third_party\whisper.cpp-bin\whisper-bin-x64\Release"
set "OLD_EXE=!TARGET_DIR!\whisper-cli.exe"

:: Backup old version
if exist "!OLD_EXE!" (
    copy /Y "!OLD_EXE!" "!OLD_EXE!.cpu-backup" >nul
    echo [OK] Backed up old CPU version to: whisper-cli.exe.cpu-backup
)

:: Copy new CUDA version — use full absolute paths for both source and target
echo Copying: "!WHISPER_EXE!" → "!OLD_EXE!"
copy /Y "!WHISPER_EXE!" "!OLD_EXE!"

if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERROR] Failed to copy CUDA binary to target directory.
    echo Target: !OLD_EXE!
    echo Source: !WHISPER_EXE!
    echo.
    echo You can copy it manually:
    echo   copy "!WHISPER_EXE!" "!OLD_EXE!"
    pause
    exit /b 1
)

echo [OK] CUDA whisper-cli.exe installed.
echo.

:: -----------------------------------------------------------------------
:: Also copy any required DLLs from the extraction
:: -----------------------------------------------------------------------
echo Copying required DLLs...
for /r "!EXTRACT_DIR!" %%f in (*.dll) do (
    copy /Y "%%f" "!TARGET_DIR!\" >nul 2>&1
    echo   Copied: %%~nxf
)

:: -----------------------------------------------------------------------
:: Cleanup
:: -----------------------------------------------------------------------
rmdir /s /q "!TEMP_DIR!" 2>nul

:: -----------------------------------------------------------------------
:: Final verification
:: -----------------------------------------------------------------------
echo.
echo =======================================================================
echo  INSTALLATION COMPLETE
echo =======================================================================
echo.
echo CUDA-accelerated whisper-cli.exe has been installed.
echo.
echo Expected performance:
echo   small.en (244M):  ~1-2s ASR (was ~20s)
echo   medium.en (769M): ~3-5s ASR (was ~16s)
echo.
echo Now run start.bat to test with GPU acceleration:
echo   start.bat          ^(uses small.en — should be ~4s total^)
echo   start_medium.bat   ^(uses medium.en — should be ~6-8s total^)
echo.
echo To rollback to CPU version:
echo   copy "!OLD_EXE!.cpu-backup" "!OLD_EXE!"
echo.
pause
