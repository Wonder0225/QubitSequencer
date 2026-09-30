$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$releaseVersion = '0.2.1'
$buildStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$distPath = Join-Path $projectRoot "output/release/builds/$releaseVersion-$buildStamp"
Set-Location -LiteralPath $projectRoot

$buildDependencies = Join-Path $projectRoot '.build_deps'
$assets = Join-Path $projectRoot 'qubit_sequencer/assets'
$icon = Join-Path $assets 'app-icon.ico'
$manual = Join-Path $projectRoot 'output/pdf/user-guide.pdf'
$versionInfo = Join-Path $projectRoot 'scripts/windows_version_info.txt'
$buildWork = Join-Path $env:TEMP "QubitSequencerBuild-$PID"
if (Test-Path -LiteralPath $buildDependencies) {
    $env:PYTHONPATH = "$buildDependencies;$env:PYTHONPATH"
}

python -m PyInstaller --clean --noconfirm --onedir --windowed `
    --name QubitSequencer `
    --icon $icon `
    --version-file $versionInfo `
    --add-data "${assets};qubit_sequencer/assets" `
    --add-data "${manual};output/pdf" `
    --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 `
    --exclude-module numpy --exclude-module scipy --exclude-module pandas --exclude-module matplotlib `
    --distpath $distPath `
    --workpath $buildWork `
    --specpath 'output/pyinstaller-spec' `
    'scripts/desktop_entry.py'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$release = Join-Path $distPath 'QubitSequencer'
$examples = Join-Path $release 'examples'
New-Item -ItemType Directory -Path $examples -Force | Out-Null
Copy-Item -LiteralPath 'examples/surface_d3_complete.qchip' -Destination $examples -Force
Copy-Item -LiteralPath 'examples/surface_d3_complete_preview.png' -Destination $examples -Force
Copy-Item -LiteralPath 'examples/surface_d3_complete_topology.png' -Destination $examples -Force
Copy-Item -LiteralPath 'examples/surface_d3_complete_dark_preview.png' -Destination $examples -Force
Copy-Item -LiteralPath 'examples/surface_d3_complete_dark_qubit_f01.png' -Destination $examples -Force
Copy-Item -LiteralPath 'docs/release-readme.md' -Destination (Join-Path $release 'README.md') -Force
Copy-Item -LiteralPath 'output/pdf/user-guide.pdf' -Destination $release -Force
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination $release -Force
Copy-Item -LiteralPath 'licenses' -Destination $release -Recurse -Force
if (Test-Path -LiteralPath 'LICENSE') {
    Copy-Item -LiteralPath 'LICENSE' -Destination $release -Force
}

$archive = Join-Path $projectRoot "output/release/QubitSequencer-$releaseVersion-win64-$buildStamp.zip"
Compress-Archive -LiteralPath $release -DestinationPath $archive -Force
& (Join-Path $PSScriptRoot 'publish_windows_release.ps1') `
    -SourceDirectory $release `
    -ArchivePath $archive `
    -Version $releaseVersion `
    -BuildStamp $buildStamp
