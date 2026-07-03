# WhisperFlow PowerShell One-Click Launcher
Set-Location $PSScriptRoot

Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host "               WHISPER FLOW - ONE-CLICK LAUNCHER" -ForegroundColor Cyan
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host ""

# Check port 8081 for llama-server
$client = New-Object System.Net.Sockets.TcpClient
$running = $false
try {
    $client.Connect('127.0.0.1', 8081)
    $running = $true
    $client.Close()
} catch {
    $running = $false
}

if ($running) {
    Write-Host "[OK] Local llama-server is already running on port 8081." -ForegroundColor Green
} else {
    Write-Host "[STARTING] Launching local llama-server on port 8081..." -ForegroundColor Yellow
    if (Test-Path "D:\llama4\llama-server.exe") {
        Start-Process "D:\llama4\llama-server.exe" -ArgumentList "-hf unsloth/gemma-4-E4B-it-GGUF:UD-Q4_K_XL --host 127.0.0.1 --port 8081 --ctx-size 32768 --n-gpu-layers 999 --parallel 2 --alias gemma-4-e4b-it --reasoning off --reasoning-budget 0" -WindowStyle Minimized
        Write-Host "[OK] llama-server process started in background window." -ForegroundColor Green
        Start-Sleep -Seconds 3
    } else {
        Write-Host "[WARNING] D:\llama4\llama-server.exe not found." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host "  Starting WhisperFlow Daemon in Auto (Mind Reader) Mode..." -ForegroundColor Green
Write-Host "  Hold Ctrl+Shift+Space to Dictate!" -ForegroundColor Yellow
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host ""

python -m whisper_flow daemon --config config.llama4.toml
