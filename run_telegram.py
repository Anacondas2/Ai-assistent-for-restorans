"""Запуск Telegram-бота агента для одного ресторана.

Шаги:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=...      # ключ Claude (оплата по факту)
    export TELEGRAM_BOT_TOKEN=...     # токен бота от @BotFather
    python3 run_telegram.py

Брони сохраняются в restaurant.db.
"""

import os
from pathlib import Path

from restaurant_ai import db
from restaurant_ai.agent import ConversationManager
from restaurant_ai.config import load_config
from restaurant_ai.telegram_bot import TelegramBot

BASE = Path(__file__).resolve().parent / "restaurant_ai" / "configs"

START_MESSAGE = (
    "Willkommen bei 60 seconds to napoli! 🍕\n"
    "Ich helfe Ihnen bei Tischreservierungen und Fragen zur Speisekarte.\n"
    "Hinweis: Dieser Chat wird zur Bearbeitung Ihrer Anfrage gespeichert."
)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Установите переменную окружения TELEGRAM_BOT_TOKEN")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Установите переменную окружения ANTHROPIC_API_KEY")

    config = load_config(BASE / "napoli.json")
    menu_text = (BASE / "napoli_menu.md").read_text(encoding="utf-8")
    conn = db.connect("restaurant.db")
    manager = ConversationManager(conn, config, menu_text)

    bot = TelegramBot(
        token,
        handler=manager.reply,
        start_message=START_MESSAGE,
        on_start=manager.reset,
    )
    bot.run()


if __name__ == "__main__":
    main()
