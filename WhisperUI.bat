@echo off
REM Запуск красивого UI WhisperDictation (без чёрного окна консоли).
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0app.py"
