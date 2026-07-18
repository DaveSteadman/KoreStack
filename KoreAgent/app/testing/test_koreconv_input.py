from __future__ import annotations

import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from input_layer.koreconv_input import _latest_message


class KoreConvInputTests(unittest.TestCase):
    def test_latest_message_uses_timestamp_instead_of_response_list_position(self) -> None:
        messages = [
            {"id": 175, "direction": "outbound", "created_at": "2026-07-18T17:25:44+00:00"},
            {"id": 179, "direction": "inbound",  "created_at": "2026-07-18T19:13:28+00:00"},
        ]

        latest = _latest_message(messages)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], 179)
        self.assertEqual(latest["direction"], "inbound")


if __name__ == "__main__":
    unittest.main()
