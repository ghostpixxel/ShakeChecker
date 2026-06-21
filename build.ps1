$ErrorActionPreference = "Stop"

Write-Host "Starting ShakeChecker Build..." -ForegroundColor Cyan

# Try to activate the virtual environment if it exists, otherwise create it
if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "No virtual environment found. Creating one..." -ForegroundColor Yellow
    python -m venv .venv
    
    if (-not $?) {
        Write-Error "Failed to create virtual environment. Ensure Python is installed and in your PATH."
        exit 1
    }
    
    Write-Host "Activating virtual environment..."
    . ".\.venv\Scripts\Activate.ps1"
    
    Write-Host "Installing build dependencies..." -ForegroundColor Yellow
    pip install -e ".[build]"
} else {
    Write-Host "Activating virtual environment..."
    . ".\.venv\Scripts\Activate.ps1"
}

# Check if a rebuild is necessary based on file timestamps
$exePath = "dist\ShakeChecker\ShakeChecker.exe"
if (Test-Path $exePath) {
    $exeTime = (Get-Item $exePath).LastWriteTime
    $sourcePaths = @("src", "assets", "calibration.toml", "pyproject.toml", "ShakeChecker.spec")
    
    $needsRebuild = $false
    foreach ($path in $sourcePaths) {
        if (Test-Path $path) {
            $files = Get-ChildItem -Path $path -Recurse -File | Where-Object { $_.Extension -ne '.pyc' -and $_.FullName -notmatch '__pycache__' }
            foreach ($file in $files) {
                if ($file.LastWriteTime -gt $exeTime) {
                    Write-Host "File changed: $($file.Name)" -ForegroundColor Cyan
                    $needsRebuild = $true
                    break
                }
            }
        }
        if ($needsRebuild) { break }
    }
    
    if (-not $needsRebuild) {
        Write-Host "No source files changed since last build. Skipping compilation (up to date)." -ForegroundColor Green
        Write-Host ""
        Write-Host "You can find the compiled application inside the 'dist/ShakeChecker/' folder." -ForegroundColor Yellow
        Write-Host ""
        exit 0
    }
}

# Run the build
Write-Host "Running PyInstaller..."
if (Test-Path ".\.venv\Scripts\pyinstaller.exe") {
    & ".\.venv\Scripts\pyinstaller.exe" --noconfirm ShakeChecker.spec
} else {
    pyinstaller --noconfirm ShakeChecker.spec
}

# Update the executable's timestamp so the incremental cache works correctly
if (Test-Path "dist\ShakeChecker\ShakeChecker.exe") {
    (Get-Item "dist\ShakeChecker\ShakeChecker.exe").LastWriteTime = Get-Date
}

Write-Host ""
Write-Host "Build finished successfully!" -ForegroundColor Green
Write-Host "You can find the compiled application inside the 'dist/ShakeChecker/' folder." -ForegroundColor Yellow
Write-Host ""
Pause
