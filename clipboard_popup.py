# -*- coding: utf-8 -*-
"""
Полупрозрачное всплывающее окно AI-обработки выделенного текста.
Запускается как отдельный subprocess из engine.py.
Аргументы: <text_base64> <config_path>
"""
import sys
import json
import time
import base64
import threading
import tkinter as tk
import urllib.request
import urllib.error
import pyperclip
import keyboard
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ACTIONS = [
    ("Перевести на русский",   "Переведи следующий текст на русский язык. Верни только перевод:\n\n"),
    ("Перефразировать",        "Перефразируй следующий текст другими словами, сохранив смысл. Верни только результат:\n\n"),
    ("Объяснить",              "Объясни простыми словами следующий текст. Отвечай кратко на русском:\n\n"),
    ("Краткое изложение",      "Сделай краткое изложение следующего текста в 2-3 предложениях на русском:\n\n"),
    ("Исправить грамматику",   "Исправь грамматические и стилистические ошибки в тексте. Верни только исправленный текст:\n\n"),
    ("Перевести на английский","Translate the following text to English. Return only the translation:\n\n"),
]

BG      = "#111214"
SURFACE = "#1c1d22"
ACCENT  = "#c6f24e"
TXT     = "#e8e8e4"
MUTED   = "#6a6a72"
BORDER  = "#2b2c32"
WIDTH   = 390


class ClipboardPopup:
    def __init__(self, text: str, cfg: dict):
        self.text   = text
        self.cfg    = cfg
        self.result = None

        r = tk.Tk()
        self.root = r
        r.title("")
        r.overrideredirect(True)
        r.attributes("-alpha", 0.95)
        r.attributes("-topmost", True)
        r.configure(bg=BORDER)
        r.resizable(False, False)

        inner = tk.Frame(r, bg=BG, padx=0, pady=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._build(inner)
        self._position()

        r.bind("<Escape>", lambda e: r.destroy())
        r.bind("<FocusOut>", lambda e: r.after(250, self._check_focus))
        r.after(120, r.focus_force)

    def _build(self, parent):
        # заголовок
        hdr = tk.Frame(parent, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(11, 5))

        tk.Label(hdr, text="AI · Clipboard", fg=ACCENT, bg=BG,
                 font=("Segoe UI", 9, "bold")).pack(side="left")

        preview = self.text[:55].replace("\n", " ")
        if len(self.text) > 55:
            preview += "…"
        tk.Label(hdr, text=f'"{preview}"', fg=MUTED, bg=BG,
                 font=("Segoe UI", 8)).pack(side="left", padx=8)

        tk.Button(hdr, text="✕", fg=MUTED, bg=BG,
                  activebackground=BG, activeforeground="#ff6b6b",
                  bd=0, cursor="hand2", font=("Segoe UI", 11),
                  command=self.root.destroy).pack(side="right")

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")

        # кнопки действий
        btn_wrap = tk.Frame(parent, bg=BG, padx=10, pady=8)
        btn_wrap.pack(fill="x")
        self._action_btns = []
        for label, prompt in ACTIONS:
            b = tk.Button(
                btn_wrap, text=label, anchor="w",
                bg=SURFACE, fg=TXT,
                activebackground=ACCENT, activeforeground="#0b0b0c",
                font=("Segoe UI", 9), bd=0, padx=12, pady=7,
                cursor="hand2", width=45,
                command=lambda p=prompt: self._on_action(p)
            )
            b.pack(fill="x", pady=2)
            b.bind("<Enter>", lambda e, btn=b: btn.config(bg="#262830"))
            b.bind("<Leave>", lambda e, btn=b: btn.config(bg=SURFACE))
            self._action_btns.append(b)

        # область результата (скрыта до нажатия)
        self._result_frame = tk.Frame(parent, bg=BG)

        tk.Frame(self._result_frame, bg=BORDER, height=1).pack(fill="x")

        res_inner = tk.Frame(self._result_frame, bg=BG, padx=12, pady=8)
        res_inner.pack(fill="x")

        self._result_var = tk.StringVar(value="")
        self._result_lbl = tk.Label(
            res_inner, textvariable=self._result_var,
            bg="#0d0e11", fg=TXT,
            font=("Segoe UI", 9),
            wraplength=WIDTH - 50,
            justify="left", anchor="nw",
            padx=10, pady=8, bd=0
        )
        self._result_lbl.pack(fill="x")

        foot = tk.Frame(res_inner, bg=BG)
        foot.pack(fill="x", pady=(7, 0))

        self._paste_btn = tk.Button(
            foot, text="Вставить",
            bg=ACCENT, fg="#0b0b0c",
            activebackground="#d4f75c", activeforeground="#0b0b0c",
            font=("Segoe UI", 9, "bold"), bd=0, padx=14, pady=6,
            cursor="hand2", command=self._paste
        )
        self._paste_btn.pack(side="left")

        tk.Button(
            foot, text="Копировать",
            bg=SURFACE, fg=TXT,
            activebackground="#262830",
            font=("Segoe UI", 9), bd=0, padx=10, pady=6,
            cursor="hand2", command=self._copy_only
        ).pack(side="left", padx=6)

    def _position(self):
        self.root.update_idletasks()
        w = WIDTH
        h = self.root.winfo_reqheight()
        cx = self.root.winfo_pointerx()
        cy = self.root.winfo_pointery()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = min(cx + 14, sw - w - 10)
        y = min(max(cy - 30, 10), sh - h - 40)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _reposition(self):
        self.root.update_idletasks()
        w = WIDTH
        h = self.root.winfo_reqheight()
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        sh = self.root.winfo_screenheight()
        if y + h > sh - 40:
            y = sh - h - 40
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _check_focus(self):
        try:
            if self.root.focus_get() is None:
                self.root.destroy()
        except Exception:
            pass

    def _on_action(self, prompt: str):
        for b in self._action_btns:
            b.config(state="disabled")
        self._result_var.set("⟳  Обрабатываю…")
        self._result_frame.pack(fill="x")
        self.root.update_idletasks()
        self._reposition()
        threading.Thread(target=self._llm_call, args=(prompt,), daemon=True).start()

    def _llm_call(self, prompt: str):
        try:
            res = self._call_llm(prompt + self.text)
        except Exception as e:
            res = f"Ошибка: {e}"
        self.result = res
        self.root.after(0, lambda: self._show_result(res))

    def _call_llm(self, prompt: str) -> str:
        ap = self.cfg.get("autopilot", {})
        provider = (ap.get("provider") or "ollama").lower()
        model = ap.get("model") or ("llama-3.3-70b-versatile" if provider == "groq" else "qwen2.5:7b")
        messages = [
            {"role": "system", "content": "Выполняй задачи точно. Отвечай только результатом, без пояснений."},
            {"role": "user", "content": prompt},
        ]
        if provider == "groq":
            return self._groq(messages, model, ap.get("api_key", ""))
        return self._ollama(messages, model, ap.get("host", "http://localhost:11434"))

    def _groq(self, messages, model, key):
        body = json.dumps({"model": model, "messages": messages, "temperature": 0.3}).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "User-Agent": "Vox/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()

    def _ollama(self, messages, model, host):
        host = host.rstrip("/")
        body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
        req = urllib.request.Request(host + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["message"]["content"].strip()

    def _show_result(self, text: str):
        self._result_var.set(text)
        for b in self._action_btns:
            b.config(state="normal")
        self.root.update_idletasks()
        self._reposition()

    def _paste(self):
        if self.result:
            pyperclip.copy(self.result)
            time.sleep(0.06)
            keyboard.send("ctrl+v")
        self.root.destroy()

    def _copy_only(self):
        if self.result:
            pyperclip.copy(self.result)

    def run(self):
        self.root.mainloop()


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    try:
        text = base64.b64decode(sys.argv[1]).decode("utf-8")
    except Exception:
        text = sys.argv[1]
    config_path = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "config.json")
    cfg = {}
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        pass
    ClipboardPopup(text, cfg).run()


if __name__ == "__main__":
    main()
