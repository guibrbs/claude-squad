<#
.SYNOPSIS
  Remove o Claude Duo: a cópia do app, os atalhos e o comando claude-duo.

.DESCRIPTION
  Devolve ao Claude oficial os links claude:// e a extensão do Chrome, que o Duo
  toma para si quando abre. Os perfis (login, conversas, histórico do Claude Code)
  ficam onde estão, a menos que -RemoveProfiles seja passado: aí eles vão para a
  Lixeira, depois de uma confirmação. O Claude oficial não é tocado.
#>
[CmdletBinding()]
param(
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ClaudeDuo'),
    [switch] $RemoveProfiles
)
$ErrorActionPreference = 'Stop'

$configPath = Join-Path $InstallDir 'duo.json'
if (-not (Test-Path $configPath)) { Write-Host "Nada a remover: $configPath não existe."; exit 0 }
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

$app = (Join-Path $InstallDir 'app').TrimEnd('\') + '\'
$running = Get-CimInstance Win32_Process -Filter "Name = 'claude.exe' OR Name = '$($config.name).exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($app, 'OrdinalIgnoreCase') }
if ($running) { Write-Host "recusado: o $($config.name) está aberto. Feche pela bandeja (Sair) e rode de novo." -ForegroundColor Red; exit 1 }

foreach ($place in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) {
    $link = Join-Path $place "$($config.name).lnk"
    if (Test-Path $link) { Remove-Item -Force $link; Write-Host "removido $link" }
}
foreach ($cmd in (Join-Path $env:USERPROFILE '.local\bin\claude-duo.cmd'), (Join-Path $InstallDir 'bin\claude-duo.cmd')) {
    if ((Test-Path $cmd) -and (Select-String -Quiet -SimpleMatch 'gerado por scripts\install.ps1' $cmd)) {
        Remove-Item -Force $cmd; Write-Host "removido $cmd"
    }
}
$skill = Join-Path $env:USERPROFILE '.claude\skills\duo-send'
if ((Test-Path "$skill\SKILL.md") -and (Select-String -Quiet -SimpleMatch 'claude-duo: instalado por' "$skill\SKILL.md")) {
    Remove-Item -Recurse -Force $skill; Write-Host "removido $skill"
}

# Links e extensão: só o que aponta para a cópia volta para o Claude oficial.
$alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\claude-desktop.exe'
$command = 'HKCU:\Software\Classes\claude\shell\open\command'
if ((Test-Path $command) -and "$((Get-Item $command).GetValue(''))".IndexOf($app, 'OrdinalIgnoreCase') -ge 0 -and (Test-Path $alias)) {
    Set-Item $command -Value "`"$alias`" `"%1`""
    Write-Host 'claude:// devolvido ao Claude oficial'
}
$manifest = Join-Path $env:APPDATA 'Claude-NativeHost\com.anthropic.claude_browser_extension.json'
foreach ($browser in 'Google\Chrome', 'Microsoft\Edge') {
    $key = "HKCU:\Software\$browser\NativeMessagingHosts\com.anthropic.claude_browser_extension"
    if ((Test-Path $key) -and "$((Get-Item $key).GetValue(''))".IndexOf($config.userDataDir, 'OrdinalIgnoreCase') -ge 0) {
        if (Test-Path $manifest) { Set-Item $key -Value $manifest } else { Remove-Item $key }
        Write-Host "extensão do $browser devolvida ao Claude oficial"
    }
}

if ($RemoveProfiles) {
    $answer = Read-Host "Mandar para a Lixeira os perfis do Duo (login, conversas e histórico)?`n  $($config.userDataDir)`n  $($config.codeConfigDir)`nDigite SIM para confirmar"
    if ($answer -ceq 'SIM') {
        Add-Type -AssemblyName Microsoft.VisualBasic
        foreach ($folder in $config.userDataDir, $config.codeConfigDir) {
            if (Test-Path $folder) {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($folder, 'OnlyErrorDialogs', 'SendToRecycleBin')
                Write-Host "para a Lixeira: $folder"
            }
        }
    } else { Write-Host 'Perfis mantidos.' }
} else {
    Write-Host "Perfis mantidos em $($config.userDataDir) e $($config.codeConfigDir)."
}

Remove-Item -Recurse -Force $InstallDir
Write-Host "removido $InstallDir"
Write-Host "$($config.name) desinstalado." -ForegroundColor Green
