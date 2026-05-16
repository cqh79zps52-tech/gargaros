# Gargaros v0.6 — full Windows uninstall. Reverses install-windows.ps1.
#
# Does NOT delete the repo, the Rust target/ dir, or anything outside the
# locations the installer touched.
#
#   pwsh -File scripts/uninstall-windows.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$binDir   = Join-Path $repoRoot 'bin'

function Header($msg) {
    Write-Host ''
    Write-Host "==[ $msg ]=================================" -ForegroundColor Cyan
}

# Stop daemon
Header 'stop gargaros-server'
Get-Process gargaros-server -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Host "stopped PID $($_.Id)"
}

# Startup shortcut
Header 'auto-start'
$startup  = [System.Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'Gargaros Server.lnk'
if (Test-Path $linkPath) {
    Remove-Item $linkPath -Force
    Write-Host "removed: $linkPath"
}

# Claude Code integration
Header 'Claude Code'
$hookDst  = Join-Path $env:USERPROFILE '.claude\hooks\gargaros_state.cjs'
$agentDst = Join-Path $env:USERPROFILE '.claude\agents\desktop.md'
foreach ($f in @($hookDst, $agentDst)) {
    if (Test-Path $f) {
        Remove-Item $f -Force
        Write-Host "removed: $f"
    }
}

$settingsPath = Join-Path $env:USERPROFILE '.claude\settings.json'
if (Test-Path $settingsPath) {
    try {
        $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json -AsHashtable
        if ($settings.ContainsKey('hooks') -and $settings.hooks.ContainsKey('UserPromptSubmit')) {
            $entries = @($settings.hooks.UserPromptSubmit | Where-Object {
                $keep = $true
                foreach ($h in $_.hooks) {
                    if ($h.command -like '*gargaros_state.cjs*') { $keep = $false; break }
                }
                $keep
            })
            if ($entries.Count -eq 0) {
                $settings.hooks.Remove('UserPromptSubmit')
                if ($settings.hooks.Count -eq 0) { $settings.Remove('hooks') }
            } else {
                $settings.hooks.UserPromptSubmit = $entries
            }
            ($settings | ConvertTo-Json -Depth 12) | Set-Content -Path $settingsPath -Encoding UTF8
            Write-Host "cleaned: $settingsPath"
        }
    } catch {
        Write-Warning "could not parse $settingsPath — leaving it alone."
    }
}

# Binaries + PATH
Header 'binaries + PATH'
if (Test-Path $binDir) {
    Remove-Item -Recurse -Force $binDir
    Write-Host "removed: $binDir"
}
$userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
if ($userPath -like "*$binDir*") {
    $newPath = ($userPath -split ';' | Where-Object { $_ -ne $binDir }) -join ';'
    [System.Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
    Write-Host "removed $binDir from user PATH"
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor Green
Write-Host ' UNINSTALL COMPLETE' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor Green
