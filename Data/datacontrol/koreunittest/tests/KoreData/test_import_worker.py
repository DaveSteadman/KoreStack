# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests the independently managed KoreReference import worker.
# ====================================================================================================

import sys
import threading
import unittest
import os
from pathlib import Path
from unittest.mock import patch


SERVICE_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreData" / "KoreReference"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.importers import state  # noqa: E402


class ImportWorkerTests(unittest.TestCase):
    def test_stop_keeps_slot_busy_until_worker_returns(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        original_state = dict(state.import_state)
        original_stop = state.import_stop_event.is_set()

        def worker() -> None:
            entered.set()
            release.wait(timeout=3)

        try:
            self.assertTrue(state.start_import_worker(
                target=worker, args=(), name="stop-test", initial_state={},
            ))
            try:
                self.assertTrue(entered.wait(timeout=1))
                self.assertTrue(state.request_import_stop())
                self.assertTrue(state.import_stop_event.is_set())
                self.assertTrue(state.import_state["running"])
                self.assertEqual(state.import_state["phase"], "stopping")
                self.assertFalse(state.start_import_worker(
                    target=lambda: None, args=(), name="too-early", initial_state={},
                ))
            finally:
                release.set()
                self.assertTrue(state.import_lock.acquire(timeout=3))
                state.import_lock.release()
            self.assertFalse(state.import_state["running"])
            self.assertEqual(state.import_state["phase"], "stopped")
            self.assertFalse(state.request_import_stop())
            self.assertTrue(state.start_import_worker(
                target=lambda: None, args=(), name="after-stop", initial_state={},
            ))
            self.assertTrue(state.import_lock.acquire(timeout=3))
            state.import_lock.release()
            self.assertFalse(state.import_stop_event.is_set())
            self.assertEqual(state.import_state["phase"], "complete")
        finally:
            with state.state_lock:
                state.import_state.clear()
                state.import_state.update(original_state)
            if original_stop:
                state.import_stop_event.set()
            else:
                state.import_stop_event.clear()

    def test_worker_failure_releases_slot_and_reports_error(self) -> None:
        original_state = dict(state.import_state)
        original_stop = state.import_stop_event.is_set()

        def fail() -> None:
            raise RuntimeError("finalization failed")

        try:
            self.assertTrue(state.start_import_worker(
                target=fail, args=(), name="failure-test",
                initial_state={"errors": 0, "last_error": None},
            ))
            self.assertTrue(state.import_lock.acquire(timeout=3))
            state.import_lock.release()
            self.assertFalse(state.import_state["running"])
            self.assertEqual(state.import_state["phase"], "failed")
            self.assertEqual(state.import_state["errors"], 1)
            self.assertIn("finalization failed", state.import_state["last_error"])
            with patch.object(threading.Thread, "start", side_effect=RuntimeError("start failed")):
                with self.assertRaisesRegex(RuntimeError, "start failed"):
                    state.start_import_worker(
                        target=lambda: None, args=(), name="failed-start", initial_state={},
                    )
            self.assertFalse(state.import_lock.locked())
            self.assertFalse(state.import_state["running"])
            self.assertEqual(state.import_state["phase"], "failed")
            self.assertIn("start failed", state.import_state["last_error"])
        finally:
            with state.state_lock:
                state.import_state.clear()
                state.import_state.update(original_state)
            if original_stop:
                state.import_stop_event.set()
            else:
                state.import_stop_event.clear()

    def test_admission_lock_is_held_until_worker_finishes(self) -> None:
        entered  = threading.Event()
        release  = threading.Event()
        finished = threading.Event()

        def worker() -> None:
            entered.set()
            release.wait(timeout=2)
            finished.set()

        original_state = dict(state.import_state)
        try:
            self.assertTrue(state.start_import_worker(
                target        = worker,
                args          = (),
                name          = "test-reference-import",
                initial_state = {"done": 0, "errors": 0},
            ))
            self.assertTrue(entered.wait(timeout=1))
            self.assertFalse(state.start_import_worker(
                target        = worker,
                args          = (),
                name          = "second-test-reference-import",
                initial_state = {},
            ))

            release.set()
            self.assertTrue(finished.wait(timeout=1))
            for _ in range(100):
                if not state.import_lock.locked():
                    break
                threading.Event().wait(0.01)
            self.assertFalse(state.import_lock.locked())
        finally:
            release.set()
            with state.state_lock:
                state.import_state.clear()
                state.import_state.update(original_state)


if __name__ == "__main__":
    unittest.main()
