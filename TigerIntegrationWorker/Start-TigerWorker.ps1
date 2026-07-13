[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Tiger Integration Worker" -ForegroundColor Cyan
Write-Host "Folder: $PSScriptRoot"

if (-not (Test-Path -LiteralPath ".\appsettings.json")) {
    Write-Host "appsettings.json is missing." -ForegroundColor Yellow
    Write-Host "Copy appsettings.example.json to appsettings.json and fill in the credentials."
    exit 2
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Host ".NET SDK was not found in PATH." -ForegroundColor Red
    Write-Host "Install .NET 8 SDK, then open a new PowerShell window and run this file again."
    exit 3
}

$project = Join-Path $PSScriptRoot "TigerWorker.csproj"

if (-not $SkipBuild) {
    Write-Host "Building Release version..." -ForegroundColor DarkCyan
    dotnet build $project --configuration Release
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed. Worker was not started." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host "Starting worker. Press Ctrl+C to stop." -ForegroundColor Green
dotnet run --project $project --configuration Release --no-build --no-launch-profile
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "Worker stopped with exit code $exitCode." -ForegroundColor Red
}

exit $exitCode
