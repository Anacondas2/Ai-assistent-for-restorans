"""Тесты диспетчера инструментов агента — без обращения к API Claude.

Проверяют, что вызовы инструментов агента корректно отображаются на
протестированное ядро бронирования.
"""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from restaurant_ai import db, tools
from restaurant_ai.config import load_config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "restaurant_ai" / "configs" / "napoli.json"


def _slot(hour=19, days_ahead=7):
    return (datetime.now() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0).isoformat()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG_PATH)
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _d(self, name, **inp):
        return tools.dispatch(self.conn, self.config, name, inp)

    def test_check_availability_free(self):
        res = self._d("check_availability", date_time=_slot(), party_size=2)
        self.assertTrue(res["available"])

    def test_check_availability_too_big(self):
        res = self._d("check_availability", date_time=_slot(), party_size=20)
        self.assertFalse(res["available"])
        self.assertEqual(res["reason"], "too_big")

    def test_check_availability_closed(self):
        res = self._d("check_availability", date_time=_slot(hour=8), party_size=2)
        self.assertFalse(res["available"])
        self.assertEqual(res["reason"], "closed")

    def test_check_availability_suggests_alternatives(self):
        slot = _slot(hour=19)
        # занять все столы в 19:00 двойками
        while self._d("create_booking", guest_name="x", party_size=2,
                      date_time=slot)["status"] == "ok":
            pass
        res = self._d("check_availability", date_time=slot, party_size=2)
        self.assertFalse(res["available"])
        self.assertEqual(res["reason"], "no_table")
        self.assertIn("alternatives", res)
        self.assertGreater(len(res["alternatives"]), 0)

    def test_create_then_cancel(self):
        r = self._d("create_booking", guest_name="Schmidt", party_size=2,
                    date_time=_slot())
        self.assertEqual(r["status"], "ok")
        c = self._d("cancel_booking", booking_id=r["booking_id"])
        self.assertEqual(c["status"], "ok")

    def test_create_deposit_flagged(self):
        r = self._d("create_booking", guest_name="Gruppe", party_size=8,
                    date_time=_slot())
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["deposit_required"])

    def test_modify(self):
        r = self._d("create_booking", guest_name="m", party_size=2, date_time=_slot(19))
        res = self._d("modify_booking", booking_id=r["booking_id"],
                      new_date_time=_slot(20))
        self.assertEqual(res["status"], "ok")

    def test_save_guest_note(self):
        res = self._d("save_guest_note", content="Nussallergie",
                      category="allergy")
        self.assertEqual(res["status"], "ok")
        self.assertIn("note_id", res)

    def test_escalate_to_human(self):
        res = self._d("escalate_to_human", reason="Allergie unklar",
                      summary="Gast fragt ob Green Mamba glutenfrei ist")
        self.assertEqual(res["status"], "escalated")

    def test_unknown_tool(self):
        res = self._d("nonexistent_tool")
        self.assertEqual(res["status"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
