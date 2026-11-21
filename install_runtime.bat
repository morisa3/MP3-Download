@echo off
setlocal
winget install --id DenoLand.Deno -e
if errorlevel 1 (
 echo Deno installation failed. See https://docs.deno.com/runtime/getting_started/installation/
 pause
 exit /b 1
)
echo Close this window, then reopen start.bat so the new PATH takes effect.
pause
