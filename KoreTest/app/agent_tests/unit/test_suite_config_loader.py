# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for suite config loader.
# Exercises the expected behaviour and regression boundaries for this area.
# MARK: FUNCTIONS
# Primary types: SuiteConfigLoaderTests.
# Function inventory:
# - test_load_service_config_reads_suite_config_and_env: Implements the test load service config reads suite config and env operation for this module.
# - test_load_service_config_applies_raw_merger: Implements the test load service config applies raw merger operation for this module.
# - merger: Implements the merger operation for this module.
# ====================================================================================================

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCommon.suite_config import load_service_config


class SuiteConfigLoaderTests(unittest.TestCase):
    def test_load_service_config_reads_suite_config_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            (cfg_dir / "korestack_config.json").write_text(
                json.dumps(
                    {
                        "network": {"host": "192.168.1.50"},
                        "services": {"korecode": {"port": 8619}},
                        "log_level": "warning",
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "KORE_SUITE_CONFIG": str(cfg_dir / "korestack_config.json"),
                    "KORECODE_PORT":       "9900",
                },
            ):
                loaded = load_service_config(
                    service_key="korecode",
                    defaults={"host": "127.0.0.1", "port": 5600, "log_level": "info"},
                    suite_root=root,
                    env_overrides={"port": "KORECODE_PORT"},
                )

            self.assertEqual(loaded["host"], "192.168.1.50")
            self.assertEqual(loaded["port"], 9900)
            self.assertEqual(loaded["log_level"], "warning")

    def test_load_service_config_applies_raw_merger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            (cfg_dir / "korestack_config.json").write_text(
                json.dumps({"connections": {"korechat": "http://host-b:8630"}}),
                encoding="utf-8",
            )

            def merger(result: dict, raw: dict) -> None:
                value = raw.get("connections", {}).get("korechat")
                if value is not None:
                    result["korechat_url"] = value

            with patch.dict(os.environ, {"KORE_SUITE_CONFIG": str(cfg_dir / "korestack_config.json")}):
                loaded = load_service_config(
                    service_key="korecomms",
                    defaults={"korechat_url": "http://localhost:8630"},
                    suite_root=root,
                    raw_merger=merger,
                )

            self.assertEqual(loaded["korechat_url"], "http://host-b:8630")


if __name__ == "__main__":
    unittest.main()
