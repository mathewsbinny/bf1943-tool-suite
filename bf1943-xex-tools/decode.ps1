param([string]$Source = 'work/extracted/default.xex', [string]$Destination = 'work/xex-analysis/default.checked.memory.bin')
$ErrorActionPreference = 'Stop'
$projectRoot = (& "$PSScriptRoot/workspace.ps1")
Set-Location $projectRoot
$javaBin = 'C:/Program Files/Java/jdk-26.0.2/bin'
$ghidraJars = (Get-ChildItem C:/Tools/ghidra_12.1.4_PUBLIC/Ghidra -Recurse -Filter *.jar).FullName -join ';'
$classes = "$projectRoot/work/xex-analysis/classes"
New-Item -ItemType Directory -Force $classes | Out-Null
$sources = (Get-ChildItem work/tools/XEXLoaderWV/XEXLoaderWV/src/main/java/xexloaderwv -Filter *.java).FullName
& "$javaBin/javac.exe" -cp $ghidraJars -d $classes $sources "$PSScriptRoot/DecodeXex.java"
if ($LASTEXITCODE -ne 0) { throw 'Compilation failed' }
& "$javaBin/java.exe" -cp "$ghidraJars;$classes" DecodeXex $Source $Destination
if ($LASTEXITCODE -ne 0) { throw 'Decode failed (existing output is never overwritten)' }
