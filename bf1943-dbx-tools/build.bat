@echo off
rem Builds DbxEdit.exe (command line) and DbxEditGUI.exe (window) next to this file with the .NET Framework 4
rem C# compiler that ships with Windows. DbxEdit's sources are listed in DbxEdit.rsp, which the compiler reads
rem relative to the current folder: so this folder is made current while it compiles.
setlocal
set HERE=%~dp0
set CSC=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (echo C# compiler not found: %CSC% & exit /b 1)
pushd "%HERE%" || exit /b 1
"%CSC%" -nologo -optimize -out:"%HERE%DbxEdit.exe" @DbxEdit.rsp || (popd & exit /b 1)
"%CSC%" -nologo -optimize -target:winexe -main:Bfbc2Dbx.Gui.GuiProgram -out:"%HERE%DbxEditGUI.exe" ^
    -r:System.Windows.Forms.dll -r:System.Drawing.dll "%HERE%DbxEditGUI.cs" @DbxEdit.rsp || (popd & exit /b 1)
popd
echo Built DbxEdit.exe and DbxEditGUI.exe
