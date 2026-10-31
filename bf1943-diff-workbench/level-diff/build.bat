@echo off
rem Builds LevelDiff.exe next to this file with the .NET Framework 4 C# compiler (part of Windows). It reads .dbx
rem files with the suite's DbxEdit code: the sources DbxEdit.rsp lists in ..\..\bf1943-dbx-tools (or set DBX_TOOLS to
rem that folder), compiled from that folder (the compiler reads the list's names relative to the current folder).
setlocal
set HERE=%~dp0
if not defined DBX_TOOLS set DBX_TOOLS=%HERE%..\..\bf1943-dbx-tools
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (echo C# compiler not found: %CSC% & exit /b 1)
if not exist "%DBX_TOOLS%\DbxEdit.rsp" (echo DbxEdit.rsp not found in "%DBX_TOOLS%" - it is in the suite's bf1943-dbx-tools folder; or set DBX_TOOLS & exit /b 1)
pushd "%DBX_TOOLS%" || exit /b 1
"%CSC%" -nologo -optimize -main:LevelDiff -out:"%HERE%LevelDiff.exe" "%HERE%LevelDiff.cs" @DbxEdit.rsp || (popd & exit /b 1)
popd
echo Built LevelDiff.exe
