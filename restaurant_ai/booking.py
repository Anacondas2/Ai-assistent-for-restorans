"""Операции над бронями: создать, изменить, отменить — поверх availability.

Каждая операция возвращает словарь с понятным полем ``status``, чтобы слой
«мозга» агента мог объяснить результат гостю человеческим языком.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import availability
from .config import RestaurantConfig


def create_booking(conn: sqlite3.Connection, config: RestaurantConfig, *,
                   guest_name: str, party_size: int, start: datetime,
                   guest_contact: str | None = None, notes: str | None = None,
                   now: datetime | None = None) -> dict:
    """Создать бронь. Возможные status:
    ok | closed | too_far | in_past | too_big | no_table
    """
    now = now or datetime.now()

    if not availability.within_horizon(config, now, start):
        status = "in_past" if start < now else "too_far"
        return {"status": status, "horizon_days": config.booking_horizon_days}

    end = availability.visit_end(config, start, party_size)

    if not availability.within_opening_hours(config, start, end):
        return {"status": "closed", "intervals": config.opening_intervals(start)}

    # компания больше самого большого стола — нужен человек (объединение столов)
    if party_size > config.largest_table_seats():
        return {"status": "too_big",
                "largest_table_seats": config.largest_table_seats()}

    table = availability.find_available_table(conn, config, party_size, start)
    if table is None:
        return {"status": "no_table"}

    deposit_required = config.deposit_required(party_size)
    cur = conn.execute(
        """
        INSERT INTO bookings
            (restaurant_id, table_id, guest_name, guest_contact, party_size,
             start_ts, end_ts, status, deposit_required, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
        """,
        (config.restaurant_id, table.id, guest_name, guest_contact, party_size,
         start.isoformat(), end.isoformat(), int(deposit_required), notes),
    )
    conn.commit()
    result = {
        "status": "ok",
        "booking_id": cur.lastrowid,
        "table_id": table.id,
        "party_size": party_size,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "deposit_required": deposit_required,
    }
    if deposit_required and config.deposit:
        result["deposit_amount_eur"] = config.deposit.amount_eur
    return result


def cancel_booking(conn: sqlite3.Connection, booking_id: int) -> dict:
    cur = conn.execute(
        "UPDATE bookings SET status = 'cancelled' "
        "WHERE id = ? AND status = 'confirmed'",
        (booking_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"status": "not_found"}
    return {"status": "ok", "booking_id": booking_id}


def modify_booking(conn: sqlite3.Connection, config: RestaurantConfig,
                   booking_id: int, *, new_start: datetime | None = None,
                   new_party_size: int | None = None,
                   now: datetime | None = None) -> dict:
    """Перенести/изменить бронь. Проверяет, что новое время/размер помещаются."""
    now = now or datetime.now()
    row = conn.execute(
        "SELECT * FROM bookings WHERE id = ? AND status = 'confirmed'",
        (booking_id,),
    ).fetchone()
    if row is None:
        return {"status": "not_found"}

    start = new_start or datetime.fromisoformat(row["start_ts"])
    party_size = new_party_size or row["party_size"]

    if not availability.within_horizon(config, now, start):
        return {"status": "in_past" if start < now else "too_far"}

    end = availability.visit_end(config, start, party_size)
    if not availability.within_opening_hours(config, start, end):
        return {"status": "closed", "intervals": config.opening_intervals(start)}
    if party_size > config.largest_table_seats():
        return {"status": "too_big",
                "largest_table_seats": config.largest_table_seats()}

    # ищем стол, не считая саму эту бронь занятой
    table = availability.find_available_table(
        conn, config, party_size, start, exclude_booking_id=booking_id)
    if table is None:
        return {"status": "no_table"}

    deposit_required = config.deposit_required(party_size)
    conn.execute(
        "UPDATE bookings SET table_id=?, party_size=?, start_ts=?, end_ts=?, "
        "deposit_required=? WHERE id=?",
        (table.id, party_size, start.isoformat(), end.isoformat(),
         int(deposit_required), booking_id),
    )
    conn.commit()
    return {
        "status": "ok",
        "booking_id": booking_id,
        "table_id": table.id,
        "party_size": party_size,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "deposit_required": deposit_required,
    }


def list_bookings_for_day(conn: sqlite3.Connection, restaurant_id: str,
                          day: datetime) -> list[dict]:
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    rows = conn.execute(
        """
        SELECT * FROM bookings
        WHERE restaurant_id = ? AND status = 'confirmed'
          AND start_ts BETWEEN ? AND ?
        ORDER BY start_ts
        """,
        (restaurant_id, day_start.isoformat(), day_end.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def add_guest_note(conn: sqlite3.Connection, restaurant_id: str, *,
                   content: str, category: str = "other",
                   booking_id: int | None = None,
                   guest_contact: str | None = None) -> int:
    """Записать важную информацию, услышанную от гостя."""
    cur = conn.execute(
        "INSERT INTO guest_notes (restaurant_id, booking_id, guest_contact, "
        "category, content) VALUES (?, ?, ?, ?, ?)",
        (restaurant_id, booking_id, guest_contact, category, content),
    )
    conn.commit()
    return cur.lastrowid
