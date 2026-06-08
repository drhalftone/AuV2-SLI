@echo off
rem Double-click launcher for the Alchitry Au Flasher PowerShell UI.
rem Runs the script next to this file with the right options for a WinForms app.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0AlchitryFlasher.ps1"
