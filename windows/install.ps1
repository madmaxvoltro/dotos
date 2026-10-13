# Dot game mode: the Windows side.
#
# Run once on the gaming PC, in PowerShell as administrator:
#   irm https://raw.githubusercontent.com/madmaxvoltro/dotos/main/windows/install.ps1 | iex
#
# It installs:
#   - Game.exe (desktop + Start menu): start game mode from inside your Dot session
#   - Dot Helper (tray, starts with Windows): game mode menu, and enters the Dot's pairing PIN for you
#   - a scheduled task that hands your Windows session to the GPU when a game starts (same-PC setup)
#
# Game.exe and Dot Helper are compiled here from source (windows/DotHelper.cs) with the C# compiler
# that comes with Windows, so nothing prebuilt is downloaded and SmartScreen has nothing to flag.
#
# It doesn't install a game streaming host: install Sunshine or Apollo yourself on the PC that runs the
# games. If one is already here, this offers to let Dot Helper enter pairing PINs into it for you.
#
# Option when run as a file:  .\install.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$Source = "https://raw.githubusercontent.com/madmaxvoltro/dotos/main/windows"
$AppDir = Join-Path $env:ProgramFiles "Dot"
$DataDir = Join-Path $env:ProgramData "Dot"
$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

function Step($text) { Write-Host ""; Write-Host "  * $text" -ForegroundColor White }
function Info($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Fail($text) { Write-Host ""; Write-Host "  x $text" -ForegroundColor Red; throw $text }

Write-Host ""
Write-Host "  *  Dot game mode for Windows" -ForegroundColor White

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail "Run PowerShell as administrator (right-click > Run as administrator), then paste the command again." }

$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "Game.lnk"),
    (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "Game.lnk"),
    (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "Dot Helper.lnk")
)

if ($Uninstall) {
    Step "Removing Dot game mode"
    Get-Process "Dot Helper", "Game" -ErrorAction SilentlyContinue | Stop-Process -Force
    Unregister-ScheduledTask -TaskName "SwitchToConsole" -TaskPath "\Dot\" -Confirm:$false -ErrorAction SilentlyContinue
    Remove-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Dot Helper" -ErrorAction SilentlyContinue
    $shortcuts | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
    Remove-Item $AppDir, $DataDir -Recurse -Force -ErrorAction SilentlyContinue
    return
}

# 1. Game.exe and Dot Helper -------------------------------------------------------------------------------

Step "Building Game.exe and Dot Helper"
if (-not (Test-Path $Csc)) { Fail "The .NET Framework 4 C# compiler wasn't found ($Csc). It comes with Windows 10 and 11." }
New-Item -ItemType Directory -Force $AppDir, $DataDir | Out-Null
$src = Join-Path $env:TEMP "DotHelper.cs"
Invoke-WebRequest "$Source/DotHelper.cs" -OutFile $src -UseBasicParsing
Get-Process "Dot Helper", "Game" -ErrorAction SilentlyContinue | Stop-Process -Force
$refs = "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Web.Extensions.dll", "/r:System.Security.dll"
foreach ($name in "Dot Helper", "Game") {
    & $Csc /nologo /optimize /target:winexe "/out:$AppDir\$name.exe" @refs $src
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't build $name.exe" }
}
Remove-Item $src
Info "$AppDir"

# 2. Shortcuts and start with Windows -------------------------------------------------------------------------

Step "Adding Game to the desktop and Start menu"
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $shortcuts) {
    $lnk = $shell.CreateShortcut($path)
    $isHelper = $path -like "*Dot Helper*"
    $lnk.TargetPath = Join-Path $AppDir ($(if ($isHelper) { "Dot Helper.exe" } else { "Game.exe" }))
    $lnk.Description = $(if ($isHelper) { "Dot game mode menu" } else { "Play on your Dot's main monitor" })
    $lnk.Save()
}
Set-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Dot Helper" -Value "`"$AppDir\Dot Helper.exe`""

# 3. Same-PC setup: hand the session to the GPU without a UAC prompt every time -------------------------------

Step "Allowing game mode to move your session to the GPU"
$switch = Join-Path $AppDir "switch-to-console.ps1"
@'
# Run by the "Dot\SwitchToConsole" task (elevated, in your own session) when Game.exe starts a game on
# this PC: moves this Windows session from Remote Desktop to the GPU so Sunshine can stream it.
$id = (Get-Process -Id $PID).SessionId
& "$env:WINDIR\System32\tscon.exe" $id /dest:console
'@ | Set-Content -Encoding UTF8 $switch
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$switch`""
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "SwitchToConsole" -TaskPath "\Dot\" -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Info "for $user"

# 4. Pairing with Sunshine / Apollo, if it's here -----------------------------------------------------------------

$hostExe = @("Sunshine\sunshine.exe", "Apollo\sunshine.exe", "Apollo\apollo.exe") |
    ForEach-Object { Join-Path $env:ProgramFiles $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
$credFile = Join-Path $DataDir "sunshine.dat"
if (-not $hostExe) {
    Step "No Sunshine or Apollo on this PC"
    Info "Install one yourself on the PC that runs the games; Dot doesn't install it."
} elseif (-not (Test-Path $credFile)) {
    Step "Pairing with $(Split-Path (Split-Path $hostExe) -Leaf)"
    # Dot Helper enters pairing PINs through its local web API (https://localhost:47990), which needs its login
    Write-Host "    To let Dot pair by itself, enter its web page login (or press Enter to skip;" -ForegroundColor Gray
    Write-Host "    you'll type the PIN in its web page instead)." -ForegroundColor Gray
    $u = Read-Host "    User name"
    if ($u) {
        $p = Read-Host "    Password" -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p))
        Add-Type -AssemblyName System.Security
        $bytes = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes("${u}:$plain"), $null, "LocalMachine")
        [IO.File]::WriteAllBytes($credFile, $bytes)
        # readable by administrators and the people who sign in to this PC, encrypted to this machine
        icacls $credFile /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" "*S-1-5-32-545:R" | Out-Null
        Info "Dot Helper can pair automatically"
    }
}

# 5. Start the helper now ------------------------------------------------------------------------------------------

Start-Process (Join-Path $AppDir "Dot Helper.exe")

Write-Host ""
Write-Host "  *  Done." -ForegroundColor White
Write-Host "     On your Dot: Settings > Games, choose how this PC plays games, then Pair."
Write-Host "     Then, signed in from your Dot, open Game on the desktop to play on the main monitor."
Write-Host ""
