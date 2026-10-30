param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$Session,
    [string]$GpuFrames = '',
    [switch]$FunctionTrace,
    [ValidateRange(0,3)][int]$LogLevel = 3,
    [switch]$NoShaderDump,
    [ValidateSet('', 'd3d12', 'vulkan')][string]$Gpu = '',
    [string]$GuestCommandLine = '',
    [int]$StopAfterSeconds = 0,
    # Xenia's own build instead of the kit's network-fixed copy
    [switch]$NoNetFix,
    # the Xenia kit's output folder (its build's kit-manifest.json); default bf1943-xenia-kit\output
    [string]$KitOut = ''
)
# Launch the Xenia kit's BF1943 build (bf1943-xenia-kit: output\kit-manifest.json) with Xenia's traces and logs
# redirected into a new session folder, <suite>\work\xenia-probes\<time>-<session> (BF1943_WORK overrides <suite>\work).
# The kit's verify runs first and after the session: the title folder, Xenia and the patch files must match the
# build. Writes session.json (flags) up front and manifest.json (outputs, hashes, exit) when Xenia exits. Options:
# -FunctionTrace (Xenia's function trace and coverage: parse_function_trace.py reads it), -GpuFrames, -LogLevel,
# -Gpu, -GuestCommandLine (the title's command line), -StopAfterSeconds, -NoShaderDump, -NoNetFix, -KitOut.
$ErrorActionPreference = 'Stop'
$suite = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$kit = "$suite/bf1943-xenia-kit"
if (!$KitOut) { $KitOut = "$kit/output" }
$manifestPath = "$KitOut/kit-manifest.json"
if (!(Test-Path -LiteralPath $manifestPath)) { throw "No Xenia kit build ($manifestPath): build it first (python xeniakit.py build <package> <xenia folder>)" }
$m = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
function Get-Sha256($path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }
function Test-Kit { & python -B "$kit/xeniakit.py" verify --out $KitOut | Out-Host; if ($LASTEXITCODE -ne 0) { throw 'The Xenia kit build differs from its manifest (xeniakit.py verify)' } }
# Start-Process joins its arguments with spaces and quotes none of them: each value is quoted here, so that a path
# with a space (BF1943_WORK, the suite's folder) stays one argument
function Quote-Argument($flag) {
    if ($flag -match '"') { throw "An argument holds a double quote, which cannot be passed on: $flag" }
    if ($flag -match '^(--[A-Za-z0-9_]+=)(.*)$') { return "$($Matches[1])`"$($Matches[2])`"" }
    return "`"$flag`""
}
Test-Kit
$xenia = if ($NoNetFix -or -not $m.netfix) { "$($m.xenia)/xenia_canary.exe" } else { $m.netfix }
$title = "$($m.title)/default.xex"
$work = if ($env:BF1943_WORK) { $env:BF1943_WORK } else { "$suite/work" }
$dir = "$work/xenia-probes/$(Get-Date -Format 'yyyyMMdd-HHmmss')-$Session"
if (Test-Path $dir) { throw "Session folder exists: $dir" }
New-Item -ItemType Directory -Force "$dir/gpu", "$dir/shaders" | Out-Null
$flags = @(
    "--log_file=$dir/xenia.log", "--log_level=$LogLevel", '--flush_log=true', "--trace_gpu_prefix=$dir/gpu/",
    # your own full-version copy: Xenia cannot validate XBLA licenses, so the first (full) license is enabled, as the kit's launch does
    '--license_mask=1'
)
if (!$NoShaderDump) { $flags += "--dump_shaders=$dir/shaders" }
if ($Gpu) { $flags += "--gpu=$Gpu" }
if ($GuestCommandLine) { $flags += "--cl=$GuestCommandLine" }
if ($GpuFrames) { $flags += "--trace_gpu_frames=$GpuFrames" }
if ($FunctionTrace) { $flags += @('--trace_functions=true', '--trace_function_coverage=true', "--trace_function_data_path=$dir/functions.trace") }
$arguments = @($flags + $title | ForEach-Object { Quote-Argument $_ })
$record = [ordered]@{ session = $Session; started = (Get-Date).ToString('o'); xenia = $xenia; xenia_sha256 = (Get-Sha256 $xenia)
    title = $title; title_sha256 = (Get-Sha256 $title); kit_manifest = $manifestPath; kit_built = $m.built; flags = $flags }
$record | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 "$dir/session.json"

$process = Start-Process -FilePath $xenia -ArgumentList $arguments -WorkingDirectory (Split-Path $xenia) -PassThru
if ($StopAfterSeconds -gt 0) {
    if (!$process.WaitForExit($StopAfterSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()      # the kill is asynchronous: ExitCode throws until the process has exited
        $record.stopped_by_probe = $true
    }
} else { $process.WaitForExit() }

$record.ended = (Get-Date).ToString('o')
$record.exit_code = $process.ExitCode
$record.title_sha256_after = Get-Sha256 $title
$record.outputs = @(Get-ChildItem -LiteralPath $dir -Recurse -File | Where-Object Name -ne 'manifest.json' | ForEach-Object {
    [ordered]@{ path = $_.FullName.Substring($dir.Length + 1); bytes = $_.Length; sha256 = (Get-Sha256 $_.FullName) } })
$record | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$dir/manifest.json"
if ($record.title_sha256_after -ne $record.title_sha256) { throw 'The title changed during the session' }
Test-Kit
"session $dir"
