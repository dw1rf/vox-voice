# -*- coding: utf-8 -*-
"""
Движок распознавания речи на Whisper (GPU): команды + диктовка.

Вынесен из dictate.py в отдельный класс DictationEngine, чтобы UI мог
запускать/останавливать его и получать события статуса и лога.

Зажми горячую клавишу, говори, отпусти:
  - если фраза совпала с командой из config.json -> выполняется действие;
  - иначе текст печатается в активное окно (диктовка).
"""

import os
import re
import sys
import time
import json
import base64
import ctypes
import winsound
import threading
import subprocess
import webbrowser
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from rapidfuzz import process as _rfprocess, fuzz as _rffuzz
    _FUZZY_OK = True
except ImportError:
    _FUZZY_OK = False

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

DEFAULT_CONFIG = {
    "model": "large-v3",
    "device": "cuda",
    "compute_type": "float16",
    "language": "ru",
    "hotkey": "f7",
    "clipboard_hotkey": "f9",
    "clipboard_hotkey_label": "F9",
    "fuzzy_threshold": 80,
    "samplerate": 16000,
    "add_trailing_space": True,
    "initial_prompt": "Клод, Яндекс, Гитхаб, Эдж, Ютуб",
    "beam_size": 1,
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
    "autopilot": {
        "enabled": True,
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "api_key": "",
        "host": "http://localhost:11434",
        "wake_words": ["клод", "клода", "клот", "слушай"],
        "max_iterations": 5,
        "tts_enabled": False,
    },
}

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

HOTKEY_CHOICES = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
                  "f9", "f10", "f11", "f12", "caps lock", "right ctrl",
                  "right shift", "right alt"]
MODEL_CHOICES = ["tiny", "base", "small", "medium",
                 "large-v2", "large-v3", "large-v3-turbo"]


def migrate_commands(cmds):
    """Привести команды к новому формату: {"actions": [ {type,value,repeat?}, … ], "sound"?}.
    Старый одиночный формат {type, value, repeat?} оборачивается в один шаг."""
    out = {}
    for k, v in (cmds or {}).items():
        if isinstance(v, dict) and isinstance(v.get("actions"), list):
            out[k] = v
        elif isinstance(v, dict) and "type" in v:
            step = {"type": v["type"], "value": v.get("value", "")}
            if "repeat" in v:
                step["repeat"] = v["repeat"]
            out[k] = {"actions": [step]}
        else:
            out[k] = v
    return out


def load_config():
    """Прочитать config.json, дополнить дефолтами, записать обратно."""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    merged = {**DEFAULT_CONFIG, **cfg}
    if "commands" not in cfg:
        merged["commands"] = DEFAULT_CONFIG["commands"]
    merged["commands"] = migrate_commands(merged.get("commands", {}))
    save_config(merged)
    return merged


def save_config(cfg):
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def normalize(text):
    """Привести фразу к виду для сравнения с командами."""
    t = text.lower().strip()
    t = re.sub(r"[.,!?;:\"'()]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def resolve_vk(name):
    name = str(name).strip().lower()
    if name.startswith("vk:"):
        try:
            return int(name[3:], 0)
        except Exception:
            pass
    return VK_MAP.get(name, 0x76)  # по умолчанию F7


def _add_cuda_dll_dirs():
    base = ROOT / "venv" / "Lib" / "site-packages" / "nvidia"
    if not base.exists():
        return
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        b = base / sub / "bin"
        if b.exists():
            os.add_dll_directory(str(b))
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


class DictationEngine:
    """Управляемый движок распознавания.

    emit(event, payload) — колбэк для отправки событий в UI. Возможные события:
      - status: {"state": "loading|ready|recording|transcribing", "text": str}
      - log:    {"kind": "command|text|info", "text": str, "ms": int}
      - stats:  {"device": str, "model": str}
    """

    def __init__(self, emit):
        self.emit = emit
        self.cfg = load_config()
        self.commands = {}
        self._thread = None
        self._stop = threading.Event()
        self._device_used = "—"
        self._recording = threading.Event()
        self._busy = threading.Lock()
        self._frames = []
        self._agent = None  # AutopilotAgent, создаётся в _run

    # ---- публичный API ----------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def reload_config(self):
        """Перечитать config.json (например, после сохранения из UI)."""
        self.cfg = load_config()
        self.commands = {normalize(k): v for k, v in self.cfg.get("commands", {}).items()}
        if self._agent is not None:
            self._agent.cfg = self.cfg.get("autopilot", {})
        self._emit_status("ready", "Конфиг обновлён")

    @staticmethod
    def _match_wake(norm, wake_words):
        """Если фраза начинается со слова-обращения — вернуть остаток, иначе None."""
        for w in wake_words:
            w = normalize(w)
            if not w:
                continue
            if norm == w:
                return ""
            if norm.startswith(w + " "):
                return norm[len(w) + 1:].strip()
        return None

    # ---- внутреннее -------------------------------------------------------

    def _emit_status(self, state, text=""):
        self.emit("status", {"state": state, "text": text})

    def _log(self, kind, text, ms=None):
        self.emit("log", {"kind": kind, "text": text, "ms": ms})

    def _run(self):
        _add_cuda_dll_dirs()
        import numpy as np
        import sounddevice as sd
        import keyboard
        import pyperclip
        from faster_whisper import WhisperModel

        self.commands = {normalize(k): v for k, v in self.cfg.get("commands", {}).items()}

        self._emit_status("loading", "Загрузка модели…")
        t0 = time.time()
        try:
            model = WhisperModel(self.cfg["model"], device=self.cfg["device"],
                                 compute_type=self.cfg["compute_type"])
            self._device_used = self.cfg["device"]
        except Exception as e:
            self._log("info", f"GPU не сработал ({e}); перехожу на CPU.")
            model = WhisperModel(self.cfg["model"], device="cpu", compute_type="int8")
            self._device_used = "cpu"
        self.emit("stats", {"device": self._device_used, "model": self.cfg["model"]})
        self._log("info", f"Модель готова за {time.time() - t0:.1f} c.")

        samplerate = int(self.cfg["samplerate"])

        def audio_callback(indata, frame_count, time_info, status):
            if self._recording.is_set():
                self._frames.append(indata.copy())

        stream = sd.InputStream(samplerate=samplerate, channels=1,
                                dtype="float32", callback=audio_callback)
        stream.start()

        def paste_text(text):
            if self.cfg["add_trailing_space"]:
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
            elif kind == "wait":
                try:
                    time.sleep(int(val) / 1000.0)
                except Exception:
                    pass
            elif kind == "webhook":
                try:
                    payload = json.dumps(action.get("payload", {}), ensure_ascii=False).encode()
                    req = urllib.request.Request(
                        val, data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                except Exception as e:
                    self._log("info", f"Webhook ошибка ({val}): {e}")

        def run_command_set(cmd):
            """Выполнить команду: список действий по порядку (или старый одиночный формат)."""
            actions = cmd.get("actions") if isinstance(cmd, dict) else None
            if actions is None:
                run_command(cmd)
                return
            for a in actions:
                run_command(a)
                time.sleep(0.05)

        def play_sound(name):
            path = ROOT / "sounds" / f"{name}.wav"
            if path.exists():
                try:
                    winsound.PlaySound(
                        str(path),
                        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                except Exception:
                    pass

        def handle(text, ms):
            norm = normalize(text)
            if not norm:
                return
            # Автопилот по слову-обращению («Клод, …»)
            ap = self.cfg.get("autopilot", {})
            if ap.get("enabled") and self._agent is not None:
                rest = self._match_wake(norm, ap.get("wake_words", []))
                if rest is not None:
                    if ap.get("sound_on_command", True) or self.cfg.get("sound_on_command", True):
                        play_sound("command")
                    self._agent.handle(rest or text)
                    return
            matched_key = None
            if norm in self.commands:
                matched_key = norm
            elif _FUZZY_OK and self.commands:
                threshold = int(self.cfg.get("fuzzy_threshold", 80))
                hit = _rfprocess.extractOne(norm, self.commands.keys(),
                                            scorer=_rffuzz.token_sort_ratio)
                if hit and hit[1] >= threshold:
                    matched_key = hit[0]
                    if hit[0] != norm:
                        self._log("info", f"~fuzzy: «{norm}» → «{hit[0]}» ({hit[1]}%)")

            if matched_key is not None:
                cmd = self.commands[matched_key]
                self._log("command", text, ms)
                snd = cmd.get("sound") if isinstance(cmd, dict) else None
                if snd if snd is not None else self.cfg.get("sound_on_command", True):
                    play_sound("command")
                run_command_set(cmd)
            else:
                self._log("text", text, ms)
                if self.cfg.get("sound_on_dictation", False):
                    play_sound("dictation")
                paste_text(text.strip())

        def stop_and_process():
            if not self._recording.is_set():
                return
            self._recording.clear()
            if not self._busy.acquire(blocking=False):
                return
            try:
                if not self._frames:
                    self._emit_status("ready", "")
                    return
                audio = np.concatenate(self._frames, axis=0).flatten().astype(np.float32)
                dur = len(audio) / samplerate
                if dur < 0.3:
                    self._emit_status("ready", "Слишком коротко")
                    return
                self._emit_status("transcribing", f"Распознаю {dur:.1f} c…")
                t = time.time()
                segments, _ = model.transcribe(
                    audio, language=self.cfg["language"],
                    beam_size=int(self.cfg["beam_size"]),
                    initial_prompt=self.cfg["initial_prompt"] or None,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text = "".join(seg.text for seg in segments).strip()
                ms = int((time.time() - t) * 1000)
                if text:
                    handle(text, ms)
                self._emit_status("ready", "")
            finally:
                self._busy.release()

        # Автопилот: LLM-агент, переиспующий примитивы выше.
        ap_cfg = self.cfg.get("autopilot", {})
        if ap_cfg.get("enabled"):
            try:
                from autopilot import AutopilotAgent
                executors = {
                    "open_url": lambda url: run_command({"type": "url", "value": url}),
                    "web_search": lambda q: webbrowser.open(
                        "https://www.google.com/search?q=" + urllib.parse.quote(q)),
                    "run_app": lambda app: run_command({"type": "run", "value": app}),
                    "press_keys": lambda keys, repeat=1: run_command(
                        {"type": "keys", "value": keys, "repeat": repeat}),
                    "type_text": lambda t: paste_text(t),
                }
                self._agent = AutopilotAgent(ap_cfg, executors, self.emit)
                self._log("info", f"Автопилот готов (модель {ap_cfg.get('model')}). "
                                  f"Скажи «{(ap_cfg.get('wake_words') or ['клод'])[0]}, …».")
            except Exception as e:
                self._log("info", f"Автопилот не запущен: {e}")
                self._agent = None

        user32 = ctypes.windll.user32
        vk = resolve_vk(self.cfg["hotkey"])
        clip_vk = resolve_vk(self.cfg.get("clipboard_hotkey", "f9"))

        def is_down():
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def is_clip_down():
            return clip_vk and bool(user32.GetAsyncKeyState(clip_vk) & 0x8000)

        def trigger_clipboard():
            keyboard.send("ctrl+c")
            time.sleep(0.12)
            try:
                text_clip = pyperclip.paste().strip()
            except Exception:
                text_clip = ""
            if not text_clip:
                return
            text_b64 = base64.b64encode(text_clip.encode("utf-8")).decode("ascii")
            venv_py = ROOT / "venv" / "Scripts" / "python.exe"
            py_exec = str(venv_py) if venv_py.exists() else sys.executable
            try:
                subprocess.Popen(
                    [py_exec, str(ROOT / "clipboard_popup.py"), text_b64, str(CONFIG_PATH)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                self._log("info", f"Clipboard popup ошибка: {e}")

        self._emit_status("ready", "")
        hk_label = self.cfg.get("hotkey_label") or str(self.cfg["hotkey"]).upper()
        clip_label = self.cfg.get("clipboard_hotkey_label") or str(self.cfg.get("clipboard_hotkey", "F9")).upper()
        self._log("info", f"Готово. Зажми [{hk_label}] и говори. Clipboard AI: [{clip_label}].")
        prev = False
        clip_prev = False
        try:
            while not self._stop.is_set():
                cur = is_down()
                if cur and not prev:
                    self._frames.clear()
                    self._recording.set()
                    self._emit_status("recording", "Запись…")
                elif not cur and prev:
                    stop_and_process()
                prev = cur

                clip_cur = is_clip_down()
                if clip_cur and not clip_prev:
                    threading.Thread(target=trigger_clipboard, daemon=True).start()
                clip_prev = clip_cur

                time.sleep(0.02)
        finally:
            stream.stop()
            stream.close()
            self._emit_status("stopped", "Остановлено")
