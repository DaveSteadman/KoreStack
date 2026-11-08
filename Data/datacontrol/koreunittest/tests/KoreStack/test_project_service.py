from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from KoreStack.main import (
    SERVICE_ICON_KEYS, STACK_STATIC_DIR, SUITE_CONFIG_FILE, UI_ELEMENTS_ASSETS,
    StackManager, build_services, resolve_services,
)
from KoreStack import main as stack_main

# Use the dashboard module actually imported by the supervisor.
dashboard = __import__(stack_main.serve_dashboard.__module__, fromlist=["build_handler"])


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(SUITE_CONFIG_FILE.read_text(encoding="utf-8"))
        self.services = build_services(self.config)
        self.project = self.services["koreproject"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        paths = {key: root for key in ("path config", "dataroot", "datacontrol", "datauser")}
        with patch.object(StackManager, "_refresh_loop"), patch.object(StackManager, "_watch_loop"):
            self.manager = StackManager([self.project], {}, paths, root)
        self.addCleanup(self.manager.stop)
        self.ollama = Mock()
        self.ollama.snapshot.return_value = {"server_running": False, "loaded_models": []}

    def test_default_selection_and_landing_panel(self) -> None:
        selected = resolve_services("all", self.services)
        self.assertIn(self.project, selected)
        self.assertEqual([self.project], resolve_services("koreproject", self.services))
        self.assertEqual("http://127.0.0.1:29619/ui", self.project.url)
        self.assertEqual("http://127.0.0.1:29619/status", self.project.health_url)

        page = dashboard.html_page(
            self.manager, "http://127.0.0.1:29600/", STACK_STATIC_DIR,
            UI_ELEMENTS_ASSETS, SERVICE_ICON_KEYS, self.ollama,
        )
        self.assertIn('data-service-card="koreproject"', page)
        self.assertIn('href="http://127.0.0.1:29619/ui"', page)
        self.assertIn('data-suite-icon="koreproject"', page)
        for action in ("Start", "Stop", "Restart"):
            self.assertIn(f'aria-label="{action} KoreProject"', page)
        self.assertIn(
            "koreproject: 'koreproject'",
            (STACK_STATIC_DIR / "stack" / "stack.js").read_text(encoding="utf-8"),
        )

    def test_disabled_project_is_not_selected(self) -> None:
        self.config["services"]["koreproject"]["enabled"] = False
        self.assertNotIn("koreproject", build_services(self.config))

    def test_suite_start_and_shutdown_manage_project_process(self) -> None:
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(stack_main, "port_accepts_tcp", return_value=False), \
                patch.object(stack_main.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(StackManager, "_stop_process_tree") as stop:
            self.manager.start()
            self.assertEqual([process], self.manager.get_live_processes())
            self.assertEqual(str(self.project.cwd), spawn.call_args.kwargs["cwd"])
            self.assertEqual("koreproject", spawn.call_args.kwargs["env"]["KORESTACK_SERVICE_NAME"])
            self.assertEqual("main.py", spawn.call_args.args[0][-1])
            self.manager.stop()
            stop.assert_called_once_with("koreproject", process)
            self.assertFalse(self.manager._log_handles)
            process.poll.return_value = 0

    def test_dashboard_start_stop_restart_routes(self) -> None:
        probe = Mock(return_value=(True, "HTTP 200"))
        handler = dashboard.build_handler(
            self.manager, "http://127.0.0.1:29600/",
            stack_static_dir=STACK_STATIC_DIR, ui_assets_dir=UI_ELEMENTS_ASSETS,
            service_icon_keys=SERVICE_ICON_KEYS, probe_http_with_retry=probe,
            suite_config=self.config, ollama_control=self.ollama,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def spawn(*args, **kwargs):
            process = Mock(pid=12345)
            process.poll.return_value = None
            return process

        def stop(slug, process):
            self.assertEqual("koreproject", slug)
            process.poll.return_value = 0

        try:
            with patch.object(stack_main, "port_accepts_tcp", return_value=False), \
                    patch.object(stack_main.subprocess, "Popen", side_effect=spawn) as start_process, \
                    patch.object(StackManager, "_stop_process_tree", side_effect=stop) as stop_process:
                for action in ("start", "restart", "stop"):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_port}/api/services/koreproject/{action}",
                        data=b"", method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        result = json.load(response)
                    self.assertTrue(result["ok"])
                    self.assertTrue(result["changed"])
                    self.assertEqual(action, result["action"])
                    self.assertEqual("koreproject", result["service"])
                self.assertEqual(2, start_process.call_count)
                self.assertEqual(2, stop_process.call_count)
                self.assertFalse(self.manager.get_live_processes())
                probe.assert_called_with(self.project.health_url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
