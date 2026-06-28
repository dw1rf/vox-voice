# -*- coding: utf-8 -*-
"""
TTS через изолированный subprocess (_tts_worker.py).
Запускает отдельный Python-процесс для воспроизведения, чтобы избежать
конфликтов pygame/asyncio внутри pywebview.
"""
import sys
import base64
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_WORKER = ROOT / "_tts_worker.py"

VOICES = {
    "Светлана (жен.)": "ru-RU-SvetlanaNeural",
    "Дарья (жен.)":    "ru-RU-DariyaNeural",
    "Дмитрий (муж.)":  "ru-RU-DmitryNeural",
}
DEFAULT_VOICE = "ru-RU-SvetlanaNeural"

_lock = threading.Lock()   # только один TTS за раз


def speak(text: str, voice: str = DEFAULT_VOICE):
    """Запустить озвучку в фоновом потоке (не блокирует вызывающий код)."""
    text = (text or "").strip()[:600]
    if not text:
        return
    threading.Thread(target=_run, args=(text, voice), daemon=True).start()


def _run(text: str, voice: str):
    with _lock:
        try:
            t64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
            venv_py = ROOT / "venv" / "Scripts" / "python.exe"
            py = str(venv_py) if venv_py.exists() else sys.executable
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            proc = subprocess.Popen(
                [py, str(_WORKER), t64, voice],
                creationflags=flags,
            )
            proc.wait(timeout=30)
        except Exception as e:
            print(f"[TTS] ошибка: {e}", file=sys.stderr)
