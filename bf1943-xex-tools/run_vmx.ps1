param([switch]$CallerOnly)
$ErrorActionPreference='Stop'
$root=(& "$PSScriptRoot/workspace.ps1")
$vmxRoot="$root/work/vmx128"
foreach($dir in @('home','settings','cache','tmp','project','scripts')){New-Item -ItemType Directory -Force "$vmxRoot/$dir" | Out-Null}
$suites=[ordered]@{'TestVmx.java'='validation.txt';'TestLogical.java'='logical-validation.txt';'TestPermutation.java'='permutation-validation.txt';'TestRlimi.java'='rlimi-validation.txt';'TestUnaligned.java'='unaligned-validation.txt';'TestDecodeCoverage.java'='decode-coverage.txt'}
foreach($script in @($suites.Keys)+'InspectVmxCaller.java'){Copy-Item -LiteralPath "$PSScriptRoot/$script" -Destination "$vmxRoot/scripts/$script"}
$javaArgs=@("-Duser.home=$vmxRoot/home","-Dapplication.settingsdir=$vmxRoot/settings","-Dapplication.cachedir=$vmxRoot/cache","-Djava.io.tmpdir=$vmxRoot/tmp","-Dghidra.external.modules=$vmxRoot/XenonVMX128",'-Djava.system.class.loader=ghidra.GhidraClassLoader','-cp','C:/Tools/ghidra_12.1.4_PUBLIC/Ghidra/Framework/Utility/lib/Utility.jar','ghidra.Ghidra')
& 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' @javaArgs ghidra.pcodeCPort.slgh_compile.SleighCompileLauncher "$vmxRoot/XenonVMX128/data/languages/xenon.slaspec" *> "$vmxRoot/compile.log"
if($LASTEXITCODE -ne 0){throw 'SLEIGH compile failed'}
$javaArgs+=@('ghidra.app.util.headless.AnalyzeHeadless',"$vmxRoot/project",'BF1943-VMX128')
if(Test-Path "$vmxRoot/project/BF1943-VMX128.gpr"){$javaArgs+=@('-process','default.checked.memory.bin')}
else{$javaArgs+=@('-import',"$root/work/xex-analysis/default.checked.memory.bin",'-loader','BinaryLoader','-loader-baseAddr','0x82000000','-processor','PowerPC:BE:64:XenonVMX128')}
$javaArgs+=@('-noanalysis','-scriptPath',"$vmxRoot/scripts",'-log',"$vmxRoot/analysis.log",'-scriptlog',"$vmxRoot/script.log")
if(!$CallerOnly){foreach($script in $suites.Keys){$javaArgs+=@('-postScript',$script,$vmxRoot)}}
$javaArgs+=@('-postScript','InspectVmxCaller.java',$vmxRoot)
$ErrorActionPreference='Continue'
$testStarted=Get-Date
& 'C:/Program Files/Java/jdk-26.0.2/bin/java.exe' @javaArgs *> "$vmxRoot/console.log"
if($LASTEXITCODE -ne 0){throw 'VMX analysis failed'}
if(!$CallerOnly){
    foreach($result in $suites.Values){
        if(!(Test-Path "$vmxRoot/$result") -or (Get-Item "$vmxRoot/$result").LastWriteTime -lt $testStarted -or !(Select-String -Path "$vmxRoot/$result" -Pattern '^PASS COMPLETE')){throw "$result did not complete; inspect console.log"}
    }
}
if(!(Test-Path "$vmxRoot/caller-validation.txt") -or (Get-Item "$vmxRoot/caller-validation.txt").LastWriteTime -lt $testStarted){throw 'Caller probe did not complete'}
if(!(Test-Path "$vmxRoot/permutation-caller-validation.txt") -or (Get-Item "$vmxRoot/permutation-caller-validation.txt").LastWriteTime -lt $testStarted){throw 'Permutation caller probe did not complete'}
