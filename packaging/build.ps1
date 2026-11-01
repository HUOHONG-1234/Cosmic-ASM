$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distDirectory = Join-Path $projectRoot "dist"
$workDirectory = Join-Path $projectRoot "build\pyinstaller"
$installerProject = Join-Path $PSScriptRoot "CosmicAsm.wixproj"

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
    @{ Script = "compiler_entry.py"; Name = "CosmicCompiler" }
)

foreach ($entryPoint in $entryPoints) {
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
& dotnet build $installerProject --configuration Release "-p:BinaryDirectory=$binaryDirectory"
if ($LASTEXITCODE -ne 0) {
    throw "WiX failed while building the MSI."
}

$msiPath = Join-Path $PSScriptRoot "bin\Release\CosmicAsm.msi"
if (-not (Test-Path -LiteralPath $msiPath)) {
    throw "WiX completed without producing the expected MSI: $msiPath"
}

$distributionMsi = Join-Path $distDirectory "CosmicAsm.msi"
Copy-Item -LiteralPath $msiPath -Destination $distributionMsi -Force
Write-Host "Created:"
Write-Host "  $(Join-Path $distDirectory 'CosmicInterpreter.exe')"
Write-Host "  $(Join-Path $distDirectory 'CosmicCompiler.exe')"
Write-Host "  $distributionMsi"
