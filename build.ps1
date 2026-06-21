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

# Run the build
Write-Host "Running PyInstaller..."
if (Test-Path ".\.venv\Scripts\pyinstaller.exe") {
    & ".\.venv\Scripts\pyinstaller.exe" --noconfirm ShakeChecker.spec
} else {
    pyinstaller --noconfirm ShakeChecker.spec
}

Write-Host ""
Write-Host "Build finished successfully!" -ForegroundColor Green
Write-Host "You can find the compiled application inside the 'dist/ShakeChecker/' folder." -ForegroundColor Yellow
Write-Host ""
Pause
