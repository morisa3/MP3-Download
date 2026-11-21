@echo off
setlocal
cd /d "%~dp0"
set "EZ_STUDIO_PY=%LOCALAPPDATA%\EZAudioStudio\venv-py312\Scripts\python.exe"
if not exist "%EZ_STUDIO_PY%" (
  echo Run start.bat once first to create the app environment.
  goto done
)
"%EZ_STUDIO_PY%" -m pip install --upgrade "yt-dlp[default]"
if errorlevel 1 goto done
"%EZ_STUDIO_PY%" check_environment.py
:done
pause
