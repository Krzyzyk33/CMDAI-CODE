# CMDAI CODE Setup & PATH Configuration Script
$targetPath = $PSScriptRoot

Write-Host "Instalowanie zaleznosci z requirements.txt..." -ForegroundColor Cyan
python -m pip install -r "$targetPath\requirements.txt"

Write-Host "Instalowanie pakietu cmdai-code..." -ForegroundColor Cyan
python -m pip install -e "$targetPath"

# Dodawanie katalogu projektu do PATH uzytkownika
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$targetPath*") {
    $newPath = if ($userPath.EndsWith(';')) { "$userPath$targetPath" } else { "$userPath;$targetPath" }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    $env:Path = "$env:Path;$targetPath"
    Write-Host "Sukces: Dodano '$targetPath' do zmiennej PATH uzytkownika!" -ForegroundColor Green
} else {
    Write-Host "Info: '$targetPath' znajduje sie juz w zmiennej PATH." -ForegroundColor Yellow
}

Write-Host "`nAll set! Mozesz teraz uruchomic aplikacje wpisujac:" -ForegroundColor Green
Write-Host "  cmdai-code" -ForegroundColor Cyan
