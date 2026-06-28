# -*- coding: utf-8 -*-
"""
Автопилот: локальный LLM-агент (Ollama) с вызовом инструментов.

Принимает фразу на естественном языке («открой ютуб и сделай погромче»),
просит модель решить, какие действия выполнить, и выполняет их через те же
примитивы, что и обычные команды движка (url/run/keys/text).

Полностью офлайн на твоей видеокарте через Ollama. Отдельная функция:
включается словом-обращением, не мешает обычной диктовке и словарю команд.
"""

import os
import json
import time
import subprocess
import threading
import urllib.request
import urllib.error

# Описание инструментов для модели (OpenAI-style, как ждёт Ollama).
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Открыть сайт или веб-страницу в браузере по умолчанию.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Полный URL, например https://youtube.com"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Найти что-то в интернете: открывает поиск Google с запросом.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос на любом языке"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_app",
            "description": "Запустить программу Windows. Примеры значений: notepad, explorer, msedge, calc, cmd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "Имя программы или исполняемого файла"}
                },
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_keys",
            "description": (
                "Нажать сочетание клавиш или мультимедийную клавишу. "
                "Примеры: 'ctrl+c', 'ctrl+v', 'alt+f4', 'alt+tab', 'volume up', "
                "'volume down', 'volume mute', 'play/pause media', 'next track', "
                "'windows+down', 'windows+shift+s'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Сочетание клавиш"},
                    "repeat": {"type": "integer", "description": "Сколько раз нажать (по умолчанию 1)"},
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Напечатать (вставить) текст в активное окно.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Текст для вставки"}
                },
                "required": ["text"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "Ты — голосовой автопилот на компьютере с Windows. Пользователь говорит "
    "по-русски, что нужно сделать, а ты выполняешь это, вызывая доступные "
    "инструменты. Разбивай сложные просьбы на несколько вызовов инструментов. "
    "Не объясняй и не задавай вопросов — просто выполняй действия инструментами. "
    "Если просьба не требует действий на компьютере, ответь одним коротким "
    "предложением. Известные сайты: ютуб=https://youtube.com, "
    "гугл=https://google.com, яндекс=https://ya.ru, гитхаб=https://github.com, "
    "клод=https://claude.ai, чат/чатгпт=https://chatgpt.com."
)

# Понятные названия инструментов для лога.
TOOL_LABELS = {
    "open_url": "открыть сайт",
    "web_search": "поиск",
    "run_app": "запустить",
    "press_keys": "клавиши",
    "type_text": "напечатать",
}


class AutopilotAgent:
    """LLM-агент на Ollama, выполняющий действия через примитивы движка.

    executors — словарь колбэков, переиспользующих run_command движка:
      open_url(url), web_search(query), run_app(app),
      press_keys(keys, repeat), type_text(text)
    emit(event, payload) — отправка событий status/log в UI.
    """

    def __init__(self, cfg, executors, emit):
        self.cfg = cfg or {}
        self.executors = executors
        self.emit = emit
        self._history = []  # rolling conversation history (clean user/assistant pairs)

    def clear_history(self):
        self._history = []

    def _build_system(self) -> str:
        """Системный промпт + контекст текущего состояния экрана."""
        import datetime
        ctx = "\nТекущее время: " + datetime.datetime.now().strftime("%d.%m.%Y %H:%M") + "."
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(
                ctypes.windll.user32.GetForegroundWindow(), buf, 256)
            win = buf.value.strip()
            if win and win != "Vox":
                ctx += f" Активное окно: «{win}»."
        except Exception:
            pass
        return SYSTEM_PROMPT + ctx

    @staticmethod
    def _is_garbage(text: str) -> bool:
        """Проверить, является ли ответ моделью мусором (повторяющиеся символы и т.п.)."""
        if not text or len(text) < 4:
            return False
        from collections import Counter
        counts = Counter(text.replace(" ", ""))
        if not counts:
            return False
        top_ratio = counts.most_common(1)[0][1] / max(len(text), 1)
        return top_ratio > 0.45  # >45% одного символа — деградация

    def _log(self, kind, text, ms=None):
        self.emit("log", {"kind": kind, "text": text, "ms": ms})

    def _status(self, state, text=""):
        self.emit("status", {"state": state, "text": text})

    def provider(self):
        return (self.cfg.get("provider") or "ollama").lower()

    def _chat(self, model, messages, tools):
        """Вернуть message-объект ответа. Провайдер: ollama (локально) или groq (облако)."""
        if self.provider() == "groq":
            return self._chat_groq(model, messages, tools)
        return self._chat_ollama(model, messages, tools)

    def _chat_groq(self, model, messages, tools):
        """Запрос к Groq (OpenAI-совместимый). Быстрое облачное распознавание намерений."""
        key = self.cfg.get("api_key") or os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise RuntimeError("не задан API-ключ Groq (поле api_key в настройках или GROQ_API_KEY)")
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.6,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key,
                     # Без явного User-Agent Cloudflare у Groq банит urllib (403/1010).
                     "User-Agent": "WhisperDictation/1.0 (+python-urllib)"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode("utf-8"))
                return data["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:  # лимит запросов — короткая пауза
                    time.sleep(2)
                    continue
                raise

    def _chat_ollama(self, model, messages, tools):
        """Запрос к Ollama /api/chat через urllib (без зависимости от пакета ollama)."""
        host = (self.cfg.get("host") or "http://localhost:11434").rstrip("/")
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": "10m",
        }).encode("utf-8")
        req = urllib.request.Request(
            host + "/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        # 503 = модель ещё грузится в видеопамять; повторяем несколько раз.
        last = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read().decode("utf-8"))["message"]
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 503:
                    if attempt == 0:
                        self._status("thinking", "Загружаю модель…")
                    time.sleep(3)
                    continue
                raise
        raise last

    def _execute(self, name, args):
        """Выполнить инструмент через примитивы движка. Вернуть строку-результат."""
        try:
            if name == "open_url":
                self.executors["open_url"](args.get("url", ""))
                return f"открыт {args.get('url')}"
            if name == "web_search":
                self.executors["web_search"](args.get("query", ""))
                return f"поиск: {args.get('query')}"
            if name == "run_app":
                self.executors["run_app"](args.get("app", ""))
                return f"запущено: {args.get('app')}"
            if name == "press_keys":
                repeat = int(args.get("repeat", 1) or 1)
                self.executors["press_keys"](args.get("keys", ""), repeat)
                return f"нажато: {args.get('keys')}"
            if name == "type_text":
                self.executors["type_text"](args.get("text", ""))
                return "напечатано"
            return f"неизвестный инструмент: {name}"
        except Exception as e:
            return f"ошибка: {e}"

    def handle(self, text):
        """Обработать запрос пользователя через агентный цикл."""
        is_groq = self.provider() == "groq"
        default_model = "llama-3.3-70b-versatile" if is_groq else "qwen2.5:7b"
        model = self.cfg.get("model") or default_model
        max_iters = int(self.cfg.get("max_iterations", 5) or 5)

        self._status("thinking", "Думаю…")
        self._log("autopilot", f"запрос: {text}")

        messages = [{"role": "system", "content": self._build_system()}]
        messages.extend(self._history[-16:])  # последние 8 пар user/assistant
        messages.append({"role": "user", "content": text})

        did_action = False
        final_reply = None  # финальный текстовый ответ модели для сохранения в историю
        seen = set()  # против зацикливания: каждое действие выполняем один раз
        for _ in range(max_iters):
            try:
                msg = self._chat(model, messages, TOOLS)
            except urllib.error.HTTPError as e:
                if is_groq and e.code in (401, 403):
                    self._log("info", "Автопилот: Groq отклонил запрос "
                              f"({e.code}) — ключ недействителен. Обнови API-ключ Groq в настройках.")
                else:
                    self._log("info", f"Автопилот: ошибка модели (HTTP {e.code}).")
                break
            except Exception as e:
                where = "Groq" if is_groq else f"Ollama ({self.cfg.get('host')})"
                self._log("info", f"Автопилот: нет связи с {where} ({e}).")
                break

            content = self._field(msg, "content") or ""
            tool_calls = self._field(msg, "tool_calls") or []

            # Сохраняем ход ассистента в историю (формат подходит обоим провайдерам).
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            if not tool_calls:
                if content.strip():
                    final_reply = content.strip()
                    if self._is_garbage(final_reply):
                        # Деградация модели — сбрасываем историю чтобы не отравлять след. запросы.
                        self._history.clear()
                        self._log("info", "Автопилот: получен некорректный ответ, контекст сброшен.")
                        final_reply = None
                    else:
                        self._log("reply", final_reply)
                        if self.cfg.get("tts_enabled"):
                            threading.Thread(target=self._speak, args=(final_reply,), daemon=True).start()
                break

            new_action = False
            for call in tool_calls:
                fn = self._field(call, "function") or {}
                name = self._field(fn, "name") or ""
                raw_args = self._field(fn, "arguments")
                args = self._parse_args(raw_args)
                key = name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    result = "уже выполнено"  # дубликат — не повторяем действие
                else:
                    seen.add(key)
                    label = TOOL_LABELS.get(name, name)
                    detail = args.get("url") or args.get("query") or args.get("app") \
                        or args.get("keys") or args.get("text") or ""
                    self._log("autopilot", f"{label}: {detail}".strip(": "))
                    result = self._execute(name, args)
                    did_action = True
                    new_action = True
                tr = {"role": "tool", "content": result}
                if is_groq:
                    tr["tool_call_id"] = self._field(call, "id")  # OpenAI требует id
                messages.append(tr)

            # Модель только повторяет уже сделанное → завершаем.
            if not new_action:
                break

        if not did_action:
            self._log("info", "Автопилот: подходящих действий не найдено.")
        self._status("ready", "")

        # Сохраняем в историю только текстовые реплики (без tool_call деталей).
        # Действия (open_url, run_app и т.п.) в историю не пишем — мелкие модели путаются.
        if final_reply:
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": final_reply})
            if len(self._history) > 16:  # максимум 8 пар
                self._history = self._history[-16:]

    def _speak(self, text: str):
        from tts import speak
        voice = self.cfg.get("tts_voice") or "ru-RU-SvetlanaNeural"
        speak(text, voice)

    @staticmethod
    def _field(obj, name):
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    @staticmethod
    def _parse_args(raw):
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}
