param(
    [switch]$Local
)

$ErrorActionPreference = "Stop"

# Force UTF-8 console output
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

# ANSI colors
$ESC = [char]27
$C_LOGO  = "$ESC[97m$ESC[38;2;255;255;255m"  # Bright White (#ffffff)
$C_FILL  = "$ESC[94m$ESC[38;2;88;166;255m"   # Bright Blue (#58a6ff)
$C_EMPTY = "$ESC[90m$ESC[38;2;72;79;88m"     # Dark Gray (#484f58)
$C_PCT   = "$ESC[97m$ESC[38;2;255;255;255m"  # Bright White (#ffffff)
$C_RESET = "$ESC[0m"

# Unicode glyphs
$G_B  = [string][char]0x2588 # █
$G_TR = [string][char]0x2557 # ╗
$G_TL = [string][char]0x2554 # ╔
$G_H  = [string][char]0x2550 # ═
$G_BR = [string][char]0x255D # ╝
$G_V  = [string][char]0x2551 # ║
$G_BL = [string][char]0x255A # ╚
$G_F  = [string][char]0x2593 # ▓ (Filled)
$G_E  = [string][char]0x2591 # ░ (Empty)

$script:FirstRender = $true

function Render-Progress {
    param([int]$Percent)
    if ($Percent -gt 100) { $Percent = 100 }
    if ($Percent -lt 0) { $Percent = 0 }

    $totalBlocks = 16
    $filledCount = [math]::Floor(($Percent * $totalBlocks) / 100)
    $emptyCount  = $totalBlocks - $filledCount

    $barFilled = ($G_F * $filledCount)
    $barEmpty  = ($G_E * $emptyCount)

    $pctStr = "{0,3}%" -f $Percent
    $barLine = "${C_FILL}${barFilled}${C_EMPTY}${barEmpty}${C_RESET}  ${C_PCT}${pctStr}${C_RESET}"

    $l1 = " ${C_LOGO}" + ($G_B * 5) + $G_TR + "    " + ($G_B * 5) + $G_TR + "${C_RESET}"
    $l2 = "${C_LOGO}" + ($G_B * 2) + $G_TL + ($G_H * 3) + $G_BR + "   " + ($G_B * 2) + $G_TL + ($G_H * 3) + $G_BR + "${C_RESET}"
    $l3 = "${C_LOGO}" + ($G_B * 2) + $G_V + "       " + ($G_B * 2) + $G_V + "${C_RESET}        $barLine"
    $l4 = "${C_LOGO}" + $G_BL + ($G_B * 5) + $G_TR + "   " + $G_BL + ($G_B * 5) + $G_TR + "${C_RESET}"
    $l5 = " ${C_LOGO}" + $G_BL + ($G_H * 4) + $G_BR + "    " + $G_BL + ($G_H * 4) + $G_BR + "${C_RESET}"

    try {
        if ($Host.UI.RawUI) {
            if ($null -eq $script:StartPos) {
                Clear-Host
                $script:StartPos = $Host.UI.RawUI.CursorPosition
            } else {
                $Host.UI.RawUI.CursorPosition = $script:StartPos
            }
        }
    } catch {
        try { Clear-Host } catch {}
    }

    [Console]::WriteLine($l1)
    [Console]::WriteLine($l2)
    [Console]::WriteLine($l3)
    [Console]::WriteLine($l4)
    [Console]::WriteLine($l5)
}

# 1. Check Python
$pyCmd = $null
foreach ($cmd in @("python", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $parts = $ver.Split('.')
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $pyCmd = $cmd
                break
            }
        }
    } catch {}
}

if (-not $pyCmd) {
    Write-Host ""
    Write-Host "Error: Python 3.10 or newer is required." -ForegroundColor Red
    Write-Host "Install Python from https://www.python.org/downloads/ and check 'Add Python to PATH'." -ForegroundColor Yellow
    exit 1
}

Render-Progress -Percent 5

# Target directory
if ($Local) {
    $installDir = $PSScriptRoot
} else {
    $installDir = Join-Path $env:LOCALAPPDATA "cmdai-code"
    if (-not (Test-Path $installDir)) {
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    }

    Render-Progress -Percent 20

    # Download main branch ZIP
    $zipUrl = "https://github.com/Krzyzyk33/CMDAI-CODE/archive/refs/heads/main.zip"
    $zipFile = Join-Path $env:TEMP "cmdai-main.zip"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing

    Render-Progress -Percent 45

    # Extract ZIP
    $extractTemp = Join-Path $env:TEMP "cmdai-extract-$([System.Guid]::NewGuid().ToString('N'))"
    Expand-Archive -Path $zipFile -DestinationPath $extractTemp -Force
    Remove-Item -Path $zipFile -Force -ErrorAction SilentlyContinue

    $extractedFolder = Get-ChildItem -Path $extractTemp | Where-Object { $_.PSIsContainer } | Select-Object -First 1
    if ($extractedFolder) {
        Copy-Item -Path "$($extractedFolder.FullName)\*" -Destination $installDir -Recurse -Force
    }
    Remove-Item -Path $extractTemp -Recurse -Force -ErrorAction SilentlyContinue
}

Render-Progress -Percent 60

# 2. Virtual environment setup
$venvDir = Join-Path $installDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    & $pyCmd -m venv $venvDir 2>$null
}

if (-not (Test-Path $venvPython)) {
    $venvPython = $pyCmd
}

Render-Progress -Percent 75

# 3. Install dependencies
$reqFile = Join-Path $installDir "requirements.txt"
if (Test-Path $reqFile) {
    & $venvPython -m pip install -r $reqFile --quiet --disable-pip-version-check 2>$null
}

Render-Progress -Percent 88

# 4. Create directories & default config
foreach ($dir in @("models", "logs", "cache", "app\sessions")) {
    $targetPath = Join-Path $installDir $dir
    if (-not (Test-Path $targetPath)) {
        New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
    }
}

$cfgFile = Join-Path $installDir "config.json"
$cfgExample = Join-Path $installDir "config.example.json"
if (-not (Test-Path $cfgFile) -and (Test-Path $cfgExample)) {
    Copy-Item -Path $cfgExample -Destination $cfgFile -Force
}

# 5. Add to User PATH if remote install
if (-not $Local) {
    $binDir = Join-Path $installDir "bin"
    if (-not (Test-Path $binDir)) {
        New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    }
    $cmdaiCmd = Join-Path $binDir "cmdai.cmd"
    $cmdaiContent = "@echo off`r`n`"$installDir\cmdai.bat`" %*"
    [System.IO.File]::WriteAllText($cmdaiCmd, $cmdaiContent, [System.Text.Encoding]::ASCII)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$userPath", "User")
        $env:Path = "$binDir;$env:Path"
    }
}

Render-Progress -Percent 100
Start-Sleep -Milliseconds 350

# 6. Automatic start
$mainScript = Join-Path $installDir "cmdai.py"
if (-not $env:CMDAI_NO_START) {
    & $venvPython $mainScript code
}
