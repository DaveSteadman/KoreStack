# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test service logging module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: ServiceLoggingTests.
# Function inventory:
# - test_service_log_config_normalises_name_and_level: Implements the test service log config normalises name and level operation for this module.
# - test_service_log_config_uses_safe_defaults: Implements the test service log config uses safe defaults operation for this module.
# ====================================================================================================

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCommon.service_logging import make_service_log_config


class ServiceLoggingTests(unittest.TestCase):
    @patch("KoreCommon.service_logging.get_service_log_path", return_value=Path("C:/logs/korecode.log"))
    def test_service_log_config_normalises_name_and_level(self, _log_path) -> None:
        config = make_service_log_config(" KoreCode ", "warning")

        self.assertEqual(config["root"]["level"], "WARNING")
        self.assertEqual(config["handlers"]["file"]["filename"], "C:\\logs\\korecode.log")
        self.assertIn("[korecode]", config["formatters"]["default"]["format"])
        self.assertEqual(config["loggers"]["uvicorn"]["level"], "WARNING")

    @patch("KoreCommon.service_logging.get_service_log_path", return_value=Path("C:/logs/service.log"))
    def test_service_log_config_uses_safe_defaults(self, _log_path) -> None:
        config = make_service_log_config("", "")

        self.assertEqual(config["root"]["level"], "INFO")
        self.assertIn("[service]", config["formatters"]["default"]["format"])
