# Gold Bot - Setup VPS Windows
# Esegui come Administrator: powershell -ExecutionPolicy Bypass -File setup-vps.ps1

$GITHUB_REPO = "https://github.com/LucaLombardo03/gold-bot.git"
$INSTALL_DIR = "C:\GoldBot"
$PYTHON_URL  = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
$GIT_URL     = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
$MT5_URL     = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

function Log-Step { param($m); Write-Host "`n==> $m" -ForegroundColor Cyan }
function Log-OK   { param($m); Write-Host "    [OK] $m" -ForegroundColor Green }
function Log-Warn { param($m); Write-Host "    [!] $m"  -ForegroundColor Yellow }

# 1. Check Administrator
Log-Step "Check Administrator"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "Esegui come Administrator." -ForegroundColor Red; exit 1 }
Log-OK "OK"

# 2. Installa Git
Log-Step "Git"
$gitPath = "C:\Program Files\Git\cmd\git.exe"
if (-not (Test-Path $gitPath)) {
    Log-Warn "Download Git..."
    $tmp = "$env:TEMP\git-setup.exe"
    (New-Object Net.WebClient).DownloadFile($GIT_URL, $tmp)
    Start-Process $tmp -ArgumentList "/VERYSILENT /NORESTART" -Wait
    $env:PATH = $env:PATH + ";C:\Program Files\Git\cmd"
    Log-OK "Git installato"
} else {
    Log-OK "Git gia presente"
}
$git = "C:\Program Files\Git\cmd\git.exe"

# 3. Installa Python 3.12
Log-Step "Python 3.12"
$pyDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
$pyExe = "$pyDir\python.exe"
if (-not (Test-Path $pyExe)) {
    Log-Warn "Download Python..."
    $tmp = "$env:TEMP\python-setup.exe"
    (New-Object Net.WebClient).DownloadFile($PYTHON_URL, $tmp)
    Start-Process $tmp -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1" -Wait
    $env:PATH = $env:PATH + ";$pyDir;$pyDir\Scripts"
    Log-OK "Python installato"
} else {
    Log-OK "Python gia presente"
}

# 4. Clona o aggiorna il repository
Log-Step "Repository in $INSTALL_DIR"
if (Test-Path "$INSTALL_DIR\.git") {
    Log-Warn "Aggiorno repo esistente..."
    & $git -C $INSTALL_DIR pull
} else {
    & $git clone $GITHUB_REPO $INSTALL_DIR
    Log-OK "Clonato"
}

# 5. Copia config.json se presente accanto allo script
Log-Step "config.json"
$configDest = "$INSTALL_DIR\config.json"
$configSrc  = "$PSScriptRoot\config.json"
if (-not (Test-Path $configDest)) {
    if (Test-Path $configSrc) {
        Copy-Item $configSrc $configDest
        Log-OK "Copiato da script locale"
    } else {
        Log-Warn "ATTENZIONE: copia config.json manualmente in $INSTALL_DIR"
    }
} else {
    Log-OK "Gia presente - non sovrascritto"
}

# 6. Installa dipendenze Python
Log-Step "Dipendenze pip"
& $pyExe -m pip install --upgrade pip -q
& $pyExe -m pip install -r "$INSTALL_DIR\requirements.txt" -q
Log-OK "Dipendenze installate"

# 7. Download MT5
Log-Step "MetaTrader 5"
$mt5Exe = "C:\Program Files\MetaTrader 5\terminal64.exe"
if (-not (Test-Path $mt5Exe)) {
    Log-Warn "Download MT5..."
    $tmp = "$env:TEMP\mt5setup.exe"
    (New-Object Net.WebClient).DownloadFile($MT5_URL, $tmp)
    Write-Host ""
    Write-Host "  Completa l'installazione di MT5 nel wizard che si apre," -ForegroundColor Yellow
    Write-Host "  poi accedi al tuo account demo ICMarketsEU-Demo." -ForegroundColor Yellow
    Write-Host ""
    Start-Process $tmp -Wait
    Log-OK "MT5 installato"
} else {
    Log-OK "MT5 gia installato"
}

# 8. Task Scheduler - MT5 (avvio a 30s dal boot)
Log-Step "Task Scheduler: MetaTrader5"
if (Get-ScheduledTask -TaskName "MetaTrader5" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "MetaTrader5" -Confirm:$false
}
if (Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe") {
    $a = New-ScheduledTaskAction -Execute "C:\Program Files\MetaTrader 5\terminal64.exe"
    $t = New-ScheduledTaskTrigger -AtStartup
    $t.Delay = "PT30S"
    $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName "MetaTrader5" -Action $a -Trigger $t -Settings $s -Principal $p -Description "Avvia MT5 al boot" | Out-Null
    Log-OK "Task MT5 creato (delay 30s)"
} else {
    Log-Warn "MT5 non trovato - task non creato. Riesegui script dopo installazione MT5."
}

# 9. Task Scheduler - GoldBot (avvio a 60s dal boot)
Log-Step "Task Scheduler: GoldBot"
if (Get-ScheduledTask -TaskName "GoldBot" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "GoldBot" -Confirm:$false
}
$pythonw = "$pyDir\pythonw.exe"
$a = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$INSTALL_DIR\bot.py`"" -WorkingDirectory $INSTALL_DIR
$t = New-ScheduledTaskTrigger -AtStartup
$t.Delay = "PT60S"
$s = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "GoldBot" -Action $a -Trigger $t -Settings $s -Principal $p -Description "Avvia Gold Bot al boot" | Out-Null
Log-OK "Task GoldBot creato (delay 60s)"

# 10. Riepilogo
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Setup completato!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Installato in : $INSTALL_DIR"
Write-Host "  Log bot       : $INSTALL_DIR\bot.log"
Write-Host ""
Write-Host "  PROSSIMI STEP:" -ForegroundColor Yellow
Write-Host "  1. Copia config.json in $INSTALL_DIR (se non gia fatto)"
Write-Host "  2. Apri MT5 e accedi all'account demo"
Write-Host "  3. In MT5: Tools > Options > Expert Advisors > Allow automated trading"
Write-Host "  4. Test manuale: cd $INSTALL_DIR && python bot.py"
Write-Host "  5. Riavvia la VM per testare l'avvio automatico"
Write-Host ""
