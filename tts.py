# -*- coding: utf-8 -*-
"""
Общий модуль TTS: Microsoft Edge Neural voices через edge-tts + pygame.
Используется engine.py (подтверждение команд) и autopilot.py (ответы агента).
"""
import os
import asyncio
import tempfile
import threading

_lock = threading.Lock()   # только один TTS за раз

VOICES = {
    "Светлана (жен.)":  "ru-RU-SvetlanaNeural",
    "Дарья (жен.)":     "ru-RU-DariyaNeural",
    "Дмитрий (муж.)":   "ru-RU-DmitryNeural",
}
DEFAULT_VOICE = "ru-RU-SvetlanaNeural"


def speak(text: str, voice: str = DEFAULT_VOICE):
    """Запустить озвучку в фоновом потоке (не блокирует вызывающий код)."""
    text = (text or "").strip()[:600]
    if not text:
        return
    threading.Thread(target=_run, args=(text, voice), daemon=True).start()


def _run(text: str, voice: str):
    with _lock:
        try:
            asyncio.run(_tts_async(text, voice))
        except Exception:
            pass


async def _tts_async(text: str, voice: str):
    import edge_tts
    import pygame

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, prefix="vox_") as f:
        tmp = f.name
    try:
        await edge_tts.Communicate(text, voice).save(tmp)

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.stop()
        pygame.mixer.music.load(tmp)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
