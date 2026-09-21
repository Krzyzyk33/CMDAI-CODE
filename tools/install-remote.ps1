<#
.SYNOPSIS
  Remote installer for CMDAI CODE. Clones (or updates) the app to a fixed
  location (~/CMDAI-CODE), installs dependencies and registers `cmdai`.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/tools/install-remote.ps1 | iex"
#>
param(
  [string]$Root = (Join-Path $env:USERPROFILE "CMDAI-CODE")
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/Krzyzyk33/CMDAI-CODE.git"

function Find-CommandSafe([string]$Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

# 1. Python 3.10+
$python = Find-CommandSafe "python"
if (-not $python) { throw "Python not found in PATH. Install Python 3.10+ from https://www.python.org/downloads/ (check 'Add Python to PATH')." }
$verOk = & $python -c "import sys; print('1' if sys.version_info >= (3, 10) else '0')"
if ($verOk.Trim() -ne "1") { throw "Python 3.10+ required. Detected: $(& $python --version)" }
Write-Host "[OK] Python: $(& $python --version)"

# 2. Git
$git = Find-CommandSafe "git"
if (-not $git) { throw "Git not found in PATH. Install it from https://git-scm.com/download/win" }

# 3. Clone or update fixed checkout
if (-not (Test-Path (Join-Path $Root "cmdai.py"))) {
  Write-Host "[*] Cloning $RepoUrl to $Root ..."
  New-Item -ItemType Directory -Force -Path (Split-Path $Root) | Out-Null
  & $git clone $RepoUrl $Root
  if ($LASTEXITCODE -ne 0) { throw "git clone failed." }
} else {
  Write-Host "[*] Updating existing checkout at $Root ..."
  & $git -C $Root pull --rebase --autostash
  if ($LASTEXITCODE -ne 0) { Write-Warning "git pull failed, continuing with local files." }
}

# 4. Dependencies (visible output, no --quiet hiding)
Write-Host "[*] Installing Python dependencies ..."
& $python -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed (see errors above)." }
Write-Host "[OK] Dependencies installed."

# 5. Config + directories
$cfg = Join-Path $Root "config.json"
if (-not (Test-Path $cfg)) {
  $example = Join-Path $Root "config.example.json"
  if (Test-Path $example) {
    Copy-Item $example $cfg
    Write-Host "[OK] Created config.json from template."
  }
}
@("models", "logs", "cache") | ForEach-Object {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root $_) | Out-Null
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root "app/sessions") | Out-Null

# 6. Global `cmdai` launcher (single source of truth: launcher.py)
Write-Host "[*] Registering global 'cmdai' command ..."
& $python (Join-Path $Root "cmdai.py") --install-launcher

Write-Host ""
Write-Host "SUCCESS: CMDAI CODE installed at $Root"
Write-Host "Open a NEW terminal and run:  cmdai code"
