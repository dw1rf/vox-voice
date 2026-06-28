# -*- coding: utf-8 -*-
"""
Всегда-слушающий детектор слова-обращения (wake word).

Использует Whisper tiny на CPU в отдельном потоке — не мешает основной
модели на GPU. Открывает свой InputStream на том же микрофоне (Windows
позволяет несколько shared-режимных потоков одновременно).
"""

import time
import threading
import numpy as np


class WakeWordDetector:
    """Скользящее окно 2 с, шаг 0.7 с, tiny-модель на CPU."""

    CHUNK_SEC  = 2.0    # размер окна транскрипции
    STEP_SEC   = 0.7    # шаг сдвига
    ENERGY_THR = 0.007  # RMS-порог молчания — пропускаем тихие фрагменты

    def __init__(self, wake_words, on_wake, model_size="tiny", samplerate=16000):
        self.wake_words = [w.lower().strip() for w in wake_words if w.strip()]
        self.on_wake    = on_wake        # callback(rest_text: str)
        self.model_size = model_size
        self.samplerate = samplerate
        self._stop   = threading.Event()
        self._paused = threading.Event()  # пауза во время записи основным движком
        self._thread = None

    # ── публичный API ──────────────────────────────────────────────────────────

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._safe_run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def pause(self, seconds: float = 4.0):
        """Временно заглушить детектор (пока движок сам пишет/обрабатывает)."""
        self._paused.set()
        def _resume():
            time.sleep(seconds)
            self._paused.clear()
        threading.Thread(target=_resume, daemon=True).start()

    # ── внутренняя реализация ──────────────────────────────────────────────────

    def _safe_run(self):
        try:
            self._detect_loop()
        except Exception as e:
            print(f"[WakeDetector] ошибка: {e}", flush=True)

    def _detect_loop(self):
        import sounddevice as sd
        from faster_whisper import WhisperModel

        model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

        RATE    = self.samplerate
        chunk_n = int(RATE * self.CHUNK_SEC)
        buf     = np.zeros(chunk_n, dtype=np.float32)
        lock    = threading.Lock()

        def _cb(indata, frames, time_info, status):
            data = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            n = len(data)
            with lock:
                buf[:-n] = buf[n:]   # сдвигаем кольцевой буфер влево
                buf[-n:] = data      # пишем новые сэмплы в конец

        stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="float32",
            blocksize=int(RATE * 0.1), callback=_cb,
        )
        stream.start()
        try:
            while not self._stop.is_set():
                time.sleep(self.STEP_SEC)

                if self._paused.is_set():
                    continue

                with lock:
                    chunk = buf.copy()

                if float(np.sqrt(np.mean(chunk ** 2))) < self.ENERGY_THR:
                    continue

                try:
                    segs, _ = model.transcribe(
                        chunk, language="ru", beam_size=1,
                        vad_filter=True, condition_on_previous_text=False,
                    )
                    text = "".join(s.text for s in segs).strip().lower()
                except Exception:
                    continue

                if not text:
                    continue

                for word in self.wake_words:
                    if word not in text:
                        continue
                    idx  = text.find(word)
                    rest = text[idx + len(word):].lstrip(" ,.!?").strip()
                    self.pause(5.0)
                    self.on_wake(rest)
                    break
        finally:
            stream.stop()
            stream.close()
