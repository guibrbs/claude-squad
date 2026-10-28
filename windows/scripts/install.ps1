<#
.SYNOPSIS
  Instala o Claude Duo: uma segunda conta do Claude Desktop no mesmo Windows.

.DESCRIPTION
  Copia o app do Claude oficial (MSIX) para %LOCALAPPDATA%\Programs\ClaudeDuo\app e
  renomeia o executável para "Claude Duo.exe". A cópia não é alterada por dentro: o
  executável continua com a assinatura da Anthropic. Os atalhos (Menu Iniciar e Área
  de Trabalho) abrem a cópia com um perfil do Desktop só dela (--user-data-dir), com
  o ícone azul e o mesmo ID de barra de tarefas da janela, para o ícone fixado ficar azul.

  O perfil do Claude Code do Duo (CLAUDE_CONFIG_DIR) é configurado uma vez dentro do
  próprio Duo; o instalador mostra como. Instala também o comando claude-duo (o
  Claude Code CLI na conta do Duo) e as ferramentas Python.

  A cópia não se atualiza sozinha: rode este instalador de novo depois que o Claude
  oficial atualizar. Rodar de novo é seguro: troca só o app e mantém os perfis.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
[CmdletBinding()]
param(
    [string] $Name = 'Claude Duo',
    [string] $UserDataDir = (Join-Path $env:LOCALAPPDATA 'Claude-Duo'),
    [string] $CodeConfigDir = (Join-Path $env:USERPROFILE '.claude-duo'),
    [ValidateRange(0, 359)] [int] $Hue = 200,
    [string] $InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\ClaudeDuo'),
    [switch] $NoDesktopShortcut,
    [switch] $WithSkill,
    [switch] $Launch
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
# Electron's default taskbar ID for an app whose productName is "Claude" (the copy runs outside the package).
$appUserModelId = 'electron.app.Claude'

function Step($text) { Write-Host "-> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "recusado: $text" -ForegroundColor Red; exit 1 }
function Inside($path, $parent) {
    $path.TrimEnd('\') -ieq $parent.TrimEnd('\') -or $path.StartsWith($parent.TrimEnd('\') + '\', 'OrdinalIgnoreCase')
}

# 1. Requisitos
Step 'Conferindo os requisitos'
if ($Name -notmatch '^[\w .-]{1,40}$') { Fail "nome inválido: $Name" }
$package = Get-AppxPackage -Name Claude | Sort-Object Version -Descending | Select-Object -First 1
if (-not $package) { Fail 'o Claude oficial (Microsoft Store/MSIX) não está instalado.' }
$source = Join-Path $package.InstallLocation 'app'
if (-not (Test-Path (Join-Path $source 'claude.exe'))) { Fail "claude.exe não encontrado em $source" }
Write-Host "   Claude $($package.Version) em $($package.InstallLocation)"

# Duas instâncias no mesmo perfil podem corrompê-lo: os perfis do Claude oficial são recusados.
$UserDataDir = [System.IO.Path]::GetFullPath($UserDataDir).TrimEnd('\')
$CodeConfigDir = [System.IO.Path]::GetFullPath($CodeConfigDir).TrimEnd('\')
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
foreach ($folder in $UserDataDir, $CodeConfigDir, $InstallDir) {
    if ($folder.Contains('"') -or $folder.StartsWith('\\')) { Fail "a pasta precisa ser um caminho local sem aspas: $folder" }
    if (-not (Inside $folder $env:USERPROFILE)) { Fail "as pastas do Duo precisam ficar dentro de $env:USERPROFILE" }
}
foreach ($official in (Join-Path $env:LOCALAPPDATA 'Claude-Data'), (Join-Path $env:APPDATA 'Claude'),
                      (Join-Path $env:APPDATA 'Claude-3p'), (Join-Path $env:LOCALAPPDATA 'Packages')) {
    if (Inside $UserDataDir $official) { Fail "UserDataDir aponta para o perfil do Claude oficial: $UserDataDir" }
}
if ($CodeConfigDir -ieq (Join-Path $env:USERPROFILE '.claude')) { Fail 'CodeConfigDir não pode ser o ~\.claude do Claude oficial.' }
if ((Inside $UserDataDir $CodeConfigDir) -or (Inside $CodeConfigDir $UserDataDir)) { Fail 'UserDataDir e CodeConfigDir precisam ser pastas separadas.' }

$app = Join-Path $InstallDir 'app'
$running = Get-CimInstance Win32_Process -Filter "Name = '$Name.exe'" | Where-Object { $_.ExecutablePath -and (Inside $_.ExecutablePath $app) }
if ($running) { Fail "o $Name está aberto. Feche pela bandeja (Sair) e rode de novo." }

# 2. Configuração (duo.json): a fonte que os scripts Python e o desinstalador seguem
Step 'Gravando a configuração'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$configPath = Join-Path $InstallDir 'duo.json'
$previous = if (Test-Path $configPath) { Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json } else { $null }
$exe = Join-Path $app "$Name.exe"
$config = [ordered]@{
    name          = $Name
    exe           = $exe
    userDataDir   = $UserDataDir
    codeConfigDir = $CodeConfigDir
    hue           = $Hue
    appVersion    = "$($package.Version)"
    package       = $package.PackageFamilyName
}
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
Write-Host "   $configPath"

# 3. Ícone azul, gerado do Claude instalado
Step 'Gerando o ícone'
$icon = Join-Path $InstallDir 'claude-duo.ico'
& (Join-Path $PSScriptRoot 'make-icon.ps1') -Out $icon -Hue $Hue -AssetsDir (Join-Path $package.InstallLocation 'assets')

# 4. Cópia do app: numa pasta nova, e só no fim troca pela anterior. A mesma versão já copiada é mantida.
$current = (Test-Path $exe) -and $previous -and $previous.appVersion -eq "$($package.Version)" -and
           (Get-AuthenticodeSignature $exe).Status -eq 'Valid'
if ($current) {
    Step "O app já está na versão $($package.Version); cópia mantida"
} else {
    Step "Copiando o Claude $($package.Version) (pode levar alguns minutos)"
    $staging = Join-Path $InstallDir 'app.new'
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    robocopy $source $staging /E /COPY:DAT /R:2 /W:2 /NFL /NDL /NP /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "a cópia falhou (robocopy $LASTEXITCODE)" }
    Rename-Item (Join-Path $staging 'claude.exe') "$Name.exe"
    $signature = Get-AuthenticodeSignature (Join-Path $staging "$Name.exe")
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Anthropic') {
        Remove-Item -Recurse -Force $staging
        Fail "o executável copiado não tem a assinatura válida da Anthropic ($($signature.Status))."
    }
    if (Test-Path $app) {
        $old = Join-Path $InstallDir 'app.old'
        if (Test-Path $old) { Remove-Item -Recurse -Force $old }
        Rename-Item $app 'app.old'
        Rename-Item $staging 'app'
        Remove-Item -Recurse -Force $old
    } else {
        Rename-Item $staging 'app'
    }
}
# Versões anteriores tinham um lançador compilado na raiz; ele não é mais usado.
Get-ChildItem -LiteralPath $InstallDir -Filter '*.exe' -File -ErrorAction SilentlyContinue | Remove-Item -Force
Write-Host "   $exe"

# 5. Atalhos com o ícone azul e o ID da barra de tarefas da janela
Step 'Criando os atalhos'
if (-not ('DuoShortcut' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class DuoShortcut
{
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        int GetCount(out uint count);
        int GetAt(uint index, out PropertyKey key);
        int GetValue(ref PropertyKey key, IntPtr value);
        int SetValue(ref PropertyKey key, PropVariant value);
        int Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid FormatId; public uint PropertyId; }

    [StructLayout(LayoutKind.Sequential)]
    class PropVariant { public ushort Type = 31; ushort r1, r2, r3; public IntPtr Text; IntPtr unused; }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(string path, IntPtr bindContext, int flags, ref Guid iid, out IPropertyStore store);

    // Gives a .lnk the AppUserModelID of the windows it opens, so the taskbar shows them under it.
    public static void SetAppUserModelId(string shortcut, string id)
    {
        Guid iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        Marshal.ThrowExceptionForHR(SHGetPropertyStoreFromParsingName(shortcut, IntPtr.Zero, 2, ref iid, out store));
        PropertyKey key = new PropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
        PropVariant value = new PropVariant { Text = Marshal.StringToCoTaskMemUni(id) };
        try
        {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, value));
            Marshal.ThrowExceptionForHR(store.Commit());
        }
        finally { Marshal.FreeCoTaskMem(value.Text); Marshal.ReleaseComObject(store); }
    }
}
'@
}
$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath('Programs'))
if (-not $NoDesktopShortcut) { $places += [Environment]::GetFolderPath('Desktop') }
foreach ($place in $places) {
    $path = Join-Path $place "$Name.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $exe
    $link.Arguments = "--user-data-dir=`"$UserDataDir`""
    $link.WorkingDirectory = $env:USERPROFILE
    $link.IconLocation = "$icon,0"
    $link.Description = "Segunda conta do Claude ($Name)"
    try {
        $link.Save()
        [DuoShortcut]::SetAppUserModelId($path, $appUserModelId)
        Write-Host "   $path"
    } catch {
        # Antivírus como o Bitdefender podem proteger a Área de Trabalho; o atalho do Menu Iniciar basta.
        Write-Host "   não foi possível criar $path ($($_.Exception.Message)); seguindo sem ele." -ForegroundColor Yellow
    }
}

# 6. Comando claude-duo: o Claude Code CLI na conta do Duo
Step 'Instalando o comando claude-duo'
$bin = Join-Path $env:USERPROFILE '.local\bin'
$onPath = ($env:PATH -split ';') | Where-Object { $_ -and ($_.Trim('"').TrimEnd('\') -ieq $bin) }
if (-not $onPath) { $bin = Join-Path $InstallDir 'bin' }
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$cmd = @"
@echo off
rem claude-duo: Claude Code CLI na conta do $Name (gerado por scripts\install.ps1)
setlocal
for /f "delims==" %%v in ('set CLAUDE 2^>nul') do set "%%v="
for /f "delims==" %%v in ('set ANTHROPIC_ 2^>nul') do set "%%v="
set "CLAUDE_CONFIG_DIR=$CodeConfigDir"
claude %*
"@
# cmd.exe lê o .cmd na página de código OEM do console
$oem = [System.Text.Encoding]::GetEncoding([System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
[System.IO.File]::WriteAllText((Join-Path $bin 'claude-duo.cmd'), ($cmd -replace "`r?`n", "`r`n"), $oem)
Write-Host "   $(Join-Path $bin 'claude-duo.cmd')"
if (-not $onPath) { Write-Host "   Adicione $bin ao PATH para usar 'claude-duo' em qualquer terminal." -ForegroundColor Yellow }

# 7. Ferramentas Python (duo-send, loop-duo, share-code-profile) num caminho fixo
Step 'Copiando as ferramentas'
$tools = Join-Path $InstallDir 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
Copy-Item -Force (Join-Path $repo 'duo\*.py') $tools
Write-Host "   $tools"

# 8. Opcional: a skill duo-send no Claude Code original
if ($WithSkill) {
    Step 'Instalando a skill duo-send no ~/.claude'
    $skill = Join-Path $env:USERPROFILE '.claude\skills\duo-send'
    $existing = Join-Path $skill 'SKILL.md'
    if ((Test-Path $existing) -and -not (Select-String -Quiet -SimpleMatch 'claude-duo: instalado por' $existing)) {
        Write-Host "   $existing já existe e não foi instalado por aqui; mantido." -ForegroundColor Yellow
    } else {
        New-Item -ItemType Directory -Force -Path $skill | Out-Null
        Copy-Item -Force (Join-Path $repo 'duo\skills\duo-send\SKILL.md') $existing
        Write-Host "   $existing"
    }
}

New-Item -ItemType Directory -Force -Path $UserDataDir, $CodeConfigDir | Out-Null

Write-Host ''
Write-Host "$Name instalado (Claude $($package.Version))." -ForegroundColor Green
Write-Host "  App:          $exe"
Write-Host "  Perfil:       $UserDataDir"
Write-Host "  Claude Code:  $CodeConfigDir"
Write-Host ''
Write-Host 'Uma vez só, depois de instalar:' -ForegroundColor Yellow
Write-Host "  1. Barra de tarefas azul: Menu Iniciar > clique direito em '$Name' > Fixar na barra de tarefas."
Write-Host '     Fixe pelo Menu Iniciar, não pela janela aberta: a janela não sabe qual é o perfil do Duo.'
Write-Host "  2. Abra o $Name e entre com a segunda conta."
Write-Host '  3. Aba Code > seletor de ambiente abaixo da caixa de prompt > engrenagem em Local >'
Write-Host "     acrescente a linha  CLAUDE_CONFIG_DIR=$CodeConfigDir  > Salvar alterações."
Write-Host "     Depois feche o $Name pela bandeja (Sair) e abra de novo."
Write-Host '  4. Links claude:// sempre no Claude oficial: Configurações > Aplicativos > Aplicativos padrão >'
Write-Host '     Escolher padrões por tipo de link > CLAUDE > Claude (o oficial). Toda vez que abre, o Duo se'
Write-Host '     registra como dono desses links, e um link aberto nele cairia fora do perfil do Duo.'
Write-Host '  5. CLI da conta (opcional): claude-duo auth login, num terminal fora do Claude.'
Write-Host ''
Write-Host 'Quando o Claude oficial atualizar, rode este instalador de novo para atualizar o Duo.'
Write-Host 'A extensão Claude in Chrome conversa com o último dos dois apps que foi aberto.'
if ($Launch) { Start-Process -FilePath $exe -ArgumentList "--user-data-dir=`"$UserDataDir`"" -WorkingDirectory $env:USERPROFILE }
