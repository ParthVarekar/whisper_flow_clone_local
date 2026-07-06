@echo off
title WhisperFlow - Indistinguishable Voice Assistant Launcher
color 0A
cls
echo =======================================================================
echo               WHISPER FLOW - ONE-CLICK LAUNCHER
echo =======================================================================
echo.

:: 1. Check if python is available
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo [ERROR] Python was not found in PATH!
    echo Please install Python and add it to your System PATH.
    pause
    exit /b 1
)

:: 2. Check if llama-server is listening on port 8081
powershell -Command "$s = New-Object System.Net.Sockets.TcpClient; try { $s.Connect('127.0.0.1', 8081); exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Local llama-server is already running on port 8081.
) else (
    echo [STARTING] Launching local llama-server on port 8081...
    if exist "D:\llama4\llama-server.exe" (
        start "llama-server" /min "D:\llama4\llama-server.exe" -m "D:\llama4\qwen2.5-coder-7b.gguf" --host 127.0.0.1 --port 8081 -c 2048 -ngl 99
        echo [OK] llama-server process started in background window.
        timeout /t 3 /nobreak >nul
    ) else (
        echo [WARNING] D:\llama4\llama-server.exe not found. Proceeding with WhisperFlow...
    )
)

echo.
echo =======================================================================
echo   Starting WhisperFlow Daemon in Auto (Mind Reader) Mode...
echo   Hold Ctrl+Shift+Space to Dictate!
echo =======================================================================
echo.

cd /d "%~dp0"
python -m whisper_flow daemon --config config.llama4.toml

pause
