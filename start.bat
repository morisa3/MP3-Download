@echo off
setlocal
cd /d "%~dp0"
if not defined LOCALAPPDATA (
  echo LOCALAPPDATA is not available.
  goto failed
)
set "EZ_STUDIO_VENV=%LOCALAPPDATA%\EZAudioStudio\venv-py312"
if not exist "%EZ_STUDIO_VENV%\Scripts\python.exe" (
  py -3.12 -m venv "%EZ_STUDIO_VENV%"
  if errorlevel 1 goto failed
)
"%EZ_STUDIO_VENV%\Scripts\python.exe" bootstrap.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Could not start EZ Audio Studio. Check Python 3.12 and the messages above.
pause
exit /b 1
