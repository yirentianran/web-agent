# One-time project setup for Windows: install dependencies and prepare environment.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Web Agent Setup (Windows) ==="

# Step 1: Check Python version
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $version = (python --version 2>&1) -replace 'Python ', ''
    $major, $minor = $version -split '\.' | ForEach-Object { [int]$_ }
    if ($major -ge 3 -and $minor -ge 12) {
        Write-Host "OK Python $version"
    } else {
        Write-Host "FAIL Python 3.12+ required (found: $version)"
        exit 1
    }
} else {
    Write-Host "FAIL Python not found"
    exit 1
}

# Step 2: Install backend dependencies
if ((Test-Path "uv.lock") -or (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing backend dependencies (uv sync)..."
    uv sync
} else {
    Write-Host "Installing backend dependencies (pip)..."
    python -m venv .venv
    . ".venv\Scripts\Activate.ps1"
    pip install -e ".[dev]"
}
Write-Host "OK Backend dependencies installed"

# Step 3: Install frontend dependencies
Write-Host "Installing frontend dependencies..."
Push-Location frontend
npm install
Pop-Location
Write-Host "OK Frontend dependencies installed"

# Step 4: Setup environment file
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "WARN Created .env from .env.example"
    Write-Host "     Please edit .env and set your ANTHROPIC_API_KEY before starting"
}

# Step 5: Install concurrently for start-dev.ps1
if (Get-Command npx -ErrorAction SilentlyContinue) {
    Write-Host "Installing concurrently (for start-dev.ps1)..."
    npm install -g concurrently 2>$null
    if ($LASTEXITCODE -ne 0) {
        Push-Location
        npm install -D concurrently
        Pop-Location
    }
}

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and set your ANTHROPIC_API_KEY"
Write-Host "  2. Run .\start-dev.ps1 to start both servers"
Write-Host "  3. Open http://localhost:3000 in your browser"
