"""Хранилище броней на SQLite.

На старте этого достаточно и бесплатно. При росте до многих ресторанов
SQLite заменяется на Postgres — интерфейс этого модуля остаётся тем же.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id   TEXT    NOT NULL,
    table_id        TEXT    NOT NULL,
    guest_name      TEXT    NOT NULL,
    guest_contact   TEXT,
    party_size      INTEGER NOT NULL,
    start_ts        TEXT    NOT NULL,   -- ISO 8601, локальное время ресторана
    end_ts          TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'confirmed',  -- confirmed|cancelled
    deposit_required INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bookings_lookup
    ON bookings (restaurant_id, table_id, status, start_ts);

-- Важная информация, услышанная от гостя (аллергии, повод, пожелания и т.п.)
CREATE TABLE IF NOT EXISTS guest_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id   TEXT    NOT NULL,
    booking_id      INTEGER,
    guest_contact   TEXT,
    category        TEXT,                -- allergy|occasion|preference|other
    content         TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: str | Path = "restaurant.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
