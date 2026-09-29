<#
.SYNOPSIS
  Remote installer for CMDAI EDITOR. Clones (or updates) the repository to a
  fixed location (~/CMDAI-CODE) and installs the editor's dependencies.

.DESCRIPTION
  The editor lives inside the CMDAI-CODE repository but is a separate
  application: it installs `editor\requirements-editor.txt` (15 entries) and
  NOT the application's `requirements.txt`. It does not create config.json -
  that belongs to the CMDAI application - and it does not register a global
  `cmdai` command, because the editor is a terminal UI you launch, not a CLI.

  The editor imports three modules from the application
  (`cmdai.core.checkpoints`, `cmdai.tui.modals.git_modal`,
  `cmdai.tui.modals.checkpoints`), so the whole repository is cloned - you
  cannot install the editor alone from this script.

.EXAMPLE
  One-liner (from any directory):
    powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/editor/tools/install-remote.ps1 | iex"

.EXAMPLE
  To a different location:
    powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/editor/tools/install-remote.ps1 | iex" -Root D:\Apps\CMDAI
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

# 3. Clone or update fixed checkout.
#    Marker is `editor\main.py`, not `cmdai.py` - this script installs the
#    editor, so a checkout that exists but has no editor (or a stale one from
#    before it was tracked in git) must be re-cloned, not merely updated.
$marker = Join-Path $Root "editor\main.py"
if (-not (Test-Path $marker)) {
  if (Test-Path $Root) {
    Write-Warning "$Root exists but has no editor\main.py - removing and re-cloning."
    Remove-Item -Recurse -Force $Root
  }
  Write-Host "[*] Cloning $RepoUrl to $Root ..."
  New-Item -ItemType Directory -Force -Path (Split-Path $Root) | Out-Null
  & $git clone $RepoUrl $Root
  if ($LASTEXITCODE -ne 0) { throw "git clone failed." }
} else {
  Write-Host "[*] Updating existing checkout at $Root ..."
  & $git -C $Root pull --rebase --autostash
  if ($LASTEXITCODE -ne 0) { Write-Warning "git pull failed, continuing with local files." }
}

# 4. Dependencies - editor's own file, NOT the application's
$req = Join-Path $Root "editor\requirements-editor.txt"
if (-not (Test-Path $req)) {
  throw "editor\requirements-editor.txt not found at $req. The editor was tracked in git on 2026-09-28; update the checkout."
}
Write-Host "[*] Installing editor dependencies (editor\requirements-editor.txt) ..."
& $python -m pip install -r $req
if ($LASTEXITCODE -ne 0) { throw "pip install failed (see errors above)." }
Write-Host "[OK] Dependencies installed."

# 5. Editor workspace. `.cmdai_code_project` holds checkpoints and is in
#    editor\.gitignore - it is runtime state, never committed.
$ws = Join-Path $Root "editor\.cmdai_code_project"
New-Item -ItemType Directory -Force -Path (Join-Path $ws "checkpoints") | Out-Null
Write-Host "[OK] Workspace prepared."

# 6. Optional components, reported but never fatal
$ruff = Find-CommandSafe "ruff"
if (-not $ruff) {
  Write-Warning "ruff missing - PROBLEMS will show syntax errors only. Install: pip install ruff"
} else {
  Write-Host "[OK] ruff: Python F rules will work."
}

Write-Host ""
Write-Host "SUCCESS: CMDAI EDITOR installed in $Root"
Write-Host "Run:      $Root\editor\run.bat"
Write-Host "Or:       cd $Root\editor ; python main.py"
Write-Host ""
Write-Host "NOTE: the editor is a SEPARATE app, but it imports 3 modules from the app"
Write-Host "(checkpoints, Git window). The whole repo is cloned."
