@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ██╗   ██╗ ██████╗ ██╗  ██╗    ███████╗███████╗████████╗██╗   ██╗██████╗
echo  ██║   ██║██╔═══██╗╚██╗██╔╝    ██╔════╝██╔════╝╚══██╔══╝██║   ██║██╔══██╗
echo  ██║   ██║██║   ██║ ╚███╔╝     ███████╗█████╗     ██║   ██║   ██║██████╔╝
echo  ╚██╗ ██╔╝██║   ██║ ██╔██╗     ╚════██║██╔══╝     ██║   ██║   ██║██╔═══╝
echo   ╚████╔╝ ╚██████╔╝██╔╝ ██╗    ███████║███████╗   ██║   ╚██████╔╝██║
echo    ╚═══╝   ╚═════╝ ╚═╝  ╚═╝    ╚══════╝╚══════╝   ╚═╝    ╚═════╝ ╚═╝
echo.
echo  Установщик голосового ИИ-ассистента
echo  ─────────────────────────────────────────────────────────────────────────
echo.

REM ── 1. Проверка Python ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python не найден.
    echo      Скачай Python 3.10+ с https://python.org
    echo      При установке поставь галочку "Add Python to PATH"
    echo      Затем запусти setup.bat снова.
    pause & exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
if %PYMAJ% LSS 3 goto :bad_python
if %PYMAJ% EQU 3 if %PYMIN% LSS 10 goto :bad_python
goto :python_ok
:bad_python
echo  [!] Нужен Python 3.10+, найден %PYVER%. Обнови Python.
pause & exit /b 1
:python_ok
echo  [+] Python %PYVER% — OK

REM ── 2. Виртуальное окружение ────────────────────────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo  [*] Создаю виртуальное окружение...
    python -m venv venv
    if errorlevel 1 ( echo  [!] Ошибка создания venv. & pause & exit /b 1 )
    echo  [+] venv создан
) else (
    echo  [+] venv уже существует
)

REM ── 3. Обновление pip ───────────────────────────────────────────────────────
echo  [*] Обновляю pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

REM ── 4. Определение GPU ──────────────────────────────────────────────────────
echo  [*] Проверяю NVIDIA GPU...
set HAS_GPU=0
nvidia-smi >nul 2>&1
if not errorlevel 1 (
    set HAS_GPU=1
    for /f "tokens=*" %%g in ('nvidia-smi --query-gpu=name --format=csv^,noheader 2^>nul') do echo  [+] GPU: %%g
) else (
    echo  [~] NVIDIA GPU не найден — режим CPU (медленнее)
)

REM ── 5. Зависимости ──────────────────────────────────────────────────────────
echo  [*] Устанавливаю зависимости (несколько минут)...
"venv\Scripts\pip.exe" install -r requirements.txt --quiet
if errorlevel 1 (
    echo  [!] Ошибка установки. Проверь интернет-соединение.
    pause & exit /b 1
)
echo  [+] Зависимости установлены

REM ── 6. Модель Whisper ───────────────────────────────────────────────────────
echo.
echo  Какую модель Whisper использовать?
echo.
echo  [1] large-v3       — лучшее качество, GPU 6+ ГБ  (рекомендуется с RTX)
echo  [2] large-v3-turbo — быстрее, GPU 4+ ГБ
echo  [3] medium         — GPU 4 ГБ или мощный CPU
echo  [4] small          — GPU 2 ГБ или средний CPU
echo  [5] Пропустить     — скачается при первом запуске
echo.
set /p MC="  Выбор [1-5, Enter=1]: "
if "%MC%"==""  set MC=1
if "%MC%"=="1" set MODEL=large-v3
if "%MC%"=="2" set MODEL=large-v3-turbo
if "%MC%"=="3" set MODEL=medium
if "%MC%"=="4" set MODEL=small
if "%MC%"=="5" set MODEL=large-v3& goto :skip_download

echo  [*] Скачиваю %MODEL% (может занять 3-10 минут)...
"venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('%MODEL%', device='cpu', compute_type='int8')"
echo  [+] Модель %MODEL% готова
:skip_download

REM ── 7. Начальный config.json ────────────────────────────────────────────────
if not exist "config.json" (
    if %HAS_GPU%==1 ( set CFG_DEV=cuda&  set CFG_CT=float16 ) else ( set CFG_DEV=cpu& set CFG_CT=int8 )
    echo  [*] Создаю config.json...
    (
        echo {
        echo   "model": "%MODEL%",
        echo   "device": "!CFG_DEV!",
        echo   "compute_type": "!CFG_CT!",
        echo   "language": "ru",
        echo   "hotkey": "f7",
        echo   "clipboard_hotkey": "f9"
        echo }
    ) > config.json
    echo  [+] config.json создан
) else (
    echo  [+] config.json уже есть — не трогаю
)

REM ── 8. Ярлык на рабочем столе ───────────────────────────────────────────────
set LINK=%USERPROFILE%\Desktop\Vox.lnk
set TGT=%~dp0WhisperUI.bat
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%LINK%'); $s.TargetPath='%TGT%'; $s.WorkingDirectory='%~dp0'; $s.Description='Vox — голосовой ИИ'; $s.Save()" >nul 2>&1
if exist "%LINK%" ( echo  [+] Ярлык на рабочем столе создан ) else ( echo  [~] Запускай через WhisperUI.bat )

REM ── Готово ──────────────────────────────────────────────────────────────────
echo.
echo  ─────────────────────────────────────────────────────────────────────────
echo  [+] Установка завершена!
echo.
echo  Как пользоваться:
echo    F7  (держать) — диктовка, текст вставится туда где курсор
echo    F9  (при выделенном тексте) — ИИ: перевод, рефрейминг, грамматика
echo    "Клод, открой ютуб" — LLM-автопилот выполнит действие на компьютере
echo.
echo  Для автопилота (необязательно, одно из):
echo    Ollama (офлайн): https://ollama.com  ->  ollama pull qwen2.5:7b
echo    Groq  (облако):  https://console.groq.com  — получи бесплатный ключ
echo  ─────────────────────────────────────────────────────────────────────────
echo.
set /p RUN="  Запустить Vox прямо сейчас? [Y/n]: "
if /i not "%RUN%"=="n" start "" "%~dp0WhisperUI.bat"
endlocal
