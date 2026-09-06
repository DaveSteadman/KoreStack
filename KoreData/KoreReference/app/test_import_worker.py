# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests the independently managed KoreReference import worker.
# ====================================================================================================

import sys
import threading
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.importers import state  # noqa: E402


class ImportWorkerTests(unittest.TestCase):
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
