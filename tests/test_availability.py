"""Тесты ядра бронирования — главное: защита от двойных броней и правила.

Запуск:  python -m pytest -q   (или  python -m unittest)
"""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from restaurant_ai import booking, db
from restaurant_ai.config import load_config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "restaurant_ai" / "configs" / "napoli.json"


def _next_weekday_at(hour: int, minute: int = 0, days_ahead: int = 7) -> datetime:
    """Дата в будущем (в пределах горизонта), в рабочие часы ресторана."""
    base = datetime.now() + timedelta(days=days_ahead)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


class BookingCoreTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG_PATH)
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_visit_duration_rules(self):
        self.assertEqual(self.config.visit_minutes(2), 120)
        self.assertEqual(self.config.visit_minutes(4), 120)
        self.assertEqual(self.config.visit_minutes(5), 180)
        self.assertEqual(self.config.visit_minutes(10), 180)

    def test_create_booking_ok(self):
        res = booking.create_booking(
            self.conn, self.config,
            guest_name="Müller", party_size=2, start=_next_weekday_at(19))
        self.assertEqual(res["status"], "ok")
        self.assertFalse(res["deposit_required"])

    def test_no_double_booking_same_slot(self):
        """Если все маленькие столы заняты в одно время — нового стола нет."""
        slot = _next_weekday_at(19)
        # 3 стола по 2 места — занимаем все тремя парами
        for _ in range(3):
            r = booking.create_booking(self.conn, self.config,
                                       guest_name="x", party_size=2, start=slot)
            self.assertEqual(r["status"], "ok")
        # четвёртая пара должна сесть за стол на 4 (апгрейд), их 4 шт.
        for _ in range(4):
            r = booking.create_booking(self.conn, self.config,
                                       guest_name="x", party_size=2, start=slot)
            self.assertEqual(r["status"], "ok")
        # дальше столы на 2 и 4 кончились — следующая пара уедет на стол 6/8,
        # их 3 шт., потом мест на 2-местную бронь не остаётся
        for _ in range(3):
            booking.create_booking(self.conn, self.config,
                                   guest_name="x", party_size=2, start=slot)
        r = booking.create_booking(self.conn, self.config,
                                   guest_name="x", party_size=2, start=slot)
        self.assertEqual(r["status"], "no_table")

    def test_non_overlapping_times_reuse_table(self):
        """Тот же стол можно занять второй раз, если время не пересекается."""
        first = _next_weekday_at(13)            # 13:00–15:00
        later = _next_weekday_at(16)            # 16:00 — после освобождения
        r1 = booking.create_booking(self.conn, self.config,
                                    guest_name="a", party_size=4, start=first)
        r2 = booking.create_booking(self.conn, self.config,
                                    guest_name="b", party_size=4, start=later)
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r2["status"], "ok")

    def test_overlapping_times_block_table(self):
        first = _next_weekday_at(13)            # 13:00–15:00
        overlap = _next_weekday_at(14)          # 14:00 — пересекается
        # занимаем ВСЕ столы в 13:00, чтобы в 14:00 точно не было места
        slot = first
        while booking.create_booking(self.conn, self.config, guest_name="x",
                                     party_size=2, start=slot)["status"] == "ok":
            pass
        r = booking.create_booking(self.conn, self.config,
                                   guest_name="late", party_size=2, start=overlap)
        self.assertEqual(r["status"], "no_table")

    def test_deposit_required_for_7_plus(self):
        res = booking.create_booking(
            self.conn, self.config,
            guest_name="Gruppe", party_size=7, start=_next_weekday_at(19))
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["deposit_required"])
        self.assertEqual(res["deposit_amount_eur"], 50)

    def test_too_big_party_needs_human(self):
        res = booking.create_booking(
            self.conn, self.config,
            guest_name="Big", party_size=12, start=_next_weekday_at(19))
        self.assertEqual(res["status"], "too_big")

    def test_outside_opening_hours_rejected(self):
        res = booking.create_booking(
            self.conn, self.config,
            guest_name="Early", party_size=2, start=_next_weekday_at(9))
        self.assertEqual(res["status"], "closed")

    def test_beyond_horizon_rejected(self):
        far = datetime.now() + timedelta(days=200)
        far = far.replace(hour=19, minute=0, second=0, microsecond=0)
        res = booking.create_booking(self.conn, self.config,
                                     guest_name="Future", party_size=2, start=far)
        self.assertEqual(res["status"], "too_far")

    def test_cancel_frees_table(self):
        slot = _next_weekday_at(19)
        # занять все столы
        ids = []
        while True:
            r = booking.create_booking(self.conn, self.config, guest_name="x",
                                       party_size=2, start=slot)
            if r["status"] != "ok":
                break
            ids.append(r["booking_id"])
        # мест нет
        self.assertEqual(
            booking.create_booking(self.conn, self.config, guest_name="y",
                                   party_size=2, start=slot)["status"],
            "no_table")
        # отменяем одну — место появляется
        booking.cancel_booking(self.conn, ids[0])
        self.assertEqual(
            booking.create_booking(self.conn, self.config, guest_name="z",
                                   party_size=2, start=slot)["status"],
            "ok")

    def test_modify_booking_time(self):
        slot = _next_weekday_at(19)
        r = booking.create_booking(self.conn, self.config, guest_name="m",
                                   party_size=2, start=slot)
        new_slot = _next_weekday_at(20)
        res = booking.modify_booking(self.conn, self.config, r["booking_id"],
                                     new_start=new_slot)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["start"], new_slot.isoformat())


if __name__ == "__main__":
    unittest.main()
