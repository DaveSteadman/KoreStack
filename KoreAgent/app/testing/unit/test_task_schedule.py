from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from input_layer.routes_tasks import _next_fire
from input_layer.routes_tasks import _task_at_slot


class TaskScheduleTests(unittest.TestCase):
    def test_interval_next_fire_uses_last_run_then_next_interval(self) -> None:
        now = datetime(2026, 7, 27, 10, 5, 42)

        next_fire = _next_fire(
            {"schedule": {"type": "interval", "minutes": 15}},
            datetime(2026, 7, 27, 10, 0, 31),
            now,
        )

        self.assertEqual(next_fire, "2026-07-27T10:15:00")

    def test_interval_without_last_run_starts_after_one_full_interval(self) -> None:
        now = datetime(2026, 7, 27, 10, 5, 42)

        next_fire = _next_fire({"schedule": {"type": "interval", "minutes": 30}}, None, now)

        self.assertEqual(next_fire, "2026-07-27T10:35:00")

    def test_daily_next_fire_rolls_to_tomorrow_after_today_time(self) -> None:
        now = datetime(2026, 7, 27, 10, 5, 42)

        next_fire = _next_fire({"schedule": {"type": "daily", "time": "09:30"}}, None, now)

        self.assertEqual(next_fire, "2026-07-28T09:30:00")

    def test_timeline_slot_uses_declared_task_order_for_a_collision(self) -> None:
        slot = datetime(2026, 7, 27, 10, 15)
        tasks = [
            {"name": "first",  "schedule": {"type": "daily", "time": "10:15"}},
            {"name": "second", "schedule": {"type": "daily", "time": "10:15"}},
        ]

        task_name = _task_at_slot(slot, datetime(2026, 7, 27, 10, 5), tasks, {})

        self.assertEqual(task_name, "first")

    def test_unscheduled_task_has_no_next_fire_or_timeline_slot(self) -> None:
        now = datetime(2026, 7, 27, 10, 5)
        task = {"name": "manual", "schedule": {"type": "manual"}}

        self.assertIsNone(_next_fire(task, None, now))
        self.assertIsNone(_task_at_slot(now, now, [task], {}))
