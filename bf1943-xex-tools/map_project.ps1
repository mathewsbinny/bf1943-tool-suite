param([switch]$ValidateOnly, [ValidatePattern('^[A-Za-z0-9_]+\.java$')][string]$Script = 'MapXex.java')
$ErrorActionPreference = 'Stop'
$projectRoot = (& "$PSScriptRoot/workspace.ps1")
$pilotRoot = "$projectRoot/work/xex-analysis"
# the pinned Java and Ghidra installs (BF1943_JAVA and BF1943_GHIDRA name others: the tests' stand-ins)
$java = if ($env:BF1943_JAVA) { $env:BF1943_JAVA } else { 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' }
$ghidra = if ($env:BF1943_GHIDRA) { $env:BF1943_GHIDRA } else { 'C:/Tools/ghidra_12.1.4_PUBLIC' }
$pilotArgs = @("-Duser.home=$pilotRoot/home", "-Djava.io.tmpdir=$pilotRoot/tmp", "-Dapplication.tempdir=$pilotRoot/tmp", "-Dapplication.cachedir=$pilotRoot/cache", "-Dapplication.settingsdir=$pilotRoot/settings", '-Djava.system.class.loader=ghidra.GhidraClassLoader', '-cp', "$ghidra/Ghidra/Framework/Utility/lib/Utility.jar", 'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless', "$pilotRoot/project", 'BF1943-entry-pilot', '-process', 'default.memory.bin', '-noanalysis', '-scriptPath', $PSScriptRoot, '-postScript', 'MapXex.java', $pilotRoot, '-log', "$pilotRoot/mapping-ghidra.log", '-scriptlog', "$pilotRoot/mapping-script.log")
$pilotArgs[$pilotArgs.IndexOf('MapXex.java')] = $Script
if ($ValidateOnly) {
    $pilotArgs[$pilotArgs.IndexOf($Script)] = 'ValidateXex.java'
    $pilotArgs += '-readOnly'
}
$logStem = if ($ValidateOnly) { 'ValidateXex' } else { [IO.Path]::GetFileNameWithoutExtension($Script) }
$pilotArgs[$pilotArgs.IndexOf('-log') + 1] = "$pilotRoot/$logStem-ghidra.log"
$pilotArgs[$pilotArgs.IndexOf('-scriptlog') + 1] = "$pilotRoot/$logStem-script.log"
# Ghidra's headless analyzer exits 0 when a post-script throws, so the exit code alone cannot tell a failed script:
# the script's own result must be written by this run, and whole. A result left from an earlier run is removed first,
# so a failure cannot leave an old PASS behind for record_batch_decompile.py. The scripts whose result this can confirm:
# - ValidateXex.java (-ValidateOnly): saved-validation.txt, first line PASS (written only on a pass);
# - MapXex.java: mapping-report.txt;
# - ApplyHelpers.java: helper-application.txt, ending with its "function count=" line (written last);
# - InspectHelpers.java: helper-disassembly.tsv, its header and 411 rows (the six helpers' 1,644 bytes).
# Any other script is refused before Ghidra starts (2026-10-31; before, its failure passed as success): its result
# cannot be confirmed here, and DumpCode, EntryPilot, InspectMainFunctions and ExperimentDecompile also need arguments
# that map_project.ps1 does not pass. Run those from Ghidra's script manager or headless by hand.
$checks = @{
    'ValidateXex.java' = @{ file = 'saved-validation.txt'; first = '^PASS ' }
    'MapXex.java' = @{ file = 'mapping-report.txt' }
    'ApplyHelpers.java' = @{ file = 'helper-application.txt'; last = '^function count=' }
    'InspectHelpers.java' = @{ file = 'helper-disassembly.tsv'; lines = 412 }
}
$checked = if ($ValidateOnly) { 'ValidateXex.java' } else { $Script }
if (!$checks.ContainsKey($checked)) {
    throw "map_project.ps1 cannot confirm $checked's result (Ghidra exits 0 when a script throws): it runs only $(($checks.Keys | Sort-Object) -join ', ')"
}
$check = $checks[$checked]
$result = "$pilotRoot/$($check.file)"
if (Test-Path -LiteralPath $result) { Remove-Item -LiteralPath $result }
$started = Get-Date
$ErrorActionPreference = 'Continue'
& $java @pilotArgs *> "$pilotRoot/$logStem-console.log"
$exit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($exit -ne 0) { throw "Ghidra failed; inspect $pilotRoot/$logStem-console.log" }
if (!(Test-Path -LiteralPath $result) -or (Get-Item -LiteralPath $result).LastWriteTime -lt $started) {
    throw "$logStem did not complete (no $result from this run); inspect $pilotRoot/$logStem-script.log"
}
$lines = @(Get-Content -LiteralPath $result)
if ($check.first -and !($lines.Count -and $lines[0] -match $check.first)) {
    throw "$logStem did not pass: $(if ($lines.Count) { $lines[0] } else { 'an empty result' })"
}
if ($check.last -and !($lines.Count -and $lines[-1] -match $check.last)) {
    throw "$logStem did not finish: $result ends before its last line; inspect $pilotRoot/$logStem-script.log"
}
if ($check.lines -and $lines.Count -ne $check.lines) {
    throw "$logStem did not finish: $result has $($lines.Count) lines, not $($check.lines); inspect $pilotRoot/$logStem-script.log"
}
