from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from KoreStack.main import SUITE_ROOT, ServiceSpec, StackManager


class DashboardSnapshotTests(unittest.TestCase):
    def test_start_skips_services_after_shutdown_is_requested(self) -> None:
        manager = object.__new__(StackManager)
        manager._services = [
            ServiceSpec(
                slug="example",
                label="Example",
                cwd=SUITE_ROOT,
                script="main.py",
                url="http://127.0.0.1:9999/",
                health_url="http://127.0.0.1:9999/status",
                description="Test service",
            )
        ]
        manager.start_service = Mock()  # type: ignore[method-assign]
        stop_event = threading.Event()
        stop_event.set()

        manager.start(stop_event)

        manager.start_service.assert_not_called()

    def test_snapshot_returns_initial_state_while_background_probe_is_pending(self) -> None:
        probe_started = threading.Event()
        release_probe = threading.Event()
        paths = {
            "path config": Path(tempfile.gettempdir()),
            "dataroot": Path(tempfile.gettempdir()),
            "datacontrol": Path(tempfile.gettempdir()),
            "datauser": Path(tempfile.gettempdir()),
        }
        service = ServiceSpec(
            slug="example",
            label="Example",
            cwd=SUITE_ROOT,
            script="main.py",
            url="http://127.0.0.1:9999/",
            health_url="http://127.0.0.1:9999/status",
            description="Test service",
        )

        def wait_for_probe() -> dict[str, object]:
            probe_started.set()
            release_probe.wait(timeout=5)
            return {"stack": {}, "services": []}

        with patch.object(StackManager, "_compute_snapshot", side_effect=wait_for_probe):
            manager = StackManager([service], {}, paths, Path(tempfile.gettempdir()))
            try:
                self.assertTrue(probe_started.wait(timeout=1))
                snapshot = manager.snapshot()
                self.assertEqual(snapshot["services"][0]["status"], "awaiting background probe")
            finally:
                release_probe.set()
                manager.stop()


if __name__ == "__main__":
    unittest.main()
