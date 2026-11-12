"""Unit tests for auto_booking.py and locum_api.py (no live booking calls)."""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import MagicMock

import auto_booking as ab
from locum_api import ApiError, LocumClient, SessionInfo


class TestParsingAndMatching(unittest.TestCase):
    def test_parse_target_dates_iso_and_range(self) -> None:
        dates = ab.parse_target_dates("2026-11-15,2026-11-17..2026-11-19")
        self.assertEqual(
            dates,
            {"2026-11-15", "2026-11-17", "2026-11-18", "2026-11-19"},
        )
        self.assertIsNone(ab.parse_target_dates("all"))
        self.assertIsNone(ab.parse_target_dates(""))

    def test_parse_target_dates_dd_mm(self) -> None:
        ref = dt.date(2026, 11, 12)
        self.assertEqual(ab.parse_single_date("13/12", ref), dt.date(2026, 12, 13))
        self.assertEqual(ab.parse_single_date("05/01", ref), dt.date(2027, 1, 5))

    def test_find_matching_slots_filters_properly(self) -> None:
        days = [
            {
                "date": "2026-11-14",
                "slots": [
                    {"key": "0900-1300", "label": "9am – 1pm", "closed": False, "mo": {"left": 1, "total": 2}},
                    {"key": "1800-2200", "label": "6pm – 10pm", "closed": False, "mo": {"left": 1, "total": 2}},
                ],
            },
            {
                "date": "2026-11-15",
                "slots": [
                    {"key": "0900-1300", "label": "9am – 1pm", "closed": True, "mo": {"left": 1, "total": 2}},
                    {"key": "1800-2200", "label": "6pm – 10pm", "closed": False, "mo": {"left": 0, "total": 2}},
                    {"key": "1900-2300", "label": "7pm – 11pm", "closed": False, "mo": {"left": 1, "total": 2}},
                ],
            },
        ]
        booked_keys = {("2026-11-14", "0900-1300")}
        booked_dates = {"2026-11-14"}

        # Without one_per_day: 2026-11-14 1800-2200 and 2026-11-15 1900-2300 match
        m1 = ab.find_matching_slots(days, "mo", booked_keys, booked_dates, None, None, one_per_day=False)
        self.assertEqual([(d, s["key"]) for d, s in m1], [("2026-11-14", "1800-2200"), ("2026-11-15", "1900-2300")])

        # With one_per_day: 2026-11-14 skipped
        m2 = ab.find_matching_slots(days, "mo", booked_keys, booked_dates, None, None, one_per_day=True)
        self.assertEqual([(d, s["key"]) for d, s in m2], [("2026-11-15", "1900-2300")])

        # Slot filter + priority order (1900-2300 prioritized over 1800-2200)
        m3 = ab.find_matching_slots(
            days, "mo", booked_keys, booked_dates, None, ["1800-2200"], one_per_day=False
        )
        self.assertEqual([(d, s["key"]) for d, s in m3], [("2026-11-14", "1800-2200")])

        days_same = [
            {
                "date": "2026-11-20",
                "slots": [
                    {"key": "0900-1300", "label": "9am – 1pm", "closed": False, "mo": {"left": 1, "total": 2}},
                    {"key": "1800-2200", "label": "6pm – 10pm", "closed": False, "mo": {"left": 1, "total": 2}},
                ],
            }
        ]
        m_prio = ab.find_matching_slots(
            days_same, "mo", set(), set(), None, ["1800-2200", "0900-1300"], one_per_day=False
        )
        self.assertEqual([s["key"] for _, s in m_prio], ["1800-2200", "0900-1300"])

    def test_parse_number_selection(self) -> None:
        self.assertEqual(ab.parse_number_selection("4,1", 5), [4, 1])
        self.assertEqual(ab.parse_number_selection("2-4,1", 5), [2, 3, 4, 1])
        self.assertIsNone(ab.parse_number_selection("20/11", 31))
        with self.assertRaises(ValueError):
            ab.parse_number_selection("99", 5)

    def test_attempt_bookings_dry_run_and_real(self) -> None:
        client = MagicMock(spec=LocumClient)
        client.book_slot.return_value = {"id": "ABC123", "seat": "MO1"}
        matches = [("2026-11-20", {"key": "1800-2200", "label": "6pm – 10pm"})]
        booked_keys: set[tuple[str, str]] = set()
        booked_dates: set[str] = set()

        # Dry run should not call book_slot
        count = ab.attempt_bookings(client, matches, booked_keys, booked_dates, 1, 0, dry_run=True)
        self.assertEqual(count, 1)
        client.book_slot.assert_not_called()

        # Real run calls book_slot
        booked_keys.clear()
        booked_dates.clear()
        count = ab.attempt_bookings(client, matches, booked_keys, booked_dates, 1, 0, dry_run=False)
        self.assertEqual(count, 1)
        client.book_slot.assert_called_once_with("2026-11-20", "1800-2200")


class TestClientAutoRelogin(unittest.TestCase):
    def test_auto_relogin_on_expired_session(self) -> None:
        client = LocumClient()
        client._ic = "910725065438"
        client._last4 = "5412"
        client.session = SessionInfo(token="old-tok", profile={"role": "MO"})

        calls: list[tuple[str, dict]] = []

        def fake_request(action: str, data=None, use_get=False):
            calls.append((action, dict(data or {})))
            if action == "mine" and data["token"] == "old-tok":
                raise ApiError("Session expired", expired=True)
            if action == "login":
                return {"ok": True, "token": "new-tok", "profile": {"role": "MO"}}
            return {"ok": True, "bookings": [{"id": "X1"}], "cutoff": 24}

        client._request = fake_request  # type: ignore[method-assign]
        bookings, cutoff = client.get_my_bookings()
        self.assertEqual(bookings, [{"id": "X1"}])
        self.assertEqual(cutoff, 24)
        self.assertEqual([c[0] for c in calls], ["mine", "login", "mine"])
        self.assertEqual(client.session.token, "new-tok")


if __name__ == "__main__":
    unittest.main()

