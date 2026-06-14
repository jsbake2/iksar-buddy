# iksar_buddy guest hardening — kill Windows Update (no surprise reboots), Game Bar/DVR,
# telemetry, Store auto-download, Delivery Optimization, error reporting; never sleep.
# Runs as SYSTEM via the qemu agent. Idempotent. Best-effort: TrustedInstaller-protected
# items may refuse even SYSTEM -> we try and report what actually stuck.
$ErrorActionPreference = 'SilentlyContinue'

function Reg($path, $name, $type, $val) {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    New-ItemProperty -Path $path -Name $name -PropertyType $type -Value $val -Force | Out-Null
}
function KillSvc($name) {
    Stop-Service $name -Force -ErrorAction SilentlyContinue
    Set-Service  $name -StartupType Disabled -ErrorAction SilentlyContinue
    # protected services ignore Set-Service -> force the start type via registry
    Reg "HKLM:\SYSTEM\CurrentControlSet\Services\$name" 'Start' DWord 4
}
function KillTasks($path) {
    Get-ScheduledTask -TaskPath $path -ErrorAction SilentlyContinue |
        Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null
}

# ---- Windows Update -------------------------------------------------------
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoUpdate' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'AUOptions' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoRebootWithLoggedOnUsers' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' 'DoNotConnectToWindowsUpdateInternetLocations' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' 'SetDisableUXWUAccess' DWord 1
'wuauserv','UsoSvc','WaaSMedicSvc','DoSvc','BITS' | ForEach-Object { KillSvc $_ }
KillTasks '\Microsoft\Windows\UpdateOrchestrator\*'
KillTasks '\Microsoft\Windows\WindowsUpdate\*'
KillTasks '\Microsoft\Windows\InstallService\*'
KillTasks '\Microsoft\Windows\WaaSMedic\*'

# ---- Telemetry / diagnostics ---------------------------------------------
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' 'AllowTelemetry' DWord 0
Reg 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection' 'AllowTelemetry' DWord 0
'DiagTrack','dmwappushservice' | ForEach-Object { KillSvc $_ }
KillTasks '\Microsoft\Windows\Application Experience\*'
KillTasks '\Microsoft\Windows\Customer Experience Improvement Program\*'
KillTasks '\Microsoft\Windows\Feedback\*'
KillTasks '\Microsoft\Windows\DiskDiagnostic\*'

# ---- Game Bar / Game DVR --------------------------------------------------
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' 'AllowGameDVR' DWord 0
# per-user bits for every real (interactive) user hive currently loaded
Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue |
    Where-Object { $_.PSChildName -match '^S-1-5-21-\d' -and $_.PSChildName -notmatch '_Classes$' } |
    ForEach-Object {
        $sid = $_.PSChildName
        Reg "Registry::HKEY_USERS\$sid\System\GameConfigStore" 'GameDVR_Enabled' DWord 0
        Reg "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\GameDVR" 'AppCaptureEnabled' DWord 0
        Reg "Registry::HKEY_USERS\$sid\Software\Microsoft\GameBar" 'AutoGameModeEnabled' DWord 0
        Reg "Registry::HKEY_USERS\$sid\Software\Microsoft\GameBar" 'ShowStartupPanel' DWord 0
    }
'XblAuthManager','XblGameSave','XboxGipSvc','XboxNetApiSvc' | ForEach-Object { KillSvc $_ }

# ---- Store auto-download / consumer features / delivery optimization ------
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' 'AutoDownload' DWord 2
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' 'DisableWindowsConsumerFeatures' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' 'DisableSoftLanding' DWord 1
Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization' 'DODownloadMode' DWord 0

# ---- Windows Error Reporting ---------------------------------------------
Reg 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting' 'Disabled' DWord 1
KillSvc 'WerSvc'

# ---- Power: never sleep / blank / spin down; no hibernate -----------------
foreach ($a in 'standby-timeout-ac','standby-timeout-dc','monitor-timeout-ac',
               'monitor-timeout-dc','disk-timeout-ac','disk-timeout-dc',
               'hibernate-timeout-ac','hibernate-timeout-dc') {
    powercfg /change $a 0 | Out-Null
}
powercfg /hibernate off | Out-Null

# ---- report ---------------------------------------------------------------
"=== service states ==="
Get-Service wuauserv,UsoSvc,WaaSMedicSvc,DoSvc,DiagTrack,dmwappushservice,WerSvc,XblGameSave `
    -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType |
    Format-Table -AutoSize | Out-String
"=== policy values ==="
"NoAutoUpdate     = " + (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate -EA SilentlyContinue).NoAutoUpdate
"NoAutoReboot     = " + (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoRebootWithLoggedOnUsers -EA SilentlyContinue).NoAutoRebootWithLoggedOnUsers
"AllowTelemetry   = " + (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name AllowTelemetry -EA SilentlyContinue).AllowTelemetry
"AllowGameDVR     = " + (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Name AllowGameDVR -EA SilentlyContinue).AllowGameDVR
"DODownloadMode   = " + (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization' -Name DODownloadMode -EA SilentlyContinue).DODownloadMode
"DONE"
