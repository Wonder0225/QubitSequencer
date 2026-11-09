param(
    [Parameter(Mandatory = $true)][string]$SourceDirectory,
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$BuildStamp
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$releaseRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'output/release')).Path
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid release version' }
if ($BuildStamp -notmatch '^\d{8}-\d{6}$') { throw 'Invalid build timestamp' }

function Assert-ReleasePath([string]$Path) {
    $absolute = [System.IO.Path]::GetFullPath($Path)
    if (Test-Path -LiteralPath $absolute) {
        $absolute = (Resolve-Path -LiteralPath $absolute).Path
    }
    $prefix = $releaseRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must be inside the release directory: $absolute"
    }
    return $absolute
}

$source = Assert-ReleasePath $SourceDirectory
$archive = Assert-ReleasePath $ArchivePath
$published = Assert-ReleasePath (Join-Path $releaseRoot 'QubitSequencer')
$publishedArchive = Assert-ReleasePath (Join-Path $releaseRoot "QubitSequencer-$Version-win64.zip")
$required = @(
    'QubitSequencer.exe',
    '_internal/output/pdf/user-guide.pdf',
    '_internal/qubit_sequencer/assets/app-icon.png',
    'examples/surface_d3_complete.qchip',
    'README.md',
    'user-guide.pdf',
    'THIRD_PARTY_NOTICES.md'
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $relative) -PathType Leaf)) {
        throw "Incomplete release: missing $relative"
    }
}

# Verify that the ZIP contains the exact executable being published.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $entry = $zip.GetEntry('QubitSequencer/QubitSequencer.exe')
    if ($null -eq $entry) { $entry = $zip.GetEntry('QubitSequencer\QubitSequencer.exe') }
    if ($null -eq $entry) { throw 'Release ZIP does not contain QubitSequencer.exe' }
    $stream = $entry.Open()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $archiveHash = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '')
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
} finally {
    $zip.Dispose()
}
$exeHash = (Get-FileHash -LiteralPath (Join-Path $source 'QubitSequencer.exe') -Algorithm SHA256).Hash
if ($archiveHash -ne $exeHash) { throw 'Release ZIP and executable do not match' }
$publishId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
$previousRoot = Assert-ReleasePath (Join-Path $releaseRoot "previous/$publishId")

if ($source -ne $published) {
    if ($published.StartsWith($source.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $source.StartsWith($published.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source and destination directories cannot contain one another'
    }
    $previous = Assert-ReleasePath (Join-Path $previousRoot 'QubitSequencer')
    if (Test-Path -LiteralPath $published) {
        New-Item -ItemType Directory -Path $previousRoot -Force | Out-Null
        Copy-Item -LiteralPath $published -Destination $previous -Recurse
    }
    # Keep the directory itself fixed; OneDrive can prevent renaming it.
    New-Item -ItemType Directory -Path $published -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $published -Recurse -Force
    }
}
if ($archive -ne $publishedArchive) {
    if (Test-Path -LiteralPath $publishedArchive) {
        New-Item -ItemType Directory -Path $previousRoot -Force | Out-Null
        Copy-Item -LiteralPath $publishedArchive -Destination $previousRoot
    }
    Copy-Item -LiteralPath $archive -Destination $publishedArchive -Force
}

$shortcutPath = Join-Path $releaseRoot 'Qubit Sequencer.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $published 'QubitSequencer.exe'
$shortcut.WorkingDirectory = $published
$shortcut.IconLocation = "$published\QubitSequencer.exe,0"
$shortcut.Description = 'Qubit Sequencer 最新本地编译版'
$shortcut.Save()

@(
    "Version: $Version",
    "Built: $BuildStamp",
    "EXE: $published\QubitSequencer.exe",
    "ZIP: $publishedArchive",
    "EXE SHA256: $exeHash"
) | Set-Content -LiteralPath (Join-Path $releaseRoot 'LATEST.txt') -Encoding utf8
@(
    "$((Get-FileHash -LiteralPath $publishedArchive -Algorithm SHA256).Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($publishedArchive))",
    "$((Get-FileHash -LiteralPath (Join-Path $published 'user-guide.pdf') -Algorithm SHA256).Hash.ToLowerInvariant())  user-guide.pdf"
) | Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ascii

Write-Host "Published $publishedArchive"
Write-Host "Fixed EXE: $published\QubitSequencer.exe"
Write-Host "Shortcut: $shortcutPath"
