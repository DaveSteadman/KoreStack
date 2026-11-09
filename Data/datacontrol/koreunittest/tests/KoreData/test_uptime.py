# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression coverage for the CSV-backed KoreData Uptime monitor.
# ====================================================================================================

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SERVICE_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreData" / "KoreDataGateway"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.uptime import UptimeMonitor  # noqa: E402


class UptimeMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.monitor   = UptimeMonitor(Path(self.temporary.name))
        self.target    = "https://status.example.test/health"
        self.now       = datetime(2026, 11, 9, 10, 30, tzinfo=timezone.utc)
        self.monitor.save_target_url(self.target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_rejects_non_http_target_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "http"):
            self.monitor.save_target_url("ftp://status.example.test")

    def test_persists_target_and_hourly_result_as_csv(self) -> None:
        hour = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)
        self.monitor._save_record(self.target, hour, True, 204, "")

        self.assertEqual(self.monitor.target_url(), self.target)
        self.assertTrue(self.monitor.settings_file.exists())
        self.assertTrue(self.monitor.hourly_file.exists())
        self.assertIn("hour_utc,url,succeeded,status_code,error", self.monitor.hourly_file.read_text(encoding="utf-8"))

    def test_timelines_have_requested_dimensions_and_states(self) -> None:
        current_hour = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)
        previous_hour = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)
        self.monitor._save_record(self.target, current_hour, True, 200, "")
        self.monitor._save_record(self.target, previous_hour, False, 503, "HTTP 503")
        view = self.monitor.view(self.now)

        self.assertEqual(len(view["hourly_days"]), 7)
        self.assertTrue(all(len(day["cells"]) == 24 for day in view["hourly_days"]))
        self.assertEqual(len(view["daily_rows"]), 12)
        self.assertTrue(all(len(row["cells"]) == 31 for row in view["daily_rows"]))
        self.assertEqual(view["daily_rows"][0]["month"], "2025-12")
        self.assertEqual(view["daily_rows"][-1]["month"], "2026-11")
        self.assertEqual(view["hourly_days"][-1]["cells"][10]["state"], "success")
        self.assertEqual(view["hourly_days"][-1]["cells"][9]["state"], "failure")
        self.assertEqual(view["hourly_days"][-1]["cells"][11]["state"], "future")

    def test_daily_summary_marks_mixed_observations_as_partial(self) -> None:
        day = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
        for hour in range(11):
            self.monitor._save_record(self.target, day.replace(hour=hour), hour != 1, 200 if hour != 1 else 503, "")

        view = self.monitor.view(self.now)
        november_day_nine = view["daily_rows"][-1]["cells"][8]
        self.assertEqual(november_day_nine["state"], "partial")

    def test_hourly_window_moves_daily_across_week_and_year_boundaries(self) -> None:
        for previous in (
            datetime(2026, 11, 8, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc),
        ):
            with self.subTest(previous=previous):
                current = previous + timedelta(minutes=1)
                before = self.monitor.view(previous)["hourly_days"]
                after = self.monitor.view(current)["hourly_days"]
                self.assertEqual([row["label"] for row in before[1:]],
                                 [row["label"] for row in after[:-1]])
                self.assertEqual(after[0]["label"], (current - timedelta(days=6)).strftime("%a %d %b"))
                self.assertEqual(after[-1]["label"], current.strftime("%a %d %b"))

    def test_monthly_window_rolls_across_year_boundary(self) -> None:
        before = self.monitor.view(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc))["daily_rows"]
        after = self.monitor.view(datetime(2027, 1, 1, tzinfo=timezone.utc))["daily_rows"]
        self.assertEqual([row["month"] for row in before[1:]], [row["month"] for row in after[:-1]])
        self.assertEqual(after[0]["month"], "2026-02")
        self.assertEqual(after[-1]["label"], "Jan 2027")
        self.assertEqual(after[-1]["cells"][0]["state"], "failure")
        self.assertTrue(all(cell["state"] == "future" for cell in after[-1]["cells"][1:]))
        local_new_year = datetime(2027, 1, 1, 1, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(self.monitor.view(local_new_year)["daily_rows"][-1]["month"], "2026-12")

    def test_month_lengths_and_leap_days_use_blank_padding_not_future_cells(self) -> None:
        for year, month, days in ((2024, 2, 29), (2025, 2, 28), (2026, 4, 30), (2026, 12, 31)):
            with self.subTest(year=year, month=month):
                current = datetime(year, month, 15, 10, tzinfo=timezone.utc)
                row = self.monitor.view(current)["daily_rows"][-1]
                self.assertEqual(len(row["cells"]), 31)
                self.assertTrue(all(cell["state"] == "failure" for cell in row["cells"][:15]))
                self.assertTrue(all(cell["state"] == "future" for cell in row["cells"][15:days]))
                self.assertEqual(row["cells"][days:], [{"state": "empty", "label": ""}] * (31 - days))

    def test_previous_year_history_appears_in_rolling_months(self) -> None:
        day = datetime(2025, 12, 5, tzinfo=timezone.utc)
        for hour in range(24):
            self.monitor._save_record(self.target, day.replace(hour=hour), True, 200, "")
        self.monitor._save_record(self.target, self.now.replace(minute=0), True, 200, "")
        row = self.monitor.view(self.now)["daily_rows"][0]
        self.assertEqual(row["label"], "Dec 2025")
        self.assertEqual(row["cells"][4]["state"], "success")
        self.assertIn("2025", row["cells"][4]["label"])


if __name__ == "__main__":
    unittest.main()
