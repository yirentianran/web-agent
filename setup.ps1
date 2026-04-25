# One-time project setup for Windows: install dependencies and prepare environment.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Web Agent Setup (Windows) ==="

# Step 1: Check Python version
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $rawVersion = (python --version 2>&1) -replace 'Python ', ''
    # Extract major.minor, ignore pre-release suffixes (rc, beta, etc.)
    $match = [regex]::Match($rawVersion, '^(\d+)\.(\d+)')
    if ($match.Success) {
        $major = [int]$match.Groups[1].Value
        $minor = [int]$match.Groups[2].Value
        if ($major -ge 3 -and $minor -ge 12) {
            Write-Host "OK Python $rawVersion"
        } else {
            Write-Host "FAIL Python 3.12+ required (found: $rawVersion)"
            exit 1
        }
    } else {
        Write-Host "FAIL Could not parse Python version: $rawVersion"
        exit 1
    }
} else {
    Write-Host "FAIL Python not found"
    exit 1
}

# Step 2: Install backend dependencies
$hasUv = $null -ne (Get-Command uv -ErrorAction SilentlyContinue)
if ($hasUv) {
    Write-Host "Installing backend dependencies (uv sync)..."
    uv sync
} elseif (Test-Path "uv.lock") {
    Write-Host "WARN uv.lock found but 'uv' is not installed. Falling back to pip."
    python -m venv .venv
    . ".venv\Scripts\Activate.ps1"
    pip install -e ".[dev]"
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
        Write-Host "  Global install failed, installing as dev dependency..."
        Push-Location
        npm install -D concurrently 2>$null
        Pop-Location
    }
} else {
    Write-Host "WARN npx not found. Install 'concurrently' manually for start-dev.ps1."
}

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and set your ANTHROPIC_API_KEY"
Write-Host "  2. Run .\start-dev.ps1 to start both servers"
Write-Host "  3. Open http://127.0.0.1:3000 in your browser"
Write-Host ""
Write-Host "Note: Use 127.0.0.1 (not localhost) on Windows to avoid IPv6 WebSocket issues"
