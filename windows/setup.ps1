# Одноразове налаштування збору OLX і rieltor.ua на Windows.
# Запуск у PowerShell з папки репозиторію:
#   powershell -ExecutionPolicy Bypass -File windows\setup.ps1              # щодня о 09:00
#   powershell -ExecutionPolicy Bypass -File windows\setup.ps1 -Time 20:30  # інший час

param([string]$Time = "09:00")

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

function Step($n, $msg) { Write-Host ""; Write-Host "[$n/5] $msg" -ForegroundColor Cyan }

Step 1 "Перевіряю Git і Python"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Не знайдено Git. Встановіть: winget install --id Git.Git -e   (або https://git-scm.com), потім відкрийте PowerShell заново і запустіть setup ще раз." -ForegroundColor Red
    exit 1
}
$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { $null }
if ($py -eq "python") {
    # «python» у Windows може бути заглушкою Microsoft Store
    $v = & python --version 2>&1
    if ("$v" -notmatch "Python 3") { $py = $null }
}
if (-not $py) {
    Write-Host "Не знайдено Python 3. Встановіть: winget install --id Python.Python.3.12 -e   (або https://python.org, з галочкою «Add python.exe to PATH»), потім відкрийте PowerShell заново." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $Repo ".git"))) {
    Write-Host "Ця папка не є клоном репозиторію. Виконайте:  git clone https://github.com/Gaspode3/FlatSearcher.git  і запустіть setup.ps1 з тієї папки." -ForegroundColor Red
    exit 1
}
Write-Host ("Git: {0}" -f (git --version))
Write-Host ("Python: {0}" -f (& $py --version))

Step 2 "Встановлюю бібліотеки Python"
& $py -m pip install --user -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "pip не зміг встановити бібліотеки" -ForegroundColor Red; exit 1 }

Step 3 "Налаштовую Git для цього репозиторію"
if (-not (git config user.name))  { git config user.name "FlatSearcher (домашній ПК)" }
if (-not (git config user.email)) { git config user.email "flatsearcher-home@users.noreply.github.com" }
git config pull.rebase true

Step 4 "Створюю завдання в Планувальнику (щодня о $Time)"
$runner = Join-Path $PSScriptRoot "run_home.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
Register-ScheduledTask -TaskName "FlatSearcher" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Щоденний збір квартир з OLX і rieltor.ua для сайту FlatSearcher" -Force | Out-Null
Write-Host "Готово: завдання «FlatSearcher» у Планувальнику завдань."

Step 5 "Пробний запуск — кілька хвилин. Якщо Git попросить увійти в GitHub, увійдіть (це один раз)."
powershell -NoProfile -ExecutionPolicy Bypass -File $runner
Write-Host ""
if ($LASTEXITCODE -eq 0) {
    Write-Host "Усе працює. Збір запускатиметься щодня о $Time, а якщо комп'ютер був вимкнений — одразу після ввімкнення." -ForegroundColor Green
} else {
    Write-Host "Пробний запуск завершився з помилкою — надішліть файл windows\home.log." -ForegroundColor Yellow
}
Write-Host "Журнал: windows\home.log"
Write-Host "Запустити вручну: Start-ScheduledTask -TaskName FlatSearcher"
Write-Host "Видалити завдання: Unregister-ScheduledTask -TaskName FlatSearcher"
