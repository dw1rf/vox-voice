# -*- coding: utf-8 -*-
"""
Русский голос на Whisper (GPU): команды + диктовка в одном инструменте.

Зажми клавишу (по умолчанию F8), говори, отпусти:
  - если фраза совпала с командой из config.json -> выполняется действие;
  - иначе текст печатается в активное окно (диктовка).

Всё локально/офлайн на видеокарте. Обучать по голосу не нужно.
"""

import os
import re
import sys
import time
import json
import ctypes
import winsound
import threading
import subprocess
import webbrowser
from pathlib import Path

# Небуферизованный вывод, чтобы реакция была видна сразу
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

DEFAULT_CONFIG = {
    "model": "large-v3",
    "device": "cuda",
    "compute_type": "float16",
    "language": "ru",
    "hotkey": "f8",
    "samplerate": 16000,
    "add_trailing_space": True,
    "initial_prompt": "Клод, Яндекс, Гитхаб, Эдж, Ютуб",
    "beam_size": 5,
    "sound_on_command": True,
    "sound_on_dictation": False,
    "commands": {
        "открой клод":        {"type": "url", "value": "https://claude.ai"},
        "открой чат":         {"type": "url", "value": "https://chatgpt.com"},
        "открой ютуб":        {"type": "url", "value": "https://youtube.com"},
        "открой яндекс":      {"type": "url", "value": "https://ya.ru"},
        "открой гитхаб":      {"type": "url", "value": "https://github.com"},
        "открой эдж":         {"type": "run", "value": "msedge"},
        "открой блокнот":     {"type": "run", "value": "notepad"},
        "открой проводник":   {"type": "run", "value": "explorer"},
        "сделай громче":      {"type": "keys", "value": "volume up", "repeat": 3},
        "сделай тише":        {"type": "keys", "value": "volume down", "repeat": 3},
        "без звука":          {"type": "keys", "value": "volume mute"},
        "пауза":              {"type": "keys", "value": "play/pause media"},
        "следующий трек":     {"type": "keys", "value": "next track"},
        "закрой окно":        {"type": "keys", "value": "alt+f4"},
        "сверни окно":        {"type": "keys", "value": "windows+down"},
        "переключи окно":     {"type": "keys", "value": "alt+tab"},
        "скопируй":           {"type": "keys", "value": "ctrl+c"},
        "вставь":             {"type": "keys", "value": "ctrl+v"},
        "отмени":             {"type": "keys", "value": "ctrl+z"},
        "сделай скриншот":    {"type": "keys", "value": "windows+shift+s"},
    },
}


def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    merged = {**DEFAULT_CONFIG, **cfg}
    if "commands" not in cfg:
        merged["commands"] = DEFAULT_CONFIG["commands"]
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


def add_cuda_dll_dirs():
    base = ROOT / "venv" / "Lib" / "site-packages" / "nvidia"
    if not base.exists():
        return
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        b = base / sub / "bin"
        if b.exists():
            os.add_dll_directory(str(b))
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def normalize(text):
    """Привести фразу к виду для сравнения с командами."""
    t = text.lower().strip()
    t = re.sub(r"[.,!?;:\"'()]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Virtual-Key коды для надёжного опроса клавиши через GetAsyncKeyState
VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "space": 0x20, "caps lock": 0x14, "scroll lock": 0x91, "pause": 0x13,
    "right ctrl": 0xA3, "left ctrl": 0xA2, "ctrl": 0x11,
    "right shift": 0xA1, "left shift": 0xA0, "shift": 0x10,
    "right alt": 0xA5, "left alt": 0xA4, "alt": 0x12,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
}


def resolve_vk(name):
    name = str(name).strip().lower()
    if name.startswith("vk:"):
        try:
            return int(name[3:], 0)
        except Exception:
            pass
    return VK_MAP.get(name, 0x77)  # по умолчанию F8


def main():
    add_cuda_dll_dirs()

    import numpy as np
    import sounddevice as sd
    import keyboard
    import pyperclip
    from faster_whisper import WhisperModel

    cfg = load_config()
    commands = {normalize(k): v for k, v in cfg.get("commands", {}).items()}

    print("=" * 60)
    print("  Русский голос (Whisper GPU): команды + диктовка")
    print("=" * 60)
    print(f"  Модель:   {cfg['model']}  ({cfg['device']}/{cfg['compute_type']})")
    print(f"  Клавиша:  зажми [{cfg['hotkey'].upper()}], говори, отпусти")
    print(f"  Команд в конфиге: {len(commands)}")
    print("  Загрузка модели...")

    t0 = time.time()
    try:
        model = WhisperModel(cfg["model"], device=cfg["device"],
                             compute_type=cfg["compute_type"])
    except Exception as e:
        print(f"\n  [!] GPU не сработал ({e}); перехожу на CPU/int8.")
        model = WhisperModel(cfg["model"], device="cpu", compute_type="int8")
    print(f"  Модель готова за {time.time() - t0:.1f} c.\n")

    samplerate = int(cfg["samplerate"])
    frames = []
    recording = threading.Event()
    busy = threading.Lock()

    def audio_callback(indata, frame_count, time_info, status):
        if recording.is_set():
            frames.append(indata.copy())

    stream = sd.InputStream(samplerate=samplerate, channels=1,
                            dtype="float32", callback=audio_callback)
    stream.start()

    def paste_text(text):
        if cfg["add_trailing_space"]:
            text = text + " "
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        time.sleep(0.15)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass

    def run_command(action):
        kind = action.get("type")
        val = action.get("value", "")
        if kind == "url":
            webbrowser.open(val)
        elif kind == "run":
            try:
                os.startfile(val)
            except Exception:
                subprocess.Popen(val, shell=True)
        elif kind == "keys":
            for _ in range(int(action.get("repeat", 1))):
                keyboard.send(val)
                time.sleep(0.05)
        elif kind == "text":
            paste_text(val)

    def play_sound(name):
        path = ROOT / "sounds" / f"{name}.wav"
        if path.exists():
            try:
                winsound.PlaySound(str(path),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass

    def handle(text):
        norm = normalize(text)
        if not norm:
            return
        if norm in commands:
            print(f"  [команда] «{text}»")
            if cfg.get("sound_on_command", True):
                play_sound("command")
            run_command(commands[norm])
        else:
            print(f"  [текст]   «{text}»")
            if cfg.get("sound_on_dictation", False):
                play_sound("dictation")
            paste_text(text.strip())

    def start_rec():
        if recording.is_set():
            return
        frames.clear()
        recording.set()
        print("  ● запись...", flush=True)

    def stop_and_process():
        if not recording.is_set():
            return
        recording.clear()
        if not busy.acquire(blocking=False):
            return
        try:
            if not frames:
                print("  (пусто)")
                return
            audio = np.concatenate(frames, axis=0).flatten().astype(np.float32)
            dur = len(audio) / samplerate
            if dur < 0.3:
                print("  (слишком коротко)")
                return
            print(f"  … распознаю {dur:.1f} c", flush=True)
            t = time.time()
            segments, _ = model.transcribe(
                audio, language=cfg["language"], beam_size=int(cfg["beam_size"]),
                initial_prompt=cfg["initial_prompt"] or None, vad_filter=True,
            )
            text = "".join(seg.text for seg in segments).strip()
            print(f"    распознано за {time.time() - t:.1f} c", flush=True)
            if text:
                handle(text)
            else:
                print("  (ничего не распознано)")
        finally:
            busy.release()

    # Надёжный опрос клавиши через WinAPI GetAsyncKeyState (без хуков)
    user32 = ctypes.windll.user32
    vk = resolve_vk(cfg["hotkey"])

    def is_down():
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    print(f"  ГОТОВО. Зажми [{cfg['hotkey'].upper()}] (vk={hex(vk)}) и говори. Выход: Ctrl+C.\n")
    prev = False
    try:
        while True:
            cur = is_down()
            if cur and not prev:
                start_rec()
            elif not cur and prev:
                stop_and_process()
            prev = cur
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
        print("\n  Остановлено.")


if __name__ == "__main__":
    main()
