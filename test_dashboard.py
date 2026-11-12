"""Unit and API tests for web_app.py and bot_manager.py."""

from __future__ import annotations

import datetime as dt
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import bot_manager as bm
import web_app


class TestBotManagerAndWeb(unittest.TestCase):
    def test_compute_run_datetime_myt_and_wib(self) -> None:
        d_myt = bm.compute_run_datetime("00:00:00", "MYT", "2026-11-13")
        self.assertEqual(d_myt.tzinfo, bm.MYT)
        self.assertEqual(d_myt.hour, 0)
        self.assertEqual(d_myt.minute, 0)

        d_wib = bm.compute_run_datetime("23:00:00", "WIB", "2026-11-12")
        self.assertEqual(d_wib.tzinfo, bm.WIB)
        self.assertEqual(d_wib.hour, 23)
        self.assertEqual(d_myt.timestamp(), d_wib.timestamp())

    def test_bot_manager_monitor_dry_run_logs_and_completes(self) -> None:
        fake_job = {
            "id": 99,
            "account_ic": "910725065438",
            "title": "Test Dry Run",
            "mode": "monitor",
            "target_dates": ["2026-11-20"],
            "target_slots": ["1800-2200"],
            "poll_interval": 0.3,
            "max_bookings": 1,
            "one_per_day": False,
            "dry_run": True,
            "success_count": 0,
        }
        fake_days = [
            {
                "date": "2026-11-20",
                "slots": [
                    {"key": "1800-2200", "label": "6pm – 10pm", "closed": False, "mo": {"left": 1, "total": 2}}
                ],
            }
        ]
        mock_client = MagicMock()
        mock_client.session = MagicMock(role_key="mo")
        mock_client.get_my_bookings.return_value = ([], 24)
        mock_client.get_availability.return_value = fake_days

        mgr = bm.BotManager()
        with (
            patch("bot_manager.db.get_job", return_value=fake_job),
            patch("bot_manager.db.get_account", return_value={"ic_number": "910725065438", "last4": "5412", "name": "TEST", "role": "MO"}),
            patch("bot_manager.LocumClient", return_value=mock_client),
            patch("bot_manager.db.add_log") as m_log,
            patch("bot_manager.db.add_booking_result", return_value=7) as m_res,
            patch("bot_manager.db.get_telegram_settings", return_value={"enabled": True, "bot_token": "tok", "chat_id": "123", "notify_on_dry_run": True}),
            patch("bot_manager.send_telegram_message", return_value=(True, "Terkirim ke Telegram.")) as m_tg,
            patch("bot_manager.db.update_booking_result_telegram") as m_tg_upd,
            patch("bot_manager.db.update_job_status") as m_stat,
        ):
            self.assertTrue(mgr.start_job(99))
            for _ in range(30):
                if not mgr.is_active(99):
                    break
                time.sleep(0.05)
            self.assertFalse(mgr.is_active(99))
            m_res.assert_called_once()
            self.assertEqual(m_res.call_args.kwargs["outcome"], "DRY_RUN")
            self.assertEqual(m_res.call_args.kwargs["slot_date"], "2026-11-20")
            m_tg.assert_called_once()
            m_tg_upd.assert_called_once_with(7, True, "Terkirim ke Telegram.")
            self.assertTrue(any(call.args[1] == "completed" for call in m_stat.call_args_list))
            self.assertTrue(any(call.args[2] == "SUCCESS" for call in m_log.call_args_list))

    def test_web_app_routes(self) -> None:
        with (
            patch("web_app.db.init_db"),
            patch("web_app.db.check_health", return_value=(True, "OK")),
            patch("web_app.db.list_jobs", return_value=[]),
            patch("web_app.db.list_booking_results", return_value=[{"id": 1, "outcome": "SUCCESS", "slot_date": "2026-11-20"}]),
            patch("web_app.db.list_logs", return_value=[]),
            patch("web_app.db.get_account", return_value={"ic_number": "910725065438", "last4": "5412"}),
            patch("web_app.db.create_job", side_effect=lambda d: {"id": 1, **d}),
            patch("web_app.db.get_job", return_value={"id": 1, "title": "Test", "status": "waiting"}),
            patch("web_app.db.get_telegram_settings", return_value={"bot_token": "tok", "chat_id": "123", "enabled": True}),
            patch("web_app.db.upsert_telegram_settings", return_value={"bot_token": "tok", "chat_id": "123", "enabled": True}),
            patch("web_app.send_telegram_message", return_value=(True, "Terkirim ke Telegram.")),
            patch("web_app.db.add_log"),
            patch("web_app.MANAGER.start_job", return_value=True),
            patch("web_app.MANAGER.resume_active_jobs", return_value=[]),
        ):
            client = TestClient(web_app.app)
            r_home = client.get("/")
            self.assertEqual(r_home.status_code, 200)
            self.assertIn("LocumSync", r_home.text)

            for path, needle in [
                ("/login", 'data-page="login"'),
                ("/jadwal", 'id="page-jadwal"'),
                ("/auto-book", 'id="page-auto-book"'),
                ("/riwayat", 'id="page-riwayat"'),
                ("/logs", 'id="page-logs"'),
                ("/telegram", 'id="page-telegram"'),
            ]:
                rp = client.get(path)
                self.assertEqual(rp.status_code, 200, f"Route {path} failed")
                self.assertIn("LocumSync", rp.text)
                self.assertIn(needle, rp.text)

            r_logout = client.post("/api/logout")
            self.assertEqual(r_logout.status_code, 200)
            self.assertTrue(r_logout.json()["ok"])

            r_health = client.get("/api/health")
            self.assertEqual(r_health.status_code, 200)
            self.assertTrue(r_health.json()["db_ok"])

            r_job = client.post(
                "/api/jobs",
                json={
                    "ic": "910725065438",
                    "title": "Test Jam 12",
                    "mode": "scheduled",
                    "target_dates": ["AUTO_30D"],
                    "target_slots": ["1800-2200"],
                    "schedule_time": "00:00",
                    "schedule_tz": "MYT",
                    "start_now": True,
                },
            )
            self.assertEqual(r_job.status_code, 200)
            self.assertTrue(r_job.json()["ok"])

            r_hist = client.get("/api/history?ic=910725065438")
            self.assertEqual(r_hist.status_code, 200)
            self.assertEqual(r_hist.json()["summary"]["success"], 1)

            r_tg_save = client.post(
                "/api/telegram",
                json={"ic": "910725065438", "bot_token": "tok", "chat_id": "123", "enabled": True},
            )
            self.assertEqual(r_tg_save.status_code, 200)

            r_tg_test = client.post(
                "/api/telegram/test",
                json={"ic": "910725065438", "bot_token": "tok", "chat_id": "123"},
            )
            self.assertEqual(r_tg_test.status_code, 200)

    def test_resume_active_jobs(self) -> None:
        mgr = bm.BotManager()
        with (
            patch("bot_manager.db.list_active_jobs_all", return_value=[{"id": 12, "account_ic": "910725065438"}]),
            patch.object(mgr, "start_job", return_value=True) as m_start,
            patch("bot_manager.db.add_log") as m_log,
        ):
            resumed = mgr.resume_active_jobs()
            self.assertEqual(resumed, [12])
            m_start.assert_called_once_with(12)
            m_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()

