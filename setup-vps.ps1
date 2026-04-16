# ============================================================
# Gold Bot — Setup automatico VPS Windows (AWS EC2 t2.micro)
# Esegui come Administrator in PowerShell
# ============================================================

$ErrorActionPreference = "Stop"

$GITHUB_REPO = "https://github.com/LucaLombardo03/gold-bot.git"
$INSTALL_DIR = "C:\GoldBot"
$PYTHON_URL  = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
$PYTHON_EXE  = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$GIT_URL     = "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe"
$MT5_URL     = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "    [!]  $msg" -ForegroundColor Yellow
}

# ─── 1. Verifica privilegi Administrator ───────────────────
Write-Step "Verifica privilegi Administrator"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Esegui lo script come Administrator." -ForegroundColor Red
    exit 1
}
Write-OK "Administrator confermato"

# ─── 2. Installa Git ───────────────────────────────────────
Write-Step "Verifica/installa Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "Git non trovato — download in corso..."
    $gitInstaller = "$env:TEMP\git-setup.exe"
    Invoke-WebRequest -Uri $GIT_URL -OutFile $gitInstaller -UseBasicParsing
    Start-Process -FilePath $gitInstaller -ArgumentList "/VERYSILENT /NORESTART" -Wait
    # Aggiorna PATH nella sessione corrente
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Write-OK "Git installato"
} else {
    Write-OK "Git già presente: $(git --version)"
}

# ─── 3. Installa Python 3.12 ───────────────────────────────
Write-Step "Verifica/installa Python 3.12"
if (-not (Test-Path $PYTHON_EXE)) {
    Write-Warn "Python non trovato — download in corso..."
    $pyInstaller = "$env:TEMP\python-setup.exe"
    Invoke-WebRequest -Uri $PYTHON_URL -OutFile $pyInstaller -UseBasicParsing
    Start-Process -FilePath $pyInstaller `
        -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1" `
        -Wait
    # Aggiorna PATH nella sessione corrente
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Write-OK "Python installato"
} else {
    Write-OK "Python già presente: $($PYTHON_EXE)"
}

# ─── 4. Clona il repository ────────────────────────────────
Write-Step "Clona repository GitHub in $INSTALL_DIR"
if (Test-Path $INSTALL_DIR) {
    Write-Warn "$INSTALL_DIR esiste già — pull aggiornamenti"
    Set-Location $INSTALL_DIR
    git pull
} else {
    git clone $GITHUB_REPO $INSTALL_DIR
    Write-OK "Repository clonato"
}

# ─── 5. Copia config.json ──────────────────────────────────
Write-Step "Configurazione config.json"
$configDest = "$INSTALL_DIR\config.json"
if (-not (Test-Path $configDest)) {
    $configSrc = "$PSScriptRoot\config.json"
    if (Test-Path $configSrc) {
        Copy-Item $configSrc $configDest
        Write-OK "config.json copiato da script locale"
    } else {
        Write-Warn "config.json non trovato accanto allo script."
        Write-Warn "Copia manualmente config.json in $INSTALL_DIR prima di avviare il bot."
    }
} else {
    Write-OK "config.json già presente — non sovrascritto"
}

# ─── 6. Installa dipendenze Python ─────────────────────────
Write-Step "Installa dipendenze Python (requirements.txt)"
& $PYTHON_EXE -m pip install --upgrade pip --quiet
& $PYTHON_EXE -m pip install -r "$INSTALL_DIR\requirements.txt" --quiet
Write-OK "Dipendenze installate"

# ─── 7. Scarica e installa MetaTrader 5 ────────────────────
Write-Step "Download MetaTrader 5"
$mt5Installer = "$env:TEMP\mt5setup.exe"
if (-not (Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe")) {
    Write-Warn "MT5 non trovato — download in corso..."
    Invoke-WebRequest -Uri $MT5_URL -OutFile $mt5Installer -UseBasicParsing
    Write-Host ""
    Write-Host "  IMPORTANTE: MT5 richiede installazione manuale con GUI." -ForegroundColor Yellow
    Write-Host "  Si aprirà il wizard di installazione — completa l'installazione," -ForegroundColor Yellow
    Write-Host "  poi accedi con il tuo account ICMarketsEU-Demo." -ForegroundColor Yellow
    Write-Host ""
    Start-Process -FilePath $mt5Installer -Wait
    Write-OK "MT5 installato (verifica il login manualmente)"
} else {
    Write-OK "MT5 già installato"
}

# ─── 8. Configura Task Scheduler ───────────────────────────
Write-Step "Configura Task Scheduler — avvio automatico bot.py"

$taskName   = "GoldBot"
$botScript  = "$INSTALL_DIR\bot.py"
$logFile    = "$INSTALL_DIR\scheduler.log"

# Rimuovi task precedente se esiste
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Warn "Task precedente rimosso"
}

# Azione: avvia bot.py con pythonw (nessuna finestra console)
$action = New-ScheduledTaskAction `
    -Execute "pythonw.exe" `
    -Argument "`"$botScript`"" `
    -WorkingDirectory $INSTALL_DIR

# Trigger: all'avvio del sistema + ritardo 60s (MT5 ha tempo di avviarsi)
$triggerBoot = New-ScheduledTaskTrigger -AtStartup
$triggerBoot.Delay = "PT60S"   # 60 secondi di delay

# Impostazioni: riavvia il task se si blocca, esegui anche se non loggato
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

# Principal: utente corrente con privilegio più alto disponibile
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $triggerBoot `
    -Settings $settings `
    -Principal $principal `
    -Description "Avvia automaticamente Gold Bot XAUUSD all'avvio della VM" | Out-Null

Write-OK "Task '$taskName' registrato in Task Scheduler"

# ─── 9. Configura Task Scheduler per MT5 ───────────────────
Write-Step "Configura Task Scheduler — avvio automatico MetaTrader 5"

$mt5TaskName = "MetaTrader5"
$mt5Exe      = "C:\Program Files\MetaTrader 5\terminal64.exe"

if (Test-Path $mt5Exe) {
    if (Get-ScheduledTask -TaskName $mt5TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $mt5TaskName -Confirm:$false
    }

    $mt5Action = New-ScheduledTaskAction -Execute $mt5Exe
    $mt5Trigger = New-ScheduledTaskTrigger -AtStartup
    $mt5Trigger.Delay = "PT30S"   # 30s delay (prima di GoldBot)

    $mt5Settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable

    $mt5Principal = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME `
        -LogonType Interactive `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $mt5TaskName `
        -Action $mt5Action `
        -Trigger $mt5Trigger `
        -Settings $mt5Settings `
        -Principal $mt5Principal `
        -Description "Avvia MetaTrader 5 all'avvio (necessario per Gold Bot)" | Out-Null

    Write-OK "Task 'MetaTrader5' registrato (avvio 30s prima di GoldBot)"
} else {
    Write-Warn "MT5 non trovato in percorso standard — task MT5 non creato."
    Write-Warn "Installa MT5 e riesegui lo script, oppure crea il task manualmente."
}

# ─── 10. Riepilogo finale ──────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Setup completato!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Bot installato in : $INSTALL_DIR"
Write-Host "  Python            : $PYTHON_EXE"
Write-Host "  Task Scheduler    : MT5 (30s) → GoldBot (60s) dopo boot"
Write-Host ""
Write-Host "  PROSSIMI STEP:" -ForegroundColor Yellow
Write-Host "  1. Verifica che config.json sia in $INSTALL_DIR" -ForegroundColor Yellow
Write-Host "  2. Apri MT5 e accedi con il tuo account demo" -ForegroundColor Yellow
Write-Host "  3. Abilita 'Auto Trading' in MT5" -ForegroundColor Yellow
Write-Host "  4. Riavvia la VM per testare l'avvio automatico" -ForegroundColor Yellow
Write-Host "  5. Controlla i log in $INSTALL_DIR\bot.log" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Per avviare il bot manualmente adesso:" -ForegroundColor Cyan
Write-Host "  cd $INSTALL_DIR && python bot.py" -ForegroundColor White
Write-Host ""
