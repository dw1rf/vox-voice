@echo off
chcp 65001 >nul
title Russian Whisper Dictation
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -u "%~dp0dictate.py"
echo.
echo Stopped. Press any key to close.
pause >nul
