# Makes Nova easy to launch: a desktop icon, a Start Menu entry, and a `nova`
# command in PowerShell. Safe to re-run - it overwrites rather than duplicates.
#
#   powershell -ExecutionPolicy Bypass -File setup-shortcuts.ps1

$ErrorActionPreference = 'Stop'

$NovaDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Icon    = Join-Path $NovaDir 'nova.ico'

Write-Host "Nova folder: $NovaDir"

# --- find pythonw.exe (python without a console window) ----------------------
$PythonW = $null
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $candidate = Join-Path (Split-Path -Parent $python.Source) 'pythonw.exe'
    if (Test-Path $candidate) { $PythonW = $candidate }
}
if (-not $PythonW) {
    Write-Host "Could not find pythonw.exe next to python. Shortcuts will use python.exe," -ForegroundColor Yellow
    Write-Host "which means a console window stays open behind the app." -ForegroundColor Yellow
    if ($python) { $PythonW = $python.Source } else { throw "Python is not on PATH." }
}
Write-Host "Launcher:    $PythonW"

# --- the shortcuts ------------------------------------------------------------
$shell = New-Object -ComObject WScript.Shell

function New-NovaShortcut {
    param([string]$Path, [string]$Arguments, [string]$Description)

    $link = $shell.CreateShortcut($Path)
    $link.TargetPath       = $PythonW
    $link.Arguments        = $Arguments
    $link.WorkingDirectory = $NovaDir
    $link.Description      = $Description
    if (Test-Path $Icon) { $link.IconLocation = $Icon }
    $link.Save()
    Write-Host "  created  $Path"
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-NovaShortcut -Path (Join-Path $desktop 'Nova.lnk') `
                 -Arguments '"main.py" gui' `
                 -Description 'Nova - an agent that does what you tell it'

$startMenu = Join-Path ([Environment]::GetFolderPath('ApplicationData')) `
                       'Microsoft\Windows\Start Menu\Programs'
New-NovaShortcut -Path (Join-Path $startMenu 'Nova.lnk') `
                 -Arguments '"main.py" gui' `
                 -Description 'Nova - an agent that does what you tell it'

# --- the `nova` command -------------------------------------------------------
$marker = '# --- Nova launcher ---'
$block = @"
$marker
function nova {
    `$novaDir = '$NovaDir'
    if (`$args.Count -eq 0) {
        Set-Location `$novaDir
        python main.py gui
    } else {
        Push-Location `$novaDir
        try { python main.py @args } finally { Pop-Location }
    }
}
# --- end Nova launcher ---
"@

$profileDir = Split-Path -Parent $PROFILE
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}
if (Test-Path $PROFILE) {
    $existing = Get-Content $PROFILE -Raw
} else {
    $existing = ''
}

if ($existing -match [regex]::Escape($marker)) {
    # Replace the old block so re-running never stacks up copies.
    $pattern = '(?s)' + [regex]::Escape($marker) + '.*?# --- end Nova launcher ---'
    $updated = [regex]::Replace($existing, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block })
    Set-Content -Path $PROFILE -Value $updated -Encoding utf8
    Write-Host "  updated  $PROFILE"
} else {
    Add-Content -Path $PROFILE -Value "`r`n$block" -Encoding utf8
    Write-Host "  added to $PROFILE"
}

Write-Host ""
Write-Host "Done. Three ways to start Nova:" -ForegroundColor Green
Write-Host "  1. Double-click the Nova icon on your desktop"
Write-Host "  2. Press the Windows key and type: nova"
Write-Host "  3. In a new PowerShell window, type: nova"
Write-Host ""
Write-Host "The 'nova' command also takes the other modes, from any folder:"
Write-Host "  nova doctor    nova cli    nova usage    nova notes    nova discord"
