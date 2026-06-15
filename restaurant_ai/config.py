"""Настройки одного ресторана.

Каждый ресторан описывается одним JSON-файлом. Это и есть «настройка под
конкретный ресторан»: меняешь файл — получаешь нового клиента, не трогая код.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class Table:
    id: str
    seats: int


@dataclass
class VisitDurationRule:
    """Сколько минут стол занят для компании размером до max_party включительно."""
    max_party: int
    minutes: int


@dataclass
class DepositRule:
    min_party: int          # с какого числа гостей нужен депозит
    amount_eur: int         # сумма депозита
    scope: str = "per_booking"   # "per_booking" — за всю бронь (не за человека)


@dataclass
class RestaurantConfig:
    restaurant_id: str
    name: str
    location: str
    language: str
    timezone: str
    # день недели (0=пн .. 6=вс) -> список интервалов [("12:00","24:00"), ...]
    opening_hours: dict[int, list[tuple[str, str]]]
    tables: list[Table]
    visit_duration_rules: list[VisitDurationRule]
    booking_horizon_days: int
    deposit: DepositRule | None = None
    # язык/тон агента и тексты — для будущего слоя «мозга»
    persona: str = ""
    extra: dict = field(default_factory=dict)

    # --- производные помощники ---

    def visit_minutes(self, party_size: int) -> int:
        """Длительность визита для данной компании по правилам ресторана."""
        for rule in sorted(self.visit_duration_rules, key=lambda r: r.max_party):
            if party_size <= rule.max_party:
                return rule.minutes
        # если ни одно правило не подошло — берём самое длинное
        return max(r.minutes for r in self.visit_duration_rules)

    def deposit_required(self, party_size: int) -> bool:
        return self.deposit is not None and party_size >= self.deposit.min_party

    def largest_table_seats(self) -> int:
        return max((t.seats for t in self.tables), default=0)

    def opening_intervals(self, day: datetime) -> list[tuple[datetime, datetime]]:
        """Интервалы работы для конкретной даты, как пары datetime.

        Закрытие "24:00" трактуется как полночь следующего дня.
        """
        weekday = day.weekday()
        result: list[tuple[datetime, datetime]] = []
        for open_s, close_s in self.opening_hours.get(weekday, []):
            open_dt = _combine(day, open_s)
            close_dt = _combine(day, close_s)
            result.append((open_dt, close_dt))
        return result


def _combine(day: datetime, hhmm: str) -> datetime:
    """'12:00' -> datetime в этот день; '24:00' -> полночь следующего дня."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(hours=hh, minutes=mm)


def load_config(path: str | Path) -> RestaurantConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return from_dict(data)


def from_dict(data: dict) -> RestaurantConfig:
    deposit = None
    if data.get("deposit"):
        deposit = DepositRule(**data["deposit"])
    # ключи часов работы в JSON — строки ("0".."6"), приводим к int
    opening = {int(k): [tuple(iv) for iv in v]
               for k, v in data.get("opening_hours", {}).items()}
    return RestaurantConfig(
        restaurant_id=data["restaurant_id"],
        name=data["name"],
        location=data.get("location", ""),
        language=data.get("language", "de"),
        timezone=data.get("timezone", "Europe/Berlin"),
        opening_hours=opening,
        tables=[Table(**t) for t in data["tables"]],
        visit_duration_rules=[VisitDurationRule(**r)
                              for r in data["visit_duration_rules"]],
        booking_horizon_days=data.get("booking_horizon_days", 90),
        deposit=deposit,
        persona=data.get("persona", ""),
        extra=data.get("extra", {}),
    )
