from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from KoreCommon.datauser_script_runner import DataUserScriptError
from KoreCommon.datauser_script_runner import resolve_datauser_script
from KoreCommon.datauser_script_runner import run_datauser_script


class DataUserScriptRunnerTests(unittest.TestCase):
    def test_runs_a_python_script_below_datauser_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            datauser = Path(temporary_directory)
            script   = datauser / "scripts" / "echo.py"
            script.parent.mkdir()
            script.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n", encoding="utf-8")

            result = run_datauser_script("./scripts/echo.py alpha beta", datauser_dir=datauser)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, "alpha beta")

    def test_rejects_a_script_outside_datauser_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            datauser = Path(temporary_directory)
            outside  = datauser / "outside.py"
            outside.write_text("print('no')\n", encoding="utf-8")

            with self.assertRaisesRegex(DataUserScriptError, "datauser/scripts"):
                resolve_datauser_script("./outside.py", datauser_dir=datauser)


if __name__ == "__main__":
    unittest.main()
