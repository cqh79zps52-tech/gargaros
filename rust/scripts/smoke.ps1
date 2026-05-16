# End-to-end smoke for the Rust core + Named Pipe + Node client.
# Runs unit tests, starts gargaros-server, exercises every opcode that has
# a non-stub implementation, and saves the mouse position so the user is
# not disrupted.
#
# Usage:
#   pwsh -File rust/scripts/smoke.ps1

$ErrorActionPreference = 'Stop'

$env:RUSTUP_HOME = 'D:\rust\rustup'
$env:CARGO_HOME  = 'D:\rust\cargo'
$env:PATH        = "D:\rust\cargo\bin;E:\ikeep\mingw64\bin;$env:PATH"

$repoRoot   = (Resolve-Path "$PSScriptRoot\..\..").Path
$rustRoot   = Join-Path $repoRoot 'rust'
$clientRoot = Join-Path $repoRoot 'client-ts'
$serverExe  = Join-Path $rustRoot 'target\release\gargaros-server.exe'
$cliExe     = Join-Path $rustRoot 'target\release\gargaros.exe'
$nodeCli    = Join-Path $clientRoot 'dist\cli.js'
$tmpFrame   = Join-Path $env:TEMP 'gargaros_smoke.webp'

function Header($msg) {
    Write-Host ''
    Write-Host "==[ $msg ]=================================" -ForegroundColor Cyan
}

function CursorPos {
    Add-Type -AssemblyName System.Windows.Forms
    return [System.Windows.Forms.Cursor]::Position
}

function MoveCursor($x, $y) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point $x, $y
}

# -------------------------------------------------------------------------
# 1. Build + tests
# -------------------------------------------------------------------------

Header 'cargo test --workspace --release'
Push-Location $rustRoot
try {
    & cargo test --workspace --release --quiet
    if ($LASTEXITCODE -ne 0) { throw "cargo test failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

Header 'cargo clippy -- -D warnings'
Push-Location $rustRoot
try {
    & cargo clippy --workspace --all-targets --quiet -- -D warnings
    if ($LASTEXITCODE -ne 0) { throw "cargo clippy failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

Header 'cargo build --release'
Push-Location $rustRoot
try {
    & cargo build --release --workspace --quiet
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

if (-not (Test-Path $serverExe)) { throw "missing $serverExe" }
if (-not (Test-Path $cliExe))    { throw "missing $cliExe" }

if (-not (Test-Path $nodeCli)) {
    Header 'npm run build (client-ts)'
    Push-Location $clientRoot
    try {
        & "C:\Program Files\nodejs\npm.cmd" run build --silent
        if ($LASTEXITCODE -ne 0) { throw "npm build failed (exit $LASTEXITCODE)" }
    } finally { Pop-Location }
}

# -------------------------------------------------------------------------
# 2. Start the daemon
# -------------------------------------------------------------------------

# Make sure no previous instance is hogging the pipe.
Get-Process gargaros-server -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 200

Header 'starting gargaros-server'
$env:RUST_LOG = 'gargaros=warn'
$serverProc = Start-Process -FilePath $serverExe -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 800
if ($serverProc.HasExited) {
    throw "gargaros-server exited immediately (code $($serverProc.ExitCode))"
}

# Save the cursor position so we can restore it after the move test.
$savedCursor = CursorPos
Write-Host "saved cursor pos: $($savedCursor.X), $($savedCursor.Y)"

try {
    # -------------------------------------------------------------------
    # 3. Rust CLI smokes
    # -------------------------------------------------------------------

    Header 'rust CLI: ping'
    $out = & $cliExe ping
    if ($LASTEXITCODE -ne 0) { throw "rust ping failed: $out" }
    if ($out -notmatch '0x81') { throw "expected PONG opcode 0x81, got: $out" }
    Write-Host "OK: $out"

    Header 'rust CLI: screenshot'
    $out = & $cliExe screenshot
    if ($LASTEXITCODE -ne 0) { throw "rust screenshot failed" }
    if ($out -notmatch 'opcode 0x90') { throw "expected FRAME opcode 0x90, got: $out" }
    Write-Host "OK: $out"

    # -------------------------------------------------------------------
    # 4. Node CLI smokes
    # -------------------------------------------------------------------

    Header 'node CLI: ping'
    $out = & node $nodeCli ping
    if ($LASTEXITCODE -ne 0) { throw "node ping failed: $out" }
    if ($out -notmatch 'pong') { throw "expected 'pong', got: $out" }
    Write-Host "OK: $out"

    Header 'node CLI: ui'
    $out = & node $nodeCli ui
    if ($LASTEXITCODE -ne 0) { throw "node ui failed: $out" }
    if ($out -notmatch 'focus hwnd=') { throw "unexpected ui output: $out" }
    Write-Host "OK: $out"

    Header 'node CLI: screenshot -> WebP file'
    if (Test-Path $tmpFrame) { Remove-Item $tmpFrame -Force }
    $out = & node $nodeCli screenshot $tmpFrame
    if ($LASTEXITCODE -ne 0) { throw "node screenshot failed" }
    if (-not (Test-Path $tmpFrame)) { throw "WebP file not written" }
    $bytes = (Get-Item $tmpFrame).Length
    if ($bytes -lt 1024) { throw "WebP suspiciously small: $bytes bytes" }
    if ($bytes -gt 4 * 1024 * 1024) { throw "WebP too large (encoder broken?): $bytes bytes" }
    $head = [System.IO.File]::ReadAllBytes($tmpFrame)[0..3]
    $magic = -join ($head | ForEach-Object { [char]$_ })
    if ($magic -ne 'RIFF') { throw "WebP magic mismatch: '$magic'" }
    Write-Host "OK: $bytes-byte WebP starts with RIFF"
    Remove-Item $tmpFrame -Force

    # -------------------------------------------------------------------
    # 5. Real input — move cursor + verify, then restore
    # -------------------------------------------------------------------

    Header 'node CLI: move cursor (real SendInput)'
    $targetX = 137; $targetY = 241
    & node $nodeCli move $targetX $targetY
    if ($LASTEXITCODE -ne 0) { throw "node move failed" }
    Start-Sleep -Milliseconds 150
    $after = CursorPos
    Write-Host "cursor after move: $($after.X), $($after.Y) (target $targetX, $targetY)"
    if ([math]::Abs($after.X - $targetX) -gt 4 -or [math]::Abs($after.Y - $targetY) -gt 4) {
        throw "cursor not at target: have ($($after.X), $($after.Y))"
    }
    Write-Host "OK: cursor moved within tolerance"

} finally {
    Header 'cleanup'
    if ($savedCursor) { MoveCursor $savedCursor.X $savedCursor.Y }
    if (-not $serverProc.HasExited) {
        Stop-Process -Id $serverProc.Id -Force
        $serverProc.WaitForExit(2000) | Out-Null
    }
    Get-Process gargaros-server -ErrorAction SilentlyContinue | Stop-Process -Force
}

Write-Host ''
Write-Host '=============================================' -ForegroundColor Green
Write-Host ' SMOKE PASS' -ForegroundColor Green
Write-Host '=============================================' -ForegroundColor Green
