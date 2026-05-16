# Gargaros v0.6 — full Windows install.
#
# Idempotent. Run from anywhere; the script discovers the repo root via
# $PSScriptRoot. Does NOT require admin.
#
#   pwsh -File scripts/install-windows.ps1                 # full install
#   pwsh -File scripts/install-windows.ps1 -NoAutoStart    # skip startup shortcut
#   pwsh -File scripts/install-windows.ps1 -NoClaudeCode   # skip ~/.claude hook/subagent
#
# Effects (all reversible — see scripts/uninstall-windows.ps1):
#   1. Build the Rust workspace in release mode.
#   2. Deploy gargaros.exe + gargaros-server.exe to <repo>/bin/.
#   3. Add <repo>/bin/ to the **user** PATH.
#   4. Create a Startup-folder shortcut so the daemon launches at logon
#      (skip with -NoAutoStart).
#   5. Install hook + subagent in ~/.claude/ and patch ~/.claude/settings.json
#      (skip with -NoClaudeCode). A backup of settings.json is created.

[CmdletBinding()]
param(
    [switch] $NoAutoStart,
    [switch] $NoClaudeCode,
    [switch] $SkipBuild
)

$ErrorActionPreference = 'Stop'

$repoRoot   = (Resolve-Path "$PSScriptRoot\..").Path
$rustRoot   = Join-Path $repoRoot 'rust'
$binDir     = Join-Path $repoRoot 'bin'
$releaseDir = Join-Path $rustRoot 'target\release'
$cargoExe   = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe' # may not exist
if (-not (Test-Path $cargoExe) -and (Test-Path 'D:\rust\cargo\bin\cargo.exe')) {
    $cargoExe = 'D:\rust\cargo\bin\cargo.exe'
}

function Header($msg) {
    Write-Host ''
    Write-Host "==[ $msg ]=================================" -ForegroundColor Cyan
}

# -------------------------------------------------------------------------
# 1. Build
# -------------------------------------------------------------------------

if (-not $SkipBuild) {
    Header 'cargo build --release --workspace'
    if (-not (Test-Path $cargoExe)) {
        throw "cargo not found. Install Rust first (https://rustup.rs/) or pass -SkipBuild."
    }
    Push-Location $rustRoot
    try {
        & $cargoExe build --release --workspace
        if ($LASTEXITCODE -ne 0) { throw "cargo build failed (exit $LASTEXITCODE)" }
    } finally { Pop-Location }
} else {
    Write-Host 'Skipping build (--SkipBuild).'
}

# -------------------------------------------------------------------------
# 2. Deploy binaries
# -------------------------------------------------------------------------

Header 'deploy binaries'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }
foreach ($exe in 'gargaros.exe', 'gargaros-server.exe') {
    $src = Join-Path $releaseDir $exe
    if (-not (Test-Path $src)) { throw "missing $src — did the build succeed?" }
    Copy-Item -Path $src -Destination (Join-Path $binDir $exe) -Force
    Write-Host "deployed: $exe"
}

# -------------------------------------------------------------------------
# 3. User PATH
# -------------------------------------------------------------------------

Header 'user PATH'
$userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
if ($userPath -notlike "*$binDir*") {
    $newPath = if ($userPath) { "$binDir;$userPath" } else { $binDir }
    [System.Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
    Write-Host "added $binDir to user PATH (new terminals only)"
} else {
    Write-Host "$binDir already in user PATH"
}

# -------------------------------------------------------------------------
# 4. Autostart at logon (Startup folder, no admin)
# -------------------------------------------------------------------------

$vbs = Join-Path $binDir 'gargaros-autostart.vbs'
if (-not $NoAutoStart) {
    Header 'auto-start at logon'
    Set-Content -Path $vbs -Encoding ASCII -Value @"
' Silent launcher for gargaros-server.exe.
Set sh = CreateObject("WScript.Shell")
sh.Run """$($binDir.Replace('\', '\\'))\\gargaros-server.exe""", 0, False
"@
    $startup  = [System.Environment]::GetFolderPath('Startup')
    $linkPath = Join-Path $startup 'Gargaros Server.lnk'
    if (Test-Path $linkPath) { Remove-Item $linkPath -Force }
    $shell    = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath       = 'wscript.exe'
    $shortcut.Arguments        = "`"$vbs`""
    $shortcut.WorkingDirectory = $binDir
    $shortcut.Description      = 'Gargaros desktop control daemon'
    $shortcut.WindowStyle      = 7  # minimised + hidden
    $shortcut.IconLocation     = "$binDir\gargaros-server.exe,0"
    $shortcut.Save()
    Write-Host "installed: $linkPath"
    Write-Host "VBS launcher: $vbs"
} else {
    Write-Host 'Skipping auto-start (--NoAutoStart).'
}

# -------------------------------------------------------------------------
# 5. Claude Code hook + subagent + settings patch
# -------------------------------------------------------------------------

if (-not $NoClaudeCode) {
    Header 'Claude Code integration'
    $claudeRoot = Join-Path $env:USERPROFILE '.claude'
    $hooksDir   = Join-Path $claudeRoot 'hooks'
    $agentsDir  = Join-Path $claudeRoot 'agents'
    foreach ($d in @($claudeRoot, $hooksDir, $agentsDir)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }

    # Hook
    $hookSrc = Join-Path $repoRoot 'client-ts\dist\hook.js'
    $hookDst = Join-Path $hooksDir 'gargaros_state.cjs'
    # The shipped hook is a TS-build artifact and pulls in @gargaros/client.
    # Instead, ship the standalone CJS version (no deps, only node:net).
    $standalone = Join-Path $repoRoot 'scripts\gargaros_state.cjs'
    if (Test-Path $standalone) {
        Copy-Item $standalone $hookDst -Force
        Write-Host "installed: $hookDst"
    } else {
        throw "missing standalone hook at $standalone"
    }

    # Subagent
    $agentSrc = Join-Path $repoRoot 'client-ts\agents\desktop.md'
    $agentDst = Join-Path $agentsDir 'desktop.md'
    Copy-Item $agentSrc $agentDst -Force
    Write-Host "installed: $agentDst"

    # settings.json patch
    $settingsPath = Join-Path $claudeRoot 'settings.json'
    if (Test-Path $settingsPath) {
        $bak = "$settingsPath.bak-$([int][double]::Parse((Get-Date -UFormat %s)))"
        Copy-Item $settingsPath $bak
        Write-Host "backup: $bak"
        $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json -AsHashtable 2>$null
        if ($null -eq $settings) { $settings = @{} }
    } else {
        $settings = @{}
    }
    if (-not $settings.ContainsKey('hooks')) { $settings.hooks = @{} }
    $cmd = "node `"$hookDst`""
    $entry = @{ hooks = @(@{ type = 'command'; command = $cmd; timeout = 2000 }) }
    $settings.hooks.UserPromptSubmit = @($entry)
    ($settings | ConvertTo-Json -Depth 12) | Set-Content -Path $settingsPath -Encoding UTF8
    Write-Host "patched: $settingsPath"
} else {
    Write-Host 'Skipping Claude Code integration (--NoClaudeCode).'
}

# -------------------------------------------------------------------------
# 6. Start the daemon now (idempotent — first_pipe_instance(true) makes
#    a second instance exit immediately)
# -------------------------------------------------------------------------

Header 'launching gargaros-server'
if (-not (Get-Process gargaros-server -ErrorAction SilentlyContinue)) {
    $serverExe = Join-Path $binDir 'gargaros-server.exe'
    Start-Process -FilePath $serverExe -WindowStyle Hidden | Out-Null
    Start-Sleep -Milliseconds 600
}
$proc = Get-Process gargaros-server -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "running: PID $($proc.Id)" -ForegroundColor Green
} else {
    Write-Warning "gargaros-server did not start. Check it manually."
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor Green
Write-Host ' INSTALL COMPLETE' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor Green
Write-Host "Open a NEW terminal and try:"
Write-Host "  gargaros ping"
Write-Host "  gargaros ui"
Write-Host ''
Write-Host "Uninstall: pwsh -File scripts/uninstall-windows.ps1"
