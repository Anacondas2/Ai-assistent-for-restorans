"""Запуск демо-чата с агентом одного ресторана.

Использование:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=...        # ключ Claude
    python3 demo_chat.py

База броней пишется в restaurant.db (можно удалять для чистого старта).
"""

from pathlib import Path

from restaurant_ai import agent, db
from restaurant_ai.config import load_config

BASE = Path(__file__).resolve().parent / "restaurant_ai" / "configs"


def main():
    config = load_config(BASE / "napoli.json")
    menu_text = (BASE / "napoli_menu.md").read_text(encoding="utf-8")
    conn = db.connect("restaurant.db")
    agent.run_chat(conn, config, menu_text)


if __name__ == "__main__":
    main()
