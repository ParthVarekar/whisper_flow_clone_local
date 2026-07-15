# WhisperFlow Download Helper
# Handles all downloads and extractions reliably using PowerShell
param(
    [Parameter(Mandatory=$true)]
    [string]$Mode,
    
    [string]$Url = "",
    [string]$OutPath = "",
    [string]$OutDir = ""
)

$ProgressPreference = 'SilentlyContinue'

function Download-File {
    param([string]$Url, [string]$OutFile, [string]$Description)
    Write-Host "  Downloading: $Description"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
        Write-Host "  [OK] Downloaded: $Description"
        return $true
    } catch {
        Write-Host "  [WARNING] Download failed: $Description"
        return $false
    }
}

function Extract-Zip {
    param([string]$ZipPath, [string]$ExtractDir)
    try {
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
        return $true
    } catch {
        Write-Host "  [WARNING] Extraction failed"
        return $false
    }
}

function Find-And-Copy {
    param([string]$ExtractDir, [string]$Filter, [string]$DestDir, [string]$DestName)
    $found = Get-ChildItem -Path $ExtractDir -Recurse -Filter $Filter -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        Copy-Item $found.FullName -Destination (Join-Path $DestDir $DestName) -Force
        Write-Host "  [OK] Installed: $DestName"
        # Copy all DLLs from same directory
        $dllDir = $found.DirectoryName
        Get-ChildItem -Path $dllDir -Filter "*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName -Destination $DestDir -Force
        }
        return $true
    }
    Write-Host "  [WARNING] $Filter not found in archive"
    return $false
}

# ============================================================
# MODE: model — Download a single model file
# ============================================================
if ($Mode -eq "model") {
    Download-File -Url $Url -OutFile $OutPath -Description (Split-Path $OutPath -Leaf)
}

# ============================================================
# MODE: whisper — Download + extract whisper-cli.exe
# ============================================================
elseif ($Mode -eq "whisper") {
    $tempZip = Join-Path $env:TEMP "whisper_setup.zip"
    $tempExtract = Join-Path $env:TEMP "whisper_setup_extract"
    
    # Try CUDA 12.4 first
    $ok = Download-File -Url "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-12.4.0-bin-x64.zip" -OutFile $tempZip -Description "whisper-cli CUDA 12.4 (266MB)"
    
    if (-not $ok) {
        # Try CUDA 11.8
        $ok = Download-File -Url "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-cublas-11.8.0-bin-x64.zip" -OutFile $tempZip -Description "whisper-cli CUDA 11.8"
    }
    
    if (-not $ok) {
        # Try CPU-only
        $ok = Download-File -Url "https://github.com/ggerganov/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip" -OutFile $tempZip -Description "whisper-cli CPU"
    }
    
    if ($ok) {
        if (Extract-Zip -ZipPath $tempZip -ExtractDir $tempExtract) {
            Find-And-Copy -ExtractDir $tempExtract -Filter "whisper-cli.exe" -DestDir $OutDir -DestName "whisper-cli.exe"
        }
    }
    
    # Cleanup
    Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
}

# ============================================================
# MODE: llama — Download + extract llama-server.exe + CUDA DLLs
# ============================================================
elseif ($Mode -eq "llama") {
    $tempZip = Join-Path $env:TEMP "llama_setup.zip"
    $tempExtract = Join-Path $env:TEMP "llama_setup_extract"
    $llamaTag = "b10015"
    
    # Try CUDA 12.4 first
    $ok = Download-File -Url "https://github.com/ggml-org/llama.cpp/releases/download/$llamaTag/llama-$llamaTag-bin-win-cuda-12.4-x64.zip" -OutFile $tempZip -Description "llama-server CUDA 12.4"
    
    if (-not $ok) {
        # Try CPU-only
        $ok = Download-File -Url "https://github.com/ggml-org/llama.cpp/releases/download/$llamaTag/llama-$llamaTag-bin-win-cpu-x64.zip" -OutFile $tempZip -Description "llama-server CPU"
    }
    
    if ($ok) {
        if (Extract-Zip -ZipPath $tempZip -ExtractDir $tempExtract) {
            Find-And-Copy -ExtractDir $tempExtract -Filter "llama-server.exe" -DestDir $OutDir -DestName "llama-server.exe"
            Find-And-Copy -ExtractDir $tempExtract -Filter "llama-cli.exe" -DestDir $OutDir -DestName "llama-cli.exe"
        }
    }
    
    # Download CUDA runtime DLLs
    $cudartZip = Join-Path $env:TEMP "cudart_setup.zip"
    $cudartExtract = Join-Path $env:TEMP "cudart_setup_extract"
    $cudartOk = Download-File -Url "https://github.com/ggml-org/llama.cpp/releases/download/$llamaTag/cudart-llama-bin-win-cuda-12.4-x64.zip" -OutFile $cudartZip -Description "CUDA runtime DLLs"
    
    if ($cudartOk) {
        if (Extract-Zip -ZipPath $cudartZip -ExtractDir $cudartExtract) {
            Get-ChildItem -Path $cudartExtract -Recurse -Filter "*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
                Copy-Item $_.FullName -Destination $OutDir -Force
            }
            Write-Host "  [OK] CUDA runtime DLLs installed"
        }
    }
    
    # Cleanup
    Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $cudartZip -Force -ErrorAction SilentlyContinue
    Remove-Item $cudartExtract -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "  Done."
