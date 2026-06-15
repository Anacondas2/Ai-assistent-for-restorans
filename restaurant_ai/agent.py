"""«Мозг» агента: системный промпт + разговорный цикл с Claude.

Ядро (booking/availability) уже протестировано и не зависит от ИИ. Здесь Claude
лишь понимает гостя и выбирает, какой инструмент вызвать. Библиотека anthropic
импортируется лениво — без неё ядро и тесты диспетчера работают.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import tools
from .config import RestaurantConfig

# Рекомендуемая модель Claude (актуальная и качественная для агента).
DEFAULT_MODEL = "claude-sonnet-4-6"

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


def run_chat(conn: sqlite3.Connection, config: RestaurantConfig, menu_text: str,
             *, model: str = DEFAULT_MODEL, max_turns: int = 20) -> None:
    """Простой консольный разговор с агентом (для демонстрации/отладки).

    Требует установленный пакет anthropic и переменную окружения
    ANTHROPIC_API_KEY.
    """
    try:
        import anthropic
    except ImportError:
        raise SystemExit("Установите пакет: pip install anthropic")

    client = anthropic.Anthropic()
    system_prompt = build_system_prompt(config, menu_text)
    messages: list[dict] = []

    print(f"[{config.name}] Чат запущен. Напишите сообщение (или 'exit').\n")
    for _ in range(max_turns):
        user = input("Гость: ").strip()
        if user.lower() in {"exit", "quit", "выход"}:
            break
        messages.append({"role": "user", "content": user})
        _run_one_exchange(client, model, system_prompt, messages, conn, config)


def _run_one_exchange(client, model, system_prompt, messages, conn, config):
    """Один обмен: модель может несколько раз вызвать инструменты, пока не ответит."""
    while True:
        resp = client.messages.create(
            model=model, max_tokens=1024,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            tools=tools.TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            print(f"Агент: {text}\n")
            return

        tool_results = []
        for tu in tool_uses:
            result = tools.dispatch(conn, config, tu.name, tu.input)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                 "content": str(result)})
        messages.append({"role": "user", "content": tool_results})
