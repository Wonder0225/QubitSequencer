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

function Remove-ReleaseArtifact([string]$Path) {
    $target = Assert-ReleasePath $Path
    if (-not (Test-Path -LiteralPath $target)) { return }
    $rootItem = Get-Item -LiteralPath $target -Force
    $items = @($rootItem)
    if ($rootItem.PSIsContainer) {
        $items += @(Get-ChildItem -LiteralPath $target -Recurse -Force)
    }
    foreach ($item in $items) {
        if ($item.LinkType) { throw "Refusing to remove a linked release artifact: $($item.FullName)" }
    }
    foreach ($item in $items) {
        if (-not $item.PSIsContainer -and $item.Name -like '*.qchip*') {
            $chipHash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
            if ($item.Name -ne 'surface_d3_complete.qchip' -or $chipHash -ne $packagedExampleHash) {
                $recoveryDirectory = Join-Path $projectRoot 'projects/recovered-from-releases'
                New-Item -ItemType Directory -Path $recoveryDirectory -Force | Out-Null
                $recovered = Join-Path $recoveryDirectory $item.Name
                if (Test-Path -LiteralPath $recovered) {
                    if ((Get-FileHash -LiteralPath $recovered -Algorithm SHA256).Hash -eq $chipHash) { continue }
                    $suffix = [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
                    $recovered = Join-Path $recoveryDirectory "$($item.BaseName)-$suffix$($item.Extension)"
                }
                Copy-Item -LiteralPath $item.FullName -Destination $recovered
                if ((Get-FileHash -LiteralPath $recovered -Algorithm SHA256).Hash -ne $chipHash) {
                    throw "Chip project backup verification failed: $recovered"
                }
                Write-Host "Preserved chip data: $recovered"
            }
        }
        # OneDrive marks directories read-only; clear that flag before removal.
        if ($item.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
            $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        }
    }
    Remove-Item -LiteralPath $target -Recurse -Force
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
$packagedExampleHash = (Get-FileHash -LiteralPath (Join-Path $source 'examples/surface_d3_complete.qchip') -Algorithm SHA256).Hash

if ($source -ne $published) {
    if ($published.StartsWith($source.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $source.StartsWith($published.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source and destination directories cannot contain one another'
    }
    Remove-ReleaseArtifact $published
    Move-Item -LiteralPath $source -Destination $published
}
if ($archive -ne $publishedArchive) {
    Remove-ReleaseArtifact $publishedArchive
    Move-Item -LiteralPath $archive -Destination $publishedArchive
}

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

# Only remove known generated release artifacts, preserving other project files.
foreach ($item in @(Get-ChildItem -LiteralPath $releaseRoot -Force)) {
    if ($item.PSIsContainer -and ($item.Name -eq 'builds' -or $item.Name -match '^\d+\.\d+\.\d+$')) {
        Remove-ReleaseArtifact $item.FullName
    } elseif (-not $item.PSIsContainer -and $item.Name -like 'QubitSequencer-*-win64*.zip' -and $item.FullName -ne $publishedArchive) {
        Remove-ReleaseArtifact $item.FullName
    }
}
Write-Host "Published $publishedArchive"
