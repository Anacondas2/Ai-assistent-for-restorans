"""Инструменты агента — то, что модель может «вызвать» во время разговора.

Каждый инструмент описан схемой в формате OpenAI (function calling) и привязан
к реальной операции ядра бронирования через ``dispatch``. Так «мозг» агента
остаётся отделён от бизнес-логики: ядро уже протестировано, а агент лишь решает,
что вызвать.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import availability, booking
from .config import RestaurantConfig


def _fn(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


# Схемы инструментов в формате OpenAI tool/function calling.
TOOLS = [
    _fn("check_availability",
        "Проверить, есть ли свободный стол на указанные дату-время и число "
        "гостей. Если нет — вернёт ближайшие свободные альтернативные времена.",
        {
            "date_time": {"type": "string",
                          "description": "Дата и время в формате ISO, напр. 2026-06-20T19:00"},
            "party_size": {"type": "integer", "description": "Число гостей"},
        },
        ["date_time", "party_size"]),
    _fn("create_booking",
        "Создать бронь столика. Вызывать только после того, как подтверждено "
        "наличие места и собраны имя и число гостей.",
        {
            "guest_name": {"type": "string"},
            "party_size": {"type": "integer"},
            "date_time": {"type": "string", "description": "ISO, напр. 2026-06-20T19:00"},
            "guest_contact": {"type": "string", "description": "Телефон или email (необязательно)"},
            "notes": {"type": "string", "description": "Пожелания, повод и т.п. (необязательно)"},
        },
        ["guest_name", "party_size", "date_time"]),
    _fn("modify_booking",
        "Перенести бронь на другое время и/или изменить число гостей.",
        {
            "booking_id": {"type": "integer"},
            "new_date_time": {"type": "string", "description": "ISO (необязательно)"},
            "new_party_size": {"type": "integer", "description": "(необязательно)"},
        },
        ["booking_id"]),
    _fn("cancel_booking",
        "Отменить существующую бронь по её номеру.",
        {"booking_id": {"type": "integer"}},
        ["booking_id"]),
    _fn("save_guest_note",
        "Сохранить важную информацию от гостя (аллергия, повод, особое "
        "пожелание), чтобы её увидел персонал.",
        {
            "content": {"type": "string"},
            "category": {"type": "string",
                         "enum": ["allergy", "occasion", "preference", "other"]},
            "guest_contact": {"type": "string"},
            "booking_id": {"type": "integer"},
        },
        ["content", "category"]),
    _fn("escalate_to_human",
        "Передать разговор живому человеку (хостес) по SMS. Вызывать, если "
        "гость недоволен, вопрос сложный, нужна очень большая компания, ИЛИ "
        "вопрос про аллергию, в котором нельзя быть уверенным на 100%.",
        {
            "reason": {"type": "string"},
            "summary": {"type": "string", "description": "Краткая суть для хостес"},
            "guest_contact": {"type": "string"},
        },
        ["reason", "summary"]),
]


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _suggest_alternatives(conn, config, party_size, start, span_minutes=180,
                          step_minutes=30, max_suggestions=3):
    """Ближайшие свободные времена вокруг запрошенного (±span)."""
    suggestions = []
    for offset in range(step_minutes, span_minutes + 1, step_minutes):
        for delta in (-offset, offset):
            cand = start + timedelta(minutes=delta)
            end = availability.visit_end(config, cand, party_size)
            if not availability.within_opening_hours(config, cand, end):
                continue
            if availability.find_available_table(conn, config, party_size, cand):
                iso = cand.isoformat()
                if iso not in suggestions:
                    suggestions.append(iso)
            if len(suggestions) >= max_suggestions:
                return suggestions
    return suggestions


def dispatch(conn: sqlite3.Connection, config: RestaurantConfig,
             tool_name: str, tool_input: dict) -> dict:
    """Выполнить вызванный агентом инструмент и вернуть результат (JSON-словарь)."""
    if tool_name == "check_availability":
        start = _parse_dt(tool_input["date_time"])
        party = tool_input["party_size"]
        if party > config.largest_table_seats():
            return {"available": False, "reason": "too_big",
                    "largest_table_seats": config.largest_table_seats()}
        end = availability.visit_end(config, start, party)
        if not availability.within_opening_hours(config, start, end):
            return {"available": False, "reason": "closed"}
        table = availability.find_available_table(conn, config, party, start)
        if table:
            return {"available": True, "table_id": table.id,
                    "deposit_required": config.deposit_required(party)}
        return {"available": False, "reason": "no_table",
                "alternatives": _suggest_alternatives(conn, config, party, start)}

    if tool_name == "create_booking":
        return booking.create_booking(
            conn, config,
            guest_name=tool_input["guest_name"],
            party_size=tool_input["party_size"],
            start=_parse_dt(tool_input["date_time"]),
            guest_contact=tool_input.get("guest_contact"),
            notes=tool_input.get("notes"))

    if tool_name == "modify_booking":
        new_start = (_parse_dt(tool_input["new_date_time"])
                     if tool_input.get("new_date_time") else None)
        return booking.modify_booking(
            conn, config, tool_input["booking_id"],
            new_start=new_start,
            new_party_size=tool_input.get("new_party_size"))

    if tool_name == "cancel_booking":
        return booking.cancel_booking(conn, tool_input["booking_id"])

    if tool_name == "save_guest_note":
        note_id = booking.add_guest_note(
            conn, config.restaurant_id,
            content=tool_input["content"],
            category=tool_input.get("category", "other"),
            guest_contact=tool_input.get("guest_contact"),
            booking_id=tool_input.get("booking_id"))
        return {"status": "ok", "note_id": note_id}

    if tool_name == "escalate_to_human":
        # На старте «передача хостес» = записать обращение + (позже) отправить SMS.
        note_id = booking.add_guest_note(
            conn, config.restaurant_id,
            content=f"ЭСКАЛАЦИЯ: {tool_input['reason']} — {tool_input['summary']}",
            category="other",
            guest_contact=tool_input.get("guest_contact"))
        return {"status": "escalated", "note_id": note_id,
                "message": "Передано хостес. С гостем свяжется человек."}

    return {"status": "unknown_tool", "tool": tool_name}
