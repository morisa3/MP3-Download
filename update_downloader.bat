@echo off
setlocal
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install --upgrade "yt-dlp[default]"
if errorlevel 1 goto done
.venv\Scripts\python.exe check_environment.py
:done
pause
