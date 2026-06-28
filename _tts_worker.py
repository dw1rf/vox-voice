# -*- coding: utf-8 -*-
"""Изолированный worker для воспроизведения TTS. Запускается как subprocess из tts.py."""
import sys
import asyncio
import base64

def main():
    if len(sys.argv) < 3:
        return
    text = base64.b64decode(sys.argv[1]).decode("utf-8")
    voice = sys.argv[2]

    async def _play():
        import edge_tts
        import pygame
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, prefix="vox_") as f:
            tmp = f.name
        try:
            await edge_tts.Communicate(text, voice).save(tmp)
            pygame.mixer.init()
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

    asyncio.run(_play())

if __name__ == "__main__":
    main()
