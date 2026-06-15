"""Логика «свободно/занято» — самая ответственная часть.

Здесь живёт защита от двойных броней: стол считается свободным, только если
ни одна активная бронь этого стола не пересекается по времени с новой.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .config import RestaurantConfig, Table


def intervals_overlap(start_a: datetime, end_a: datetime,
                      start_b: datetime, end_b: datetime) -> bool:
    """Пересекаются ли интервалы [start, end). Касание границами — НЕ пересечение."""
    return start_a < end_b and start_b < end_a


def visit_end(config: RestaurantConfig, start: datetime, party_size: int) -> datetime:
    return start + timedelta(minutes=config.visit_minutes(party_size))


def within_opening_hours(config: RestaurantConfig,
                         start: datetime, end: datetime) -> bool:
    """Бронь должна целиком умещаться в один интервал работы ресторана."""
    for open_dt, close_dt in config.opening_intervals(start):
        if start >= open_dt and end <= close_dt:
            return True
    return False


def within_horizon(config: RestaurantConfig, now: datetime, start: datetime) -> bool:
    if start < now:
        return False
    return start <= now + timedelta(days=config.booking_horizon_days)


def _table_is_free(conn: sqlite3.Connection, restaurant_id: str, table_id: str,
                   start: datetime, end: datetime,
                   exclude_booking_id: int | None = None) -> bool:
    rows = conn.execute(
        """
        SELECT start_ts, end_ts FROM bookings
        WHERE restaurant_id = ? AND table_id = ? AND status = 'confirmed'
          AND (? IS NULL OR id != ?)
        """,
        (restaurant_id, table_id, exclude_booking_id, exclude_booking_id),
    ).fetchall()
    for row in rows:
        b_start = datetime.fromisoformat(row["start_ts"])
        b_end = datetime.fromisoformat(row["end_ts"])
        if intervals_overlap(start, end, b_start, b_end):
            return False
    return True


def find_available_table(conn: sqlite3.Connection, config: RestaurantConfig,
                         party_size: int, start: datetime,
                         exclude_booking_id: int | None = None) -> Table | None:
    """Найти свободный стол под компанию. Берём наименьший подходящий, чтобы
    не «съедать» большие столы маленькими компаниями."""
    end = visit_end(config, start, party_size)
    candidates = sorted(
        (t for t in config.tables if t.seats >= party_size),
        key=lambda t: t.seats,
    )
    for table in candidates:
        if _table_is_free(conn, config.restaurant_id, table.id,
                           start, end, exclude_booking_id):
            return table
    return None


def free_tables_at(conn: sqlite3.Connection, config: RestaurantConfig,
                   start: datetime, party_size: int = 1) -> list[Table]:
    """Все свободные на это время столы, вмещающие компанию (для отчётов/ответов)."""
    end = visit_end(config, start, party_size)
    result = []
    for table in config.tables:
        if table.seats >= party_size and _table_is_free(
                conn, config.restaurant_id, table.id, start, end):
            result.append(table)
    return result
