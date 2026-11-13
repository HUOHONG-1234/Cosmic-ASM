param(
    [string]$Version = "0.1.0",
    [switch]$SkipBinaries
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distDirectory = Join-Path $projectRoot "dist"
$workDirectory = Join-Path $projectRoot "build\pyinstaller"
$installerProject = Join-Path $PSScriptRoot "CosmicAsm.wixproj"
$licenseRtf = (Resolve-Path (Join-Path $PSScriptRoot "License.rtf")).Path

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must be a numeric MSI version such as 0.1.0 (got '$Version')."
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    throw "PyInstaller was not found. Install packaging requirements with: python -m pip install -r requirements-build.txt"
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "The .NET SDK is required to build the MSI."
}

New-Item -ItemType Directory -Force -Path $distDirectory, $workDirectory | Out-Null

$entryPoints = @(
    @{ Script = "interpreter_entry.py"; Name = "CosmicInterpreter" },
    @{ Script = "compiler_entry.py"; Name = "CosmicCompiler" },
    @{ Script = "cosp_entry.py"; Name = "cosp" }
)

foreach ($entryPoint in $entryPoints) {
    $output = Join-Path $distDirectory "$($entryPoint.Name).exe"
    if ($SkipBinaries -and (Test-Path -LiteralPath $output)) {
        Write-Host "Reusing existing $output"
        continue
    }
    $scriptPath = Join-Path $PSScriptRoot $entryPoint.Script
    & $pyinstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name $entryPoint.Name `
        --paths $projectRoot `
        --distpath $distDirectory `
        --workpath (Join-Path $workDirectory $entryPoint.Name) `
        --specpath $workDirectory `
        $scriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed while building $($entryPoint.Name)."
    }
}

$binaryDirectory = (Resolve-Path $distDirectory).Path
& dotnet build $installerProject --configuration Release `
    "-p:BinaryDirectory=$binaryDirectory" `
    "-p:ProductVersion=$Version" `
    "-p:LicenseRtf=$licenseRtf"
if ($LASTEXITCODE -ne 0) {
    throw "WiX failed while building the MSI."
}

$msiPath = Join-Path $PSScriptRoot "bin\Release\CosmicAsm.msi"
if (-not (Test-Path -LiteralPath $msiPath)) {
    throw "WiX completed without producing the expected MSI: $msiPath"
}

$distributionMsi = Join-Path $distDirectory "CosmicAsm-$Version.msi"
Copy-Item -LiteralPath $msiPath -Destination $distributionMsi -Force
Write-Host "Created:"
Write-Host "  $(Join-Path $distDirectory 'CosmicInterpreter.exe')"
Write-Host "  $(Join-Path $distDirectory 'CosmicCompiler.exe')"
Write-Host "  $(Join-Path $distDirectory 'cosp.exe')"
Write-Host "  $distributionMsi"
