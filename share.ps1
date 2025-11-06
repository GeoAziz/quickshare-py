<#
PowerShell helper to start QuickSharePy easily.
Usage examples:
    # Start sharing current folder on port 8080
    .\share.ps1

    # Start sharing a specific folder and open default browser
    .\share.ps1 -Folder "C:\Users\elvis\projects\LLaMA-Factory\data" -Port 8080 -Open

    # Start with password
    .\share.ps1 -Password mypass
#>
param(
    [string]$Folder = ".",
    [int]$Port = 8080,
    [string]$Password = $null,
    [switch]$Open,
    [switch]$Qr
)

$pwdBefore = Get-Location
try {
    Set-Location -Path $Folder
} catch {
    Write-Error "Unable to cd into $Folder: $_"
    exit 1
}

$args = @()
$args += ('"' + (Get-Location).Path + '"')
$args += "--port"
$args += $Port
if ($Password) {
    $args += "--password"
    $args += $Password
}
if ($Qr) { $args += "--qr" }

# Start the server in a new window so the user can stop it with Ctrl+C there.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = ("quickshare.py " + ($args -join ' '))
$psi.UseShellExecute = $true
Start-Process -FilePath $psi.FileName -ArgumentList $psi.Arguments

$ip = (python -c "import socket; s=socket.socket(); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()") 2>$null
if (-not $ip) { $ip = '127.0.0.1' }
$url = "http://$ip`:$Port"
Write-Output "Started QuickSharePy for folder: $(Get-Location).Path"
Write-Output "Access at: $url"
if ($Open) {
    Start-Process $url
}

Set-Location $pwdBefore
