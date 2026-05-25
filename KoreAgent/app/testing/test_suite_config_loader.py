import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreCommon.suite_config import load_service_config


class SuiteConfigLoaderTests(unittest.TestCase):
    def test_load_service_config_merges_default_local_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            (cfg_dir / "default.json").write_text(
                json.dumps(
                    {
                        "network": {"host": "10.0.0.1"},
                        "services": {"korecode": {"port": 8610}},
                        "log_level": "warning",
                    }
                ),
                encoding="utf-8",
            )
            (cfg_dir / "local.json").write_text(
                json.dumps(
                    {
                        "network": {"host": "192.168.1.50"},
                        "services": {"korecode": {"port": 8619}},
                    }
                ),
                encoding="utf-8",
            )

            os.environ["KORECODE_PORT"] = "9900"
            try:
                loaded = load_service_config(
                    service_key="korecode",
                    defaults={"host": "127.0.0.1", "port": 5600, "log_level": "info"},
                    suite_root=root,
                    env_overrides={"port": "KORECODE_PORT"},
                )
            finally:
                os.environ.pop("KORECODE_PORT", None)

            self.assertEqual(loaded["host"], "192.168.1.50")
            self.assertEqual(loaded["port"], 9900)
            self.assertEqual(loaded["log_level"], "warning")

    def test_load_service_config_applies_raw_merger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_dir = root / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)

            (cfg_dir / "default.json").write_text(
                json.dumps({"connections": {"korechat": "http://host-a:8630"}}),
                encoding="utf-8",
            )
            (cfg_dir / "local.json").write_text(
                json.dumps({"connections": {"korechat": "http://host-b:8630"}}),
                encoding="utf-8",
            )

            def merger(result: dict, raw: dict) -> None:
                value = raw.get("connections", {}).get("korechat")
                if value is not None:
                    result["korechat_url"] = value

            loaded = load_service_config(
                service_key="korecomms",
                defaults={"korechat_url": "http://localhost:8630"},
                suite_root=root,
                raw_merger=merger,
            )

            self.assertEqual(loaded["korechat_url"], "http://host-b:8630")


if __name__ == "__main__":
    unittest.main()
