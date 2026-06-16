"""Тесты канала: менеджер диалогов (с поддельным Claude) и логика Telegram-бота.

Сеть и API не вызываются — всё на заглушках.
"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from restaurant_ai import db
from restaurant_ai.agent import ConversationManager
from restaurant_ai.config import load_config
from restaurant_ai.telegram_bot import TelegramBot

CONFIG_PATH = Path(__file__).resolve().parents[1] / "restaurant_ai" / "configs" / "napoli.json"
MENU_PATH = Path(__file__).resolve().parents[1] / "restaurant_ai" / "configs" / "napoli_menu.md"


# --- заглушка клиента OpenAI (формат chat completions) ---

class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments  # JSON-строка, как у OpenAI


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResp:
    """Имитация ответа OpenAI: resp.choices[0].message."""
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


def text_resp(text):
    return FakeResp(FakeMessage(content=text))


def tool_resp(call_id, name, arguments_json):
    return FakeResp(FakeMessage(
        content=None,
        tool_calls=[FakeToolCall(call_id, name, arguments_json)]))


class _Completions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class _Chat:
    def __init__(self, script):
        self.completions = _Completions(script)


class FakeClient:
    def __init__(self, script):
        self.chat = _Chat(script)


def _slot(hour=19, days_ahead=7):
    return (datetime.now() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0).isoformat()


class ConversationManagerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG_PATH)
        self.menu = MENU_PATH.read_text(encoding="utf-8")
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_reply_with_tool_call(self):
        """Модель вызывает check_availability, затем отвечает текстом."""
        script = [
            tool_resp("t1", "check_availability",
                      json.dumps({"date_time": _slot(), "party_size": 2})),
            text_resp("Ein Tisch ist frei!"),
        ]
        mgr = ConversationManager(self.conn, self.config, self.menu,
                                  client=FakeClient(script))
        answer = mgr.reply("chat1", "Tisch für 2 heute Abend?")
        self.assertEqual(answer, "Ein Tisch ist frei!")
        # история: user, assistant(tool_calls), tool(result), assistant(text)
        self.assertEqual(len(mgr._sessions["chat1"]), 4)

    def test_sessions_are_isolated(self):
        script = [text_resp("A"), text_resp("B")]
        mgr = ConversationManager(self.conn, self.config, self.menu,
                                  client=FakeClient(script))
        self.assertEqual(mgr.reply("chatA", "hi"), "A")
        self.assertEqual(mgr.reply("chatB", "hi"), "B")
        self.assertEqual(len(mgr._sessions), 2)
        self.assertIn("chatA", mgr._sessions)
        self.assertIn("chatB", mgr._sessions)

    def test_reset_clears_session(self):
        script = [text_resp("ok")]
        mgr = ConversationManager(self.conn, self.config, self.menu,
                                  client=FakeClient(script))
        mgr.reply("c", "hi")
        self.assertIn("c", mgr._sessions)
        mgr.reset("c")
        self.assertNotIn("c", mgr._sessions)


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.started = []
        self.bot = TelegramBot(
            "token",
            handler=lambda chat_id, text: f"echo:{text}",
            start_message="Willkommen!",
            on_start=lambda chat_id: self.started.append(chat_id),
        )
        # перехватываем отправку, чтобы не ходить в сеть
        self.bot.send_message = lambda cid, txt: self.sent.append((cid, txt))

    def test_text_message_is_handled(self):
        self.bot.process_update(
            {"update_id": 10, "message": {"chat": {"id": 123}, "text": "hi"}})
        self.assertEqual(self.sent, [("123", "echo:hi")])
        self.assertEqual(self.bot._offset, 11)

    def test_start_command(self):
        self.bot.process_update(
            {"update_id": 1, "message": {"chat": {"id": 5}, "text": "/start"}})
        self.assertEqual(self.started, ["5"])
        self.assertEqual(self.sent, [("5", "Willkommen!")])

    def test_non_text_ignored(self):
        self.bot.process_update(
            {"update_id": 2, "message": {"chat": {"id": 5}}})  # нет text
        self.assertEqual(self.sent, [])
        self.assertEqual(self.bot._offset, 3)

    def test_handler_error_is_caught(self):
        def boom(chat_id, text):
            raise RuntimeError("kaputt")
        self.bot.handler = boom
        self.bot.process_update(
            {"update_id": 7, "message": {"chat": {"id": 9}, "text": "hi"}})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("technisches Problem", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
