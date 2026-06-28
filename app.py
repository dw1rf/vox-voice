# -*- coding: utf-8 -*-
"""
Десктоп-обёртка (UI) для WhisperDictation на pywebview.

Запускает движок распознавания (engine.DictationEngine) и красивый
веб-интерфейс в нативном окне Windows (Edge WebView2). Мост Python<->JS:
  - JS вызывает методы класса Api через window.pywebview.api.*
  - Python шлёт события в UI через window.evaluate_js("window.__emit(...)")
"""

import os
import sys
import json
import ctypes
import threading
import datetime
from pathlib import Path

import webview
import pystray
from PIL import Image, ImageDraw

from engine import (
    DictationEngine, load_config, save_config,
    HOTKEY_CHOICES, MODEL_CHOICES,
)

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

# Единственное окно; нужно колбэку emit для отправки событий в JS.
_window = None
_api_ref = None  # ссылка на Api для перехвата истории


def emit(event, payload):
    """Отправить событие в UI. Безопасно для вызова из фонового потока."""
    if _api_ref is not None:
        _api_ref._on_emit(event, payload)
    elif _window is not None:
        data = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        try:
            _window.evaluate_js(f"window.__emit({data})")
        except Exception:
            pass


class Api:
    """Мост, доступный из JS как window.pywebview.api.*"""

    def __init__(self):
        global _api_ref
        _api_ref = self
        self._history = []
        self.engine = DictationEngine(emit)

    def _on_emit(self, event, payload):
        """Перехватывает события: пишет историю, затем шлёт в JS."""
        if event == "log" and payload.get("kind") in ("text", "command", "autopilot"):
            self._history.insert(0, {
                "kind": payload["kind"],
                "text": payload["text"],
                "ms": payload.get("ms"),
                "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            })
            if len(self._history) > 500:
                self._history.pop()
        if _window is None:
            return
        data = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        try:
            _window.evaluate_js(f"window.__emit({data})")
        except Exception:
            pass

    def get_config(self):
        return {
            "config": load_config(),
            "hotkeyChoices": HOTKEY_CHOICES,
            "modelChoices": MODEL_CHOICES,
        }

    def save_config(self, cfg):
        """cfg приходит из JS как dict. Сохраняем и перечитываем в движке."""
        try:
            save_config(cfg)
            self.engine.cfg = load_config()
            self.engine.reload_config()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_models(self, provider, host):
        """Шорткаты моделей: установленные Ollama-модели или готовый список Groq."""
        try:
            if provider == "groq":
                return {"models": [
                    "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                    "openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct",
                    "qwen/qwen3-32b",
                ]}
            import urllib.request
            host = (host or "http://localhost:11434").rstrip("/")
            with urllib.request.urlopen(host + "/api/tags", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            return {"models": [m.get("name") for m in data.get("models", []) if m.get("name")]}
        except Exception as e:
            return {"models": [], "error": str(e)}

    def get_history(self):
        return self._history

    def clear_history(self):
        self._history.clear()
        return {"ok": True}

    def export_commands(self):
        cfg = load_config()
        return json.dumps(cfg.get("commands", {}), ensure_ascii=False, indent=2)

    def import_commands(self, data):
        try:
            from engine import migrate_commands
            new_cmds = json.loads(data) if isinstance(data, str) else data
            if not isinstance(new_cmds, dict):
                return {"ok": False, "error": "Ожидался объект JSON"}
            cfg = load_config()
            cfg["commands"].update(migrate_commands(new_cmds))
            save_config(cfg)
            self.engine.reload_config()
            return {"ok": True, "count": len(new_cmds)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def start_engine(self):
        self.engine.start()
        return {"ok": True}

    def minimize(self):
        if _window:
            _window.minimize()

    def close(self):
        if _window:
            _window.destroy()
        os._exit(0)


_tray = None


def _tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([3, 3, 61, 61], fill=(198, 242, 78, 255))
    ink = (16, 19, 10, 255)
    d.line([(22, 20), (32, 44)], fill=ink, width=6, joint="curve")
    d.line([(32, 44), (42, 20)], fill=ink, width=6, joint="curve")
    return img


def _start_tray():
    """Иконка в трее: открыть окно / полностью выйти."""
    global _tray

    def on_open(icon, item):
        if _window:
            _window.show()

    def on_quit(icon, item):
        try:
            icon.stop()
        except Exception:
            pass
        try:
            if _window:
                _window.destroy()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Открыть Vox", on_open, default=True),
        pystray.MenuItem("Выход", on_quit),
    )
    _tray = pystray.Icon("Vox", _tray_image(), "Vox — голос + ИИ", menu)
    threading.Thread(target=_tray.run, daemon=True).start()


def _on_closing():
    """Крестик закрывает не программу, а сворачивает её в трей."""
    if _window:
        _window.hide()
    return False  # отменить реальное закрытие


def _ensure_single_instance():
    """Не дать запустить вторую копию: иначе обе ловят F7 → команды дублируются,
    а две модели в видеопамяти конкурируют → распознавание тормозит."""
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "WhisperDictation_SingleInstance")
    ERROR_ALREADY_EXISTS = 183
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(
            0, "Vox уже запущен. Вторая копия не нужна — "
               "иначе команды выполняются дважды и распознавание тормозит.",
            "Vox", 0x40)
        sys.exit(0)
    return mutex  # держим ссылку, чтобы мьютекс жил всё время работы


def main():
    global _window
    _mutex = _ensure_single_instance()  # noqa: F841 — держим до конца процесса
    api = Api()
    _window = webview.create_window(
        "Vox",
        url=str(WEB / "index.html"),
        js_api=api,
        width=1080,
        height=720,
        min_size=(900, 600),
        background_color="#0b0b0c",
        frameless=False,
        easy_drag=False,
    )

    def on_loaded():
        # Стартуем движок после загрузки UI, чтобы первые события дошли.
        threading.Thread(target=api.engine.start, daemon=True).start()

    _window.events.loaded += on_loaded
    _window.events.closing += _on_closing  # крестик → в трей
    _start_tray()
    webview.start()


if __name__ == "__main__":
    main()
