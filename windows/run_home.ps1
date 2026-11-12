# Збирає OLX і rieltor.ua з цього комп'ютера і відправляє результат на GitHub.
# Запускається Планувальником завдань щодня (див. setup.ps1). Вручну:
#   powershell -ExecutionPolicy Bypass -File windows\run_home.ps1

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Log  = Join-Path $PSScriptRoot "home.log"
Set-Location $Repo
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:GIT_TERMINAL_PROMPT = "0"   # git не чекатиме мовчки на введення пароля

function Write-Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm}  {1}" -f (Get-Date), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

# журнал не росте безкінечно: лишаємо останні 2000 рядків
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 1MB)) {
    Get-Content $Log -Tail 2000 | Set-Content $Log -Encoding UTF8
}

Write-Log "Старт. Оновлюю дані з GitHub…"
git pull --rebase --autostash -q 2>&1 | ForEach-Object { Write-Log "git: $_" }
Write-Log "Збираю OLX і rieltor.ua — це 5–10 хвилин, прогрес нижче"

$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $py -u scraper/scrape.py --home 2>&1 | ForEach-Object {
    $line = "$_"
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}
if ($LASTEXITCODE -ne 0) {
    Write-Log "ПОМИЛКА: збирач завершився з кодом $LASTEXITCODE"
    exit 1
}

git add docs/data/listings-home.json docs/data/geocache-home.json notify/home-new.md 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Log "Змін немає"
    exit 0
}
Write-Log "Відправляю результат на GitHub…"
git commit -q -m ("Домашній ПК: OLX і rieltor {0:yyyy-MM-dd}" -f (Get-Date))
for ($i = 1; $i -le 3; $i++) {
    git pull --rebase -q 2>&1 | ForEach-Object { Write-Log "git: $_" }
    git push -q 2>&1 | ForEach-Object { Write-Log "git: $_" }
    if ($LASTEXITCODE -eq 0) { Write-Log "Відправлено на GitHub"; exit 0 }
    Start-Sleep -Seconds 20
}
Write-Log "ПОМИЛКА: не вдалося відправити на GitHub"
exit 1
