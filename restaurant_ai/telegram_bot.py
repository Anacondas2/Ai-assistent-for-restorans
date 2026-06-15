"""Канал Telegram для агента.

Использует только стандартную библиотеку (urllib) — без платных/тяжёлых
зависимостей. Сетевая часть (опрос Telegram, отправка) отделена от логики:
сообщения обрабатывает переданный ``handler`` — это удобно тестировать.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Callable

API = "https://api.telegram.org/bot{token}/{method}"

# handler(chat_id, text) -> текст ответа гостю
Handler = Callable[[str, str], str]


class TelegramBot:
    def __init__(self, token: str, handler: Handler, *,
                 start_message: str | None = None,
                 on_start: Callable[[str], None] | None = None):
        self.token = token
        self.handler = handler
        self.start_message = start_message
        self.on_start = on_start
        self._offset = 0

    # --- сетевые вызовы Telegram ---

    def _call(self, method: str, params: dict, timeout: int = 35) -> dict:
        url = API.format(token=self.token, method=method)
        data = urllib.parse.urlencode(params).encode("utf-8")
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send_message(self, chat_id: str, text: str) -> None:
        # Telegram ограничивает сообщение 4096 символами
        for chunk in _split(text, 4000):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def get_updates(self, timeout: int = 30) -> list[dict]:
        resp = self._call("getUpdates",
                          {"offset": self._offset, "timeout": timeout},
                          timeout=timeout + 5)
        return resp.get("result", [])

    # --- основной цикл ---

    def process_update(self, update: dict) -> None:
        self._offset = max(self._offset, update["update_id"] + 1)
        message = update.get("message") or update.get("edited_message")
        if not message or "text" not in message:
            return
        chat_id = str(message["chat"]["id"])
        text = message["text"].strip()

        if text.startswith("/start"):
            if self.on_start:
                self.on_start(chat_id)
            if self.start_message:
                self.send_message(chat_id, self.start_message)
            return

        try:
            answer = self.handler(chat_id, text)
        except Exception as exc:  # один сбой не должен ронять бота
            answer = ("Entschuldigung, es gab ein technisches Problem. "
                      "Bitte versuchen Sie es erneut.")
            print(f"[telegram] Fehler bei chat {chat_id}: {exc}")
        self.send_message(chat_id, answer)

    def run(self) -> None:
        print("[telegram] Bot gestartet, warte auf Nachrichten…")
        while True:
            try:
                for update in self.get_updates():
                    self.process_update(update)
            except Exception as exc:  # сетевые сбои — подождать и продолжить
                print(f"[telegram] Polling-Fehler: {exc}")
                time.sleep(3)


def _split(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]
