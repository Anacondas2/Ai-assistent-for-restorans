"""«Мозг» агента: системный промпт, генерация ответа и управление диалогами.

Ядро (booking/availability) уже протестировано и не зависит от ИИ. Здесь модель
OpenAI лишь понимает гостя и выбирает, какой инструмент вызвать. Библиотека
openai импортируется лениво — без неё ядро и тесты работают.

Слой канала (Telegram/веб) использует ``ConversationManager``: он хранит
отдельную историю на каждого гостя и общую базу броней.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import tools
from .config import RestaurantConfig

# Модель OpenAI по умолчанию (можно переопределить при создании менеджера).
DEFAULT_MODEL = "gpt-4o"

GDPR_NOTICE = (
    "Hinweis zum Datenschutz: Dieser Chat wird zur Bearbeitung Ihrer Anfrage "
    "gespeichert. Mit dem Fortfahren stimmen Sie zu."
)


def build_system_prompt(config: RestaurantConfig, menu_text: str,
                        now: datetime | None = None) -> str:
    now = now or datetime.now()
    deposit = ""
    if config.deposit:
        deposit = (f"Ab {config.deposit.min_party} Gästen ist eine Anzahlung von "
                   f"{config.deposit.amount_eur} € pro Reservierung erforderlich.")
    persona = config.persona or "Freundlich, mit leichtem Humor, aber sachlich."

    return f"""Du bist der digitale Assistent des Restaurants "{config.name}" \
({config.location}).

ROLLE & TON: {persona}
Sprich standardmäßig Deutsch. Wenn der Gast eine andere Sprache nutzt, antworte \
in dieser Sprache. Halte dich kurz: Gäste wollen schnell ein Ergebnis.

AKTUELLES DATUM/UHRZEIT: {now.isoformat()}

WAS DU KANNST:
- Tische reservieren, ändern und stornieren (nutze die Tools, rate niemals \
selbst, ob ein Tisch frei ist — rufe check_availability auf).
- Fragen zu Speisekarte, Preisen, Öffnungszeiten und Restaurant beantworten.
- Wichtige Infos des Gastes speichern (save_guest_note): Allergien, Anlass, Wünsche.

REGELN:
- Reservierungen bis zu {config.booking_horizon_days} Tage im Voraus.
- {deposit}
- Wenn kein Tisch frei ist: biete alternative Zeiten oder Warteliste an, oder \
leite an einen Menschen weiter (escalate_to_human).
- Sammle vor create_booking: Name, Personenzahl, Datum/Uhrzeit.

⚠️ ALLERGENE — STRENG: Nenne Allergene NUR exakt so, wie sie unten in der \
Speisekarte stehen. Wenn ein Allergen mit "нужно уточнить"/unklar markiert ist \
ODER der Gast eine gesundheitskritische Allergie erwähnt, BEHAUPTE NIEMALS, ein \
Gericht sei sicher. Rufe stattdessen escalate_to_human auf und bitte den Gast, \
dies direkt mit dem Personal zu bestätigen. Erfinde niemals Zutaten oder Allergene.

DATENSCHUTZ: Weise zu Beginn einmal höflich darauf hin: "{GDPR_NOTICE}"

--- SPEISEKARTE ---
{menu_text}
--- ENDE SPEISEKARTE ---
"""


def generate_reply(client, model: str, system_prompt: str, messages: list,
                   conn: sqlite3.Connection, config: RestaurantConfig,
                   max_tokens: int = 1024) -> str:
    """Один обмен: модель может несколько раз вызвать инструменты, пока не ответит.

    ``messages`` мутируется (добавляются ответы модели и результаты инструментов).
    Возвращает финальный текст для гостя. Формат — OpenAI chat completions.
    """
    while True:
        full = [{"role": "system", "content": system_prompt}] + messages
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            tools=tools.TOOLS, tool_choice="auto", messages=full)
        msg = resp.choices[0].message

        if not msg.tool_calls:
            text = msg.content or ""
            messages.append({"role": "assistant", "content": text})
            return text

        # сохраняем ответ ассистента с вызовами инструментов
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        # выполняем каждый инструмент и возвращаем результат модели
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = tools.dispatch(conn, config, tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str,
                                                   ensure_ascii=False)})


class ConversationManager:
    """Хранит отдельную историю диалога на каждого гостя (chat_id) и общую базу.

    ``client`` можно передать свой (например, заглушку в тестах). По умолчанию
    создаётся реальный клиент openai при первом обращении.
    """

    def __init__(self, conn: sqlite3.Connection, config: RestaurantConfig,
                 menu_text: str, *, client=None, model: str = DEFAULT_MODEL,
                 history_limit: int = 40):
        self.conn = conn
        self.config = config
        self.system_prompt = build_system_prompt(config, menu_text)
        self.model = model
        self.history_limit = history_limit
        self._client = client
        self._sessions: dict[str, list] = {}

    @property
    def client(self):
        if self._client is None:
            import openai  # ленивый импорт: нужен только для живых ответов
            self._client = openai.OpenAI()
        return self._client

    def reset(self, chat_id: str) -> None:
        self._sessions.pop(str(chat_id), None)

    def reply(self, chat_id: str, text: str) -> str:
        chat_id = str(chat_id)
        messages = self._sessions.setdefault(chat_id, [])
        messages.append({"role": "user", "content": text})
        answer = generate_reply(self.client, self.model, self.system_prompt,
                                messages, self.conn, self.config)
        self._trim(messages)
        return answer

    def _trim(self, messages: list) -> None:
        """Ограничить длину истории, не разрывая пару assistant→tool.

        Обрезаем с начала до ближайшей реплики пользователя, чтобы история
        всегда начиналась корректно (иначе OpenAI отвергнет «висячий» tool).
        """
        if len(messages) <= self.history_limit:
            return
        cut = len(messages) - self.history_limit
        while cut < len(messages) and messages[cut]["role"] != "user":
            cut += 1
        del messages[:cut]


def run_chat(conn: sqlite3.Connection, config: RestaurantConfig, menu_text: str,
             *, model: str = DEFAULT_MODEL, max_turns: int = 50) -> None:
    """Простой консольный разговор с агентом (для демонстрации/отладки).

    Требует пакет openai и переменную окружения OPENAI_API_KEY.
    """
    manager = ConversationManager(conn, config, menu_text, model=model)
    print(f"[{config.name}] Чат запущен. Напишите сообщение (или 'exit').\n")
    for _ in range(max_turns):
        user = input("Гость: ").strip()
        if user.lower() in {"exit", "quit", "выход"}:
            break
        print(f"Агент: {manager.reply('cli', user)}\n")
